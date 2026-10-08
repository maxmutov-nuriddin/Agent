"""Agent Telegram akkaunti orqali ovozli qo'ng'iroq (ixtiyoriy, py-tgcalls kerak).

- Egasi qo'ng'iroq qilsa, agent ko'taradi: gapni eshitadi (ovoz -> matn), javobni ovoz bilan aytadi.
- Vazifa tugaganda (panelda yoqilsa) agent egasiga o'zi qo'ng'iroq qilib natijani aytadi.
- Faqat egasi (tg_owner_ids / OWNER_TELEGRAM_ID) bilan: boshqalarning qo'ng'irog'i ko'tarilmaydi.
Ovoz: kirish 16 kHz mono PCM (energiya bo'yicha gap bo'laklarga ajratiladi), chiqish Gemini TTS 24 kHz PCM.
"""
from __future__ import annotations

import array
import asyncio
import io
import logging
import re
import time
import wave
from datetime import datetime, timedelta, timezone

log = logging.getLogger("aicompany.calls")

IN_RATE = 16000          # eshitish: 16 kHz mono s16le
OUT_RATE = 24000         # aytish: Gemini TTS 24 kHz mono s16le
FRAME_MS = 10
IDLE_HANGUP = 75         # soniya: shuncha jim turilsa qo'ng'iroq tugatiladi
CALL_COOLDOWN = 120      # soniya: ketma-ket avtomatik qo'ng'iroqlar orasida


def pcm_to_wav(pcm: bytes, rate: int = IN_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def rms(pcm: bytes) -> float:
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) // 2 * 2])
    return (sum(x * x for x in a) / len(a)) ** 0.5 if a else 0.0


class Segmenter:
    """Doimiy PCM oqimidan gap bo'laklarini ajratadi: ovoz boshlanadi, keyin `silence` soniya jimlik -> bo'lak tayyor."""

    def __init__(self, rate: int = IN_RATE, threshold: float = 500, silence: float = 0.9,
                 min_speech: float = 0.35, max_len: float = 25):
        self.rate, self.threshold, self.silence = rate, threshold, silence
        self.min_speech, self.max_len = min_speech, max_len
        self._reset()

    def _reset(self):
        self.buf = bytearray()
        self.speech = 0.0
        self.quiet = 0.0
        self.active = False

    def feed(self, pcm: bytes) -> list[bytes]:
        out = []
        dur = len(pcm) / 2 / self.rate
        if not dur:
            return out
        loud = rms(pcm) >= self.threshold
        if loud:
            self.active = True
            self.speech += dur
            self.quiet = 0.0
        elif self.active:
            self.quiet += dur
        if self.active:
            self.buf += pcm
            done = self.quiet >= self.silence or len(self.buf) / 2 / self.rate >= self.max_len
            if done:
                if self.speech >= self.min_speech:
                    out.append(bytes(self.buf))
                self._reset()
        return out


def spoken_text(text: str, limit: int = 600) -> str:
    """Natijani ovozda aytishga qulay qisqa matnga aylantiradi (markdown, havola, kod olib tashlanadi)."""
    t = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[#*_`>|~]+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= limit:
        return t
    cut = t[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[:end + 1] if end > limit // 2 else cut.rsplit(" ", 1)[0]) + " Batafsil natija chatda."


REPORT_WORDS = re.compile(r"hisobot|otchet|отчет|отчёт|report|nima qilindi|bugun nima", re.I)
HANGUP_WORDS = re.compile(r"^(xayr|hayr|rahmat,? xayr|bo'?ldi|tamom|пока|до свидания|bye|goodbye)\b", re.I)


class Call:
    """Bitta faol qo'ng'iroq holati."""

    def __init__(self, chat_id: int):
        self.chat_id = chat_id
        self.seg = Segmenter()
        self.queue: asyncio.Queue = asyncio.Queue()
        self.worker: asyncio.Task | None = None
        self.last_activity = time.monotonic()
        self.speaking = False
        self.closed = False


class CallService:
    def __init__(self, app):
        self.app = app
        self._client = None
        self._tgc = None
        self.call: Call | None = None
        self._last_auto = 0.0
        self._bg: set[asyncio.Task] = set()
        self.last_error = ""

    # ---------- sozlamalar / holat ----------
    @staticmethod
    def available() -> bool:
        try:
            import pytgcalls  # noqa: F401
            return True
        except Exception:  # noqa: BLE001 — kutubxona yo'q yoki mos emas
            return False

    async def enabled(self) -> bool:
        return (await self.app.store.get_kv("tg_calls")) != "0"

    async def notify_on(self) -> bool:
        return (await self.app.store.get_kv("tg_call_notify")) == "1"

    async def status(self) -> dict:
        return {"available": self.available(), "enabled": await self.enabled(), "notify": await self.notify_on(),
                "ready": self._tgc is not None, "in_call": self.call is not None, "error": self.last_error}

    def _spawn(self, coro):
        t = asyncio.create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)
        return t

    # ---------- ulanish ----------
    async def attach(self, client) -> bool:
        """Telethon mijozi tayyor bo'lganda chaqiriladi (TgListener). Kutubxona yo'q bo'lsa jim o'tadi."""
        if client is self._client and self._tgc is not None:
            return True
        await self.detach()
        if not await self.enabled():
            return False
        try:
            from pytgcalls import PyTgCalls, filters
            from pytgcalls.types import ChatUpdate, Device, Direction
        except Exception as e:  # noqa: BLE001
            self.last_error = f"py-tgcalls o'rnatilmagan ({type(e).__name__})"
            return False
        try:
            tgc = PyTgCalls(client)
        except Exception as e:  # noqa: BLE001 — mijoz turi mos emas
            self.last_error = f"qo'ng'iroq moduli mijozni qabul qilmadi ({type(e).__name__})"
            return False

        @tgc.on_update(filters.chat_update(ChatUpdate.Status.INCOMING_CALL))
        async def _incoming(_, update):
            self._spawn(self._on_incoming(update.chat_id))

        @tgc.on_update(filters.chat_update(ChatUpdate.Status.LEFT_CALL))
        async def _left(_, update):
            await self._cleanup(update.chat_id)

        @tgc.on_update(filters.stream_frame(Direction.INCOMING, Device.MICROPHONE))
        async def _frames(_, update):
            c = self.call
            if c is None or c.chat_id != update.chat_id or c.speaking:
                return
            for fr in update.frames:
                for utt in c.seg.feed(fr.frame):
                    c.last_activity = time.monotonic()
                    c.queue.put_nowait(utt)

        try:
            await tgc.start()
        except Exception as e:  # noqa: BLE001
            self.last_error = f"qo'ng'iroq modulini ishga tushirib bo'lmadi: {e}"[:200]
            log.warning(self.last_error)
            return False
        self._client, self._tgc, self.last_error = client, tgc, ""
        log.info("Telegram qo'ng'iroq moduli tayyor")
        return True

    async def detach(self):
        if self.call:
            await self._cleanup(self.call.chat_id)
        self._tgc = self._client = None

    # ---------- ovoz: kirish/chiqish ----------
    def _stream(self):
        from pytgcalls.types import ExternalMedia, MediaStream
        from pytgcalls.types.raw import AudioParameters
        return MediaStream(ExternalMedia.AUDIO, audio_parameters=AudioParameters(OUT_RATE, 1),
                           audio_flags=MediaStream.Flags.REQUIRED, video_flags=MediaStream.Flags.IGNORE)

    async def _listen(self, chat_id: int):
        from pytgcalls.types import RecordStream
        from pytgcalls.types.raw import AudioParameters
        await self._tgc.record(chat_id, RecordStream(True, AudioParameters(IN_RATE, 1)))

    async def say(self, call: Call, text: str):
        """Matnni ovozga aylantirib, qo'ng'iroqda aytadi (aytayotganda eshitmaydi: o'zini eshitib qolmasin)."""
        from pytgcalls.types import Device
        text = (text or "").strip()
        if not text or call.closed:
            return
        call.speaking = True
        try:
            pcm = await self.app.router.speak(text)
            step = OUT_RATE * 2 * FRAME_MS // 1000
            t0 = time.monotonic()
            for i, off in enumerate(range(0, len(pcm), step)):
                if call.closed:
                    return
                await self._tgc.send_frame(call.chat_id, Device.MICROPHONE, pcm[off:off + step])
                wait = t0 + (i + 1) * FRAME_MS / 1000 - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
            await asyncio.sleep(0.3)
        finally:
            call.speaking = False
            call.seg = Segmenter()
            call.last_activity = time.monotonic()

    # ---------- qo'ng'iroq hayoti ----------
    async def _owner_ids(self) -> list[int]:
        lst = getattr(self.app, "listener", None)
        return await lst.owner_ids() if lst else []

    async def _on_incoming(self, chat_id: int):
        if chat_id not in await self._owner_ids() or not await self.enabled():
            return  # begona odam: ko'tarilmaydi
        if self.call is not None:
            return
        try:
            call = await self._open(chat_id, None)
            await self.say(call, "Salom, eshitaman.")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self.last_error = f"qo'ng'iroqni ko'tarib bo'lmadi: {e}"[:200]
            log.warning(self.last_error)
            await self._cleanup(chat_id)

    async def _open(self, chat_id: int, config) -> Call:
        call = Call(chat_id)
        self.call = call
        await self._tgc.play(chat_id, self._stream(), config)
        await self._listen(chat_id)
        call.worker = asyncio.create_task(self._loop(call))
        return call

    async def _cleanup(self, chat_id: int):
        c = self.call
        if c is None or c.chat_id != chat_id:
            return
        c.closed = True
        self.call = None
        if c.worker:
            c.worker.cancel()
        try:
            await self._tgc.leave_call(chat_id)
        except Exception:  # noqa: BLE001 — allaqachon tugagan
            pass

    async def hangup(self):
        if self.call:
            await self._cleanup(self.call.chat_id)

    async def _loop(self, call: Call):
        """Eshitilgan gaplarni ketma-ket qayta ishlaydi; uzoq jim turilsa qo'ng'iroqni tugatadi."""
        try:
            while not call.closed:
                try:
                    utt = await asyncio.wait_for(call.queue.get(), 5)
                except asyncio.TimeoutError:
                    if not call.speaking and time.monotonic() - call.last_activity > IDLE_HANGUP:
                        await self.say(call, "Xayr, kerak bo'lsa qo'ng'iroq qiling.")
                        break
                    continue
                if not await self._turn(call, utt):
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)[:200]
            log.exception("qo'ng'iroq sikli xatosi")
        finally:
            await self._cleanup(call.chat_id)

    async def _turn(self, call: Call, utt: bytes) -> bool:
        """Bitta gap: eshit -> tushun -> ayt. False qaytsa qo'ng'iroq tugaydi."""
        from .providers import VoiceError, VoiceUnavailable
        try:
            text = await self.app.router.transcribe(pcm_to_wav(utt), "audio/wav")
        except VoiceUnavailable as e:
            await self.say(call, str(e))
            return False
        except VoiceError:
            await self.say(call, "Tushunmadim, qaytaring.")
            return True
        log.info("qo'ng'iroq: %s", text[:80])
        if HANGUP_WORDS.match(text.strip()):
            await self.say(call, "Xayr!")
            return False
        if REPORT_WORDS.search(text):
            await self.say(call, await self.spoken_report())
            return True
        first = asyncio.Event()
        said: list[str] = []

        async def notify(s):
            if not said:
                said.append(str(s))
                first.set()
        job = asyncio.create_task(self.app.orch.handle(text, call.chat_id, notify))
        waiter = asyncio.create_task(first.wait())
        await asyncio.wait({job, waiter}, return_when=asyncio.FIRST_COMPLETED)
        waiter.cancel()
        if said:
            await self.say(call, spoken_text(said[0], 400))
        if job.done():
            await self._finish(call, job, spoken=bool(said))
        else:
            self._spawn(self._finish_later(call, job, bool(said)))
        return True

    async def _finish_later(self, call: Call, job: asyncio.Task, spoken: bool):
        try:
            await job
        except Exception:  # noqa: BLE001
            pass
        await self._finish(call, job, spoken)

    async def _finish(self, call: Call, job: asyncio.Task, spoken: bool):
        """Vazifa/javob tayyor: ovozda qisqa aytiladi, to'liq natija Telegram chatiga yuboriladi."""
        try:
            res = job.result()
        except asyncio.CancelledError:
            return
        except Exception as e:  # noqa: BLE001
            if not call.closed:
                await self.say(call, "Xatolik bo'ldi: " + str(e)[:120])
            return
        if res.get("kind") == "chat":
            if not spoken and not call.closed:
                await self.say(call, spoken_text(res.get("reply", ""), 500))
            return
        short = spoken_text(res.get("result") or res.get("error") or "", 500)
        if not call.closed:
            await self.say(call, f"Vazifa {res['task_id']} tayyor. {short}")
        await self._send_text(call.chat_id, f"🏁 Vazifa #{res['task_id']}\n{res.get('result') or res.get('error') or ''}")

    async def _send_text(self, chat_id: int, text: str):
        try:
            client = await self.app.tg.client()
            for i in range(0, len(text), 3900):
                await client.send_message(chat_id, text[i:i + 3900])
        except Exception:  # noqa: BLE001
            log.exception("natija chatga yuborilmadi")

    async def spoken_report(self) -> str:
        since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        tasks = await self.app.store.tasks_since(since)
        done = sum(1 for t in tasks if t["status"] == "done")
        bad = sum(1 for t in tasks if t["status"] in ("failed", "interrupted", "limit"))
        spent = await self.app.store.spent_since(since)
        s = f"So'nggi yigirma to'rt soatda {len(tasks)} ta vazifa: {done} tasi tayyor"
        s += f", {bad} tasi bajarilmadi. " if bad else ". "
        return s + f"Sarf {spent:.2f} dollar."

    # ---------- chiqish: agent qo'ng'iroq qiladi ----------
    async def call_owner(self, text: str, *, listen: bool = True) -> bool:
        """Egasiga qo'ng'iroq qilib `text`ni aytadi. Ko'tarilmasa False (natija baribir chatda bo'ladi)."""
        if self._tgc is None or self.call is not None:
            return False
        ids = await self._owner_ids()
        if not ids:
            return False
        from pytgcalls.types import CallConfig
        chat_id = ids[0]
        try:
            call = await self._open(chat_id, CallConfig(timeout=40))
            await self.say(call, text)
            if not listen:
                await self._cleanup(chat_id)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — ko'tarmadi, band yoki rad etdi
            self.last_error = f"qo'ng'iroq qilib bo'lmadi: {e}"[:200]
            log.info(self.last_error)
            await self._cleanup(chat_id)
            return False

    async def task_done(self, res: dict):
        """Orchestrator vazifa tugaganda chaqiradi: yoqilgan bo'lsa, egasiga qo'ng'iroq qiladi."""
        if res.get("kind") == "chat" or res.get("status") not in ("done", "failed", "limit"):
            return
        if not (self._tgc and await self.notify_on() and await self.enabled()):
            return
        if time.monotonic() - self._last_auto < CALL_COOLDOWN or self.call is not None:
            return
        self._last_auto = time.monotonic()
        body = spoken_text(res.get("result") or res.get("error") or "", 500)
        head = "Vazifa tayyor." if res.get("status") == "done" else "Vazifa bajarilmadi."
        await self.call_owner(f"Assalomu alaykum. {head} Raqami {res.get('task_id')}. {body}")
