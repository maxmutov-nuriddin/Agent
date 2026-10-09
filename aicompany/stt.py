"""Ovozni matnga aylantirish zanjiri (speech-to-text).

Avto rejim: Google Cloud STT → Microsoft Azure Speech → Groq Whisper (bepul, Sozlamalarda yoqilsa) → Whisper (serverda, bepul) → Gemini.
- Bepul limitlar hisoblanadi (oy bo'yicha daqiqa): limit tugashiga 1 daqiqa qolsa, keyingisiga o'tiladi.
- Natijaning ishonch darajasi past bo'lsa (gap tushunilmagan bo'lishi mumkin), keyingi xizmat, oxirida Gemini
  (u ovozdagi ma'noni ham tushunadi). Hech biri ishonchli bo'lmasa, eng yaxshi natija qaytariladi.
Qo'lda rejim: Sozlamalarda bitta xizmat tanlanadi (u ishlamasa, baribir zanjir bo'yicha davom etadi).
Kalitlar .env da: GOOGLE_STT_KEY, AZURE_SPEECH_KEY + AZURE_SPEECH_REGION. Whisper: faster-whisper o'rnatilgan bo'lsa.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import math
import os
import tempfile
import wave
from datetime import datetime, timezone

import httpx

log = logging.getLogger("aicompany.stt")
ORDER = ("google", "azure", "groq", "whisper", "gemini")
NAMES = {"google": "Google Cloud", "azure": "Microsoft Azure", "groq": "Groq Whisper", "whisper": "Whisper (serverda)", "gemini": "Gemini"}
MIN_CONF = 0.6          # shundan past ishonch: "tushunilmagan bo'lishi mumkin", keyingi xizmatga
CHUNK_S = 55            # Google/Azure qisqa so'rov limiti ~60 soniya: bo'laklab yuboriladi
RATE = 16000


def free_minutes(name: str) -> float | None:
    """Oylik bepul limit (daqiqa). None: cheklanmagan (o'zimizniki yoki pullik hisob)."""
    env = {"google": ("STT_GOOGLE_FREE_MIN", 60), "azure": ("STT_AZURE_FREE_MIN", 300)}.get(name)
    if not env:
        return None
    try:
        return float(os.environ.get(env[0], env[1]))
    except ValueError:
        return float(env[1])


def configured(name: str) -> bool:
    if name == "google":
        return bool(os.environ.get("GOOGLE_STT_KEY"))
    if name == "azure":
        return bool(os.environ.get("AZURE_SPEECH_KEY") and os.environ.get("AZURE_SPEECH_REGION"))
    if name == "groq":
        return bool(os.environ.get("GROQ_API_KEY"))
    if name == "whisper":
        try:
            import faster_whisper  # noqa: F401
            return os.environ.get("WHISPER_MODEL", "small") != "off"
        except Exception:  # noqa: BLE001
            return False
    return True  # gemini: router o'zi tekshiradi


async def to_wav16k(audio: bytes, mime: str) -> bytes:
    """Har qanday ovozni (ogg/opus, webm, mp4/aac, wav) 16 kHz mono WAV ga aylantiradi (ffmpeg)."""
    if mime.startswith("audio/wav") or mime == "audio/x-wav":
        try:
            with wave.open(io.BytesIO(audio)) as w:
                if w.getframerate() == RATE and w.getnchannels() == 1 and w.getsampwidth() == 2:
                    return audio
        except wave.Error:
            pass
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(RATE), "-f", "wav", "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate(audio)
    if proc.returncode != 0 or not out:
        raise RuntimeError(f"ovozni o'girib bo'lmadi: {err.decode(errors='ignore')[:150]}")
    return out


def wav_frames(wav: bytes) -> bytes:
    with wave.open(io.BytesIO(wav)) as w:
        return w.readframes(w.getnframes())


def frames_to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def chunks(wav: bytes, seconds: int = CHUNK_S) -> list[bytes]:
    pcm = wav_frames(wav)
    step = RATE * 2 * seconds
    return [frames_to_wav(pcm[i:i + step]) for i in range(0, len(pcm), step)] or [wav]


def duration_s(wav: bytes) -> float:
    return len(wav_frames(wav)) / 2 / RATE


# ---------- xizmatlar: (matn, ishonch 0..1) ----------
async def google(wav: bytes) -> tuple[str, float]:
    key = os.environ["GOOGLE_STT_KEY"]
    texts, confs = [], []
    async with httpx.AsyncClient(timeout=40) as c:
        for part in chunks(wav):
            r = await c.post(f"https://speech.googleapis.com/v1/speech:recognize?key={key}", json={
                "config": {"encoding": "LINEAR16", "sampleRateHertz": RATE, "languageCode": "uz-UZ",
                           "alternativeLanguageCodes": ["ru-RU"], "enableAutomaticPunctuation": True},
                "audio": {"content": base64.b64encode(part).decode()}})
            if r.status_code >= 400:
                raise RuntimeError(f"Google STT: HTTP {r.status_code} {r.text[:150]}")
            for res in r.json().get("results", []):
                alt = (res.get("alternatives") or [{}])[0]
                if alt.get("transcript"):
                    texts.append(alt["transcript"].strip())
                    confs.append(float(alt.get("confidence", 0.0)))
    return " ".join(texts).strip(), (sum(confs) / len(confs) if confs else 0.0)


async def azure(wav: bytes) -> tuple[str, float]:
    key, region = os.environ["AZURE_SPEECH_KEY"], os.environ["AZURE_SPEECH_REGION"]
    url = f"https://{region}.stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1"
    texts, confs = [], []
    async with httpx.AsyncClient(timeout=40) as c:
        for part in chunks(wav):
            r = await c.post(url, params={"language": "uz-UZ", "format": "detailed"}, content=part, headers={
                "Ocp-Apim-Subscription-Key": key, "Content-Type": f"audio/wav; codecs=audio/pcm; samplerate={RATE}"})
            if r.status_code >= 400:
                raise RuntimeError(f"Azure: HTTP {r.status_code} {r.text[:150]}")
            d = r.json()
            if d.get("RecognitionStatus") != "Success":
                continue
            best = (d.get("NBest") or [{}])[0]
            text = best.get("Display") or d.get("DisplayText") or ""
            if text:
                texts.append(text.strip())
                confs.append(float(best.get("Confidence", 0.0)))
    return " ".join(texts).strip(), (sum(confs) / len(confs) if confs else 0.0)


async def groq(wav: bytes) -> tuple[str, float]:
    """Groq Whisper (bepul tarif, limit bilan). Ishonch: segmentlarning avg_logprob'idan."""
    key = os.environ["GROQ_API_KEY"]
    model = os.environ.get("GROQ_STT_MODEL", "whisper-large-v3")
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post("https://api.groq.com/openai/v1/audio/transcriptions", headers={"Authorization": f"Bearer {key}"},
                         files={"file": ("audio.wav", wav, "audio/wav")},
                         data={"model": model, "language": "uz", "response_format": "verbose_json", "temperature": "0"})
    if r.status_code >= 400:
        raise RuntimeError(f"Groq STT: HTTP {r.status_code} {r.text[:150]}")
    d = r.json()
    segs = d.get("segments") or []
    text = (d.get("text") or "").strip()
    if not text:
        return "", 0.0
    if not segs:
        return text, 0.7
    lp = sum(float(x.get("avg_logprob", -1.0)) for x in segs) / len(segs)
    return text, max(0.0, min(1.0, math.exp(lp)))


_whisper_model = None


def _whisper_sync(path: str) -> tuple[str, float]:
    global _whisper_model
    from faster_whisper import WhisperModel
    if _whisper_model is None:
        _whisper_model = WhisperModel(os.environ.get("WHISPER_MODEL", "small"), device="cpu", compute_type="int8")
    segs, _info = _whisper_model.transcribe(path, language="uz", vad_filter=True, beam_size=1)
    segs = list(segs)
    text = " ".join(s.text.strip() for s in segs).strip()
    if not segs:
        return "", 0.0
    lp = sum(s.avg_logprob for s in segs) / len(segs)
    return text, max(0.0, min(1.0, math.exp(lp)))


async def whisper(wav: bytes) -> tuple[str, float]:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as f:
        f.write(wav)
        f.flush()
        return await asyncio.to_thread(_whisper_sync, f.name)


SERVICES = {"google": google, "azure": azure, "groq": groq, "whisper": whisper}


class SpeechChain:
    def __init__(self, router):
        self.router = router
        self.store = router.store

    async def mode(self) -> str:
        m = await self.store.get_kv("stt_mode")
        return m if m in ("auto", *ORDER) else "auto"

    async def groq_on(self) -> bool:
        return await self.store.get_kv("groq_on") == "1"

    def _month(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    async def used_min(self, name: str) -> float:
        try:
            return float(await self.store.get_kv(f"stt_used:{name}:{self._month()}") or 0) / 60
        except ValueError:
            return 0.0

    async def _add_used(self, name: str, seconds: float):
        key = f"stt_used:{name}:{self._month()}"
        try:
            cur = float(await self.store.get_kv(key) or 0)
        except ValueError:
            cur = 0.0
        await self.store.set_kv(key, str(round(cur + seconds, 1)))

    async def status(self) -> dict:
        out = {}
        for n in ORDER:
            lim = free_minutes(n)
            ready = configured(n) if n != "gemini" else self.router.has_audio()
            if n == "groq":
                ready = ready and await self.groq_on()   # Sozlamalarda "Groq" yoqilgan bo'lsagina
            out[n] = {"name": NAMES[n], "ready": ready,
                      "used_min": round(await self.used_min(n), 1), "free_min": lim}
        return {"mode": await self.mode(), "services": out}

    async def order(self) -> list[str]:
        m = await self.mode()
        return list(ORDER) if m == "auto" else [m] + [n for n in ORDER if n != m]

    async def transcribe(self, audio: bytes, mime: str, gemini) -> str:
        """gemini: async (audio, mime) -> matn (eski yo'l, oxirgi zaxira)."""
        from .providers import VoiceError, VoiceUnavailable
        wav = None
        best = ("", 0.0, "")
        errors = []
        for name in await self.order():
            if name == "gemini":
                if not self.router.has_audio():
                    continue
                try:
                    return await gemini(audio, mime)
                except VoiceError as e:
                    errors.append(f"gemini: {e}")
                    continue
            if not configured(name) or (name == "groq" and not await self.groq_on()):
                continue
            try:
                wav = wav or await to_wav16k(audio, mime)
            except Exception as e:  # noqa: BLE001 — o'girib bo'lmasa, faqat Gemini qoladi
                errors.append(str(e))
                continue
            dur = duration_s(wav)
            lim = free_minutes(name)
            if lim is not None and (await self.used_min(name)) * 60 + dur > lim * 60 - 60:
                errors.append(f"{name}: bepul limit tugashiga oz qoldi")
                continue
            try:
                text, conf = await SERVICES[name](wav)
            except Exception as e:  # noqa: BLE001 — bitta xizmat xatosi zanjirni to'xtatmaydi
                errors.append(f"{name}: {e}")
                await self.store.audit("stt", "error", f"{name}: {e}"[:300])
                continue
            await self._add_used(name, dur)
            if text and conf >= MIN_CONF:
                return text
            if text and conf > best[1]:
                best = (text, conf, name)
        if best[0]:
            return best[0]   # hech biri ishonchli emas: eng yaxshisi
        if not errors and not self.router.has_audio():
            raise VoiceUnavailable("Ovozni tanish uchun kalit kerak (GEMINI_API_KEY yoki GOOGLE_STT_KEY / AZURE_SPEECH_KEY)")
        raise VoiceError("Ovozda gap topilmadi" if not errors else "Ovozni matnga aylantirib bo'lmadi (" + "; ".join(errors)[:200] + ")")
