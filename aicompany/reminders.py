"""Eslatmalar: vaqtni tushunish va vaqti kelganda egasiga yuborish."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger("reminders")
UNITS = {"s": 1, "sek": 1, "m": 60, "min": 60, "daq": 60, "h": 3600, "soat": 3600, "d": 86400, "kun": 86400}


def parse_when(text: str, tz: str, now: datetime | None = None) -> datetime:
    """'2026-10-09 09:00', '09:00', 'ertaga 9:00', 'bugun 18:30', '+30m', '2 soat', '1d' -> UTC vaqt.
    Mahalliy vaqt sozlamadagi vaqt zonasida tushuniladi. O'tgan vaqt yoki tushunarsiz matn: ValueError."""
    zone = ZoneInfo(tz)
    now_local = (now or datetime.now(timezone.utc)).astimezone(zone)
    t = (text or "").strip().lower().replace("t", " ", 1) if re.match(r"\d{4}-\d\d-\d\dt", (text or "").strip().lower()) else (text or "").strip().lower()
    m = re.fullmatch(r"\+?\s*(\d+(?:[.,]\d+)?)\s*(s|sek|m|min|daq|h|soat|d|kun)\w*(\s+(keyin|dan keyin))?", t)
    if m:
        due = now_local + timedelta(seconds=float(m[1].replace(",", ".")) * UNITS[m[2]])
    else:
        m = re.fullmatch(r"(\d{4})-(\d\d)-(\d\d)[ ]+(\d{1,2})[:.](\d\d)", t)
        if m:
            due = datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]), tzinfo=zone)
        else:
            m = re.fullmatch(r"(ertaga|bugun|today|tomorrow)?\s*(?:soat\s*)?(\d{1,2})[:.](\d\d)", t)
            if not m:
                raise ValueError("vaqtni tushunmadim (masalan: 09:00, ertaga 9:00, 2026-10-09 18:30, 30 daq)")
            day = now_local.date() + timedelta(days=1 if m[1] in ("ertaga", "tomorrow") else 0)
            due = datetime(day.year, day.month, day.day, int(m[2]), int(m[3]), tzinfo=zone)
            if m[1] is None and due <= now_local:  # faqat soat aytilgan va bugun o'tib ketgan: ertaga
                due += timedelta(days=1)
    if due <= now_local:
        raise ValueError("bu vaqt allaqachon o'tgan")
    if due - now_local > timedelta(days=366):
        raise ValueError("bir yildan uzoq eslatma qo'yib bo'lmaydi")
    return due.astimezone(timezone.utc)


def local_text(due_iso: str, tz: str) -> str:
    return datetime.fromisoformat(due_iso).astimezone(ZoneInfo(tz)).strftime("%d.%m %H:%M")


async def create(store, settings, chat_id: int, text: str, when: str) -> dict:
    text = (text or "").strip()
    if not text or len(text) > 500:
        raise ValueError("eslatma matni 1-500 belgi bo'lsin")
    due = parse_when(when, settings.report_tz)
    rid = await store.add_reminder(chat_id, text, due.isoformat())
    return {"id": rid, "text": text, "due_at": due.isoformat(), "local": local_text(due.isoformat(), settings.report_tz)}


async def deliver_due(app, senders) -> int:
    """Vaqti kelgan eslatmalarni yuboradi. Kamida bitta kanal yetkazsa 'sent' bo'ladi."""
    n = 0
    for r in await app.store.due_reminders(datetime.now(timezone.utc).isoformat()):
        ok = False
        for send in senders:
            try:
                await send(f"⏰ Eslatma: {r['text']}")
                ok = True
            except Exception:  # noqa: BLE001 — bitta kanal ishlamasa boshqasi yetkazadi
                log.exception("eslatma yuborilmadi")
        await app.store.mark_reminder(r["id"], "sent" if ok else "failed")
        n += ok
    return n


async def reminder_loop(app, senders, interval: float = 20):
    while True:
        try:
            await deliver_due(app, senders)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — sikl to'xtamasligi kerak
            log.exception("eslatma sikli xatosi")
        await asyncio.sleep(interval)
