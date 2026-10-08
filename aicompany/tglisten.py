"""Agent Telegram akkaunti bot o'rnida: faqat egasining chat(lar)idan kelgan xabarga javob beradi.

Boshqa har qanday odam yoki guruh yozsa, javob berilmaydi (xabar shunchaki o'qilmay qoladi).
Egasi ID'si: panelda kiritilgan ro'yxat (kv `tg_owner_ids`) yoki .env dagi OWNER_TELEGRAM_ID.
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
from pathlib import Path

log = logging.getLogger("aicompany.tglisten")
MAX_FILE = 10_000_000
STATUS_UZ = {"done": "tayyor", "cancelled": "to'xtatildi", "limit": "limit tugadi", "failed": "xato",
             "interrupted": "uzildi", "paused": "pauzada"}


def parse_ids(raw: str) -> list[int]:
    """'123, 456' -> [123, 456]. Noto'g'ri qism bo'lsa ValueError."""
    out = []
    for part in re.split(r"[,\s]+", raw or ""):
        if not part:
            continue
        if not part.lstrip("-").isdigit():
            raise ValueError(f"'{part}' raqamli ID emas")
        out.append(int(part))
    return out


class TgListener:
    def __init__(self, app):
        self.app = app
        self._client = None       # handler ulangan mijoz (qayta kirilsa yangisiga ko'chamiz)
        self._handler = None
        self._wake = asyncio.Event()
        self._running: set[asyncio.Task] = set()
        self.last_error = ""

    # ---------- sozlamalar ----------
    async def owner_ids(self) -> list[int]:
        raw = await self.app.store.get_kv("tg_owner_ids")
        if raw:
            try:
                return parse_ids(raw)
            except ValueError:
                pass
        return [self.app.settings.owner_id] if self.app.settings.owner_id else []

    async def enabled(self) -> bool:
        return (await self.app.store.get_kv("tg_listen")) != "0"

    def active(self) -> bool:
        return self._client is not None

    def kick(self):
        """Sozlama yoki ulanish o'zgardi: kuzatuvchini darhol uyg'otamiz."""
        self._wake.set()

    # ---------- ishga tushirish ----------
    async def attach(self) -> bool:
        tg = self.app.tg
        want = tg is not None and tg.configured() and await self.enabled() and bool(await self.owner_ids())
        if not want:
            self.detach()
            return False
        client = await tg.client()
        if client is self._client:
            return True
        self.detach()
        from telethon import events
        handler = self._on_event
        client.add_event_handler(handler, events.NewMessage(incoming=True))
        await client.get_me()  # Telegram yangiliklarni shu mijozga yubora boshlaydi
        self._client, self._handler = client, handler
        log.info("Telegram agent akkaunti egasining xabarlarini tinglayapti")
        return True

    def detach(self):
        if self._client is not None and self._handler is not None:
            try:
                self._client.remove_event_handler(self._handler)
            except Exception:  # noqa: BLE001
                pass
        self._client = self._handler = None

    async def supervise(self, interval: float = 30):
        """Doimiy: ulanish uzilsa yoki panelda qayta kirilsa, tinglashni qayta yoqadi."""
        while True:
            try:
                if self._client is not None and not self._client.is_connected():
                    self.detach()
                    await self.app.tg.close()
                await self.attach()
                self.last_error = ""
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — tarmoq xatosi kuzatuvni to'xtatmasin
                self.last_error = str(e)[:200]
                log.warning("Telegram tinglash: %s", e)
                self.detach()
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), interval)
            except asyncio.TimeoutError:
                pass

    # ---------- xabarlar ----------
    async def _on_event(self, event):
        t = asyncio.create_task(self.handle_event(event))
        self._running.add(t)
        t.add_done_callback(self._running.discard)

    async def handle_event(self, event):
        if not getattr(event, "is_private", False) or event.sender_id not in await self.owner_ids():
            return  # begona odam yoki guruh: javob bermaymiz
        chat_id = event.chat_id
        msg = event.message
        try:
            text = (getattr(msg, "message", "") or "").strip()
            attachments = None
            if getattr(msg, "voice", None) or getattr(msg, "audio", None):
                text = await self._voice(event, msg)
                if text is None:
                    return
            elif getattr(msg, "document", None) or getattr(msg, "photo", None):
                path = await self._download(event, msg)
                if path is None:
                    return
                attachments = [path]
                text = text or "Ilova qilingan fayl bilan ishlang."
            if not text:
                return
            await self.run(chat_id, text, attachments)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — egasi javobsiz qolmasin
            log.exception("Telegram xabarni qayta ishlashda xato")
            await self._send(chat_id, f"❌ Xatolik: {str(e)[:300]}")

    async def _voice(self, event, msg):
        from .providers import VoiceError, VoiceUnavailable
        size = getattr(getattr(msg, "file", None), "size", 0) or 0
        if size > MAX_FILE:
            await self._send(event.chat_id, "Ovoz fayli juda katta (10MB limit)")
            return None
        data = await event.download_media(file=bytes)
        mime = getattr(getattr(msg, "file", None), "mime_type", None) or "audio/ogg"
        try:
            text = await self.app.router.transcribe(data, mime)
        except VoiceUnavailable as e:
            await self._send(event.chat_id, f"🎤 {e}. Matn yozing yoki .env ga Gemini kalitini qo'shing.")
            return None
        except VoiceError as e:
            await self._send(event.chat_id, f"🎤 {e}")
            return None
        await self._send(event.chat_id, f"🎤 Eshitdim: {text}")
        return text

    async def _download(self, event, msg):
        f = getattr(msg, "file", None)
        if (getattr(f, "size", 0) or 0) > MAX_FILE:
            await self._send(event.chat_id, "Fayl juda katta (10MB limit)")
            return None
        inbox = self.app.settings.workspace_dir / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        name = getattr(f, "name", None) or f"file{getattr(f, 'ext', '') or ''}"
        safe = re.sub(r"[^\w.\- ]", "_", Path(name).name)[:100] or "file"
        dest = inbox / f"tg{msg.id}_{safe}"
        dest.write_bytes(await event.download_media(file=bytes))
        return dest

    async def _send(self, chat_id, text: str):
        client = await self.app.tg.client()
        text = text or "…"
        for i in range(0, len(text), 3900):
            await client.send_message(chat_id, text[i:i + 3900])

    async def run(self, chat_id: int, text: str, attachments=None):
        async def notify(s: str):
            await self._send(chat_id, s)
        res = await self.app.orch.handle(text, chat_id, notify, attachments)
        if res["kind"] == "chat":
            return
        head = f"🏁 Vazifa #{res['task_id']} — {STATUS_UZ.get(res['status'], res['status'])}"
        if res.get("error"):
            head += f"\n{res['error']}"
        await self._send(chat_id, head)
        client = await self.app.tg.client()
        result = res.get("result") or ""
        if len(result) <= 3500:
            if result:
                await self._send(chat_id, result)
        else:
            await self._send(chat_id, result[:600] + "\n\n… (to'liq natija faylda)")
            buf = io.BytesIO(result.encode())
            buf.name = f"task_{res['task_id']}.md"
            await client.send_file(chat_id, buf)
        ws = self.app.settings.workspace_dir / f"task_{res['task_id']}"
        files = res.get("files", [])
        for rel in files[:8]:
            f = ws / rel
            try:
                if f.stat().st_size <= MAX_FILE:
                    await client.send_file(chat_id, str(f), caption=f"📎 {rel}")
            except Exception:  # noqa: BLE001 — bitta fayl yuborilmasa qolganlari yuborilsin
                log.exception("fayl yuborilmadi: %s", rel)
        if len(files) > 8:
            await self._send(chat_id, f"… yana {len(files) - 8} ta fayl panelda")
