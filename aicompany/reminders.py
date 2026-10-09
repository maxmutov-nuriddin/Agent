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


async def create(store, settings, chat_id: int, text: str, when: str, call: bool = False) -> dict:
    text = (text or "").strip()
    if not text or len(text) > 500:
        raise ValueError("eslatma matni 1-500 belgi bo'lsin")
    due = parse_when(when, settings.report_tz)
    dup = await store.similar_pending_reminder(chat_id, text, due.isoformat())
    if dup:  # xuddi shu vaqt va mazmun: yangisini ochmaymiz, mavjudini yangilaymiz (masalan «telefon qilib ham eslat»)
        if call:
            await store.set_kv(f"rcall:{dup['id']}", "1")
        return {"id": dup["id"], "text": dup["text"], "due_at": due.isoformat(), "local": local_text(due.isoformat(), settings.report_tz),
                "call": call or bool(await store.get_kv(f"rcall:{dup['id']}")), "merged": True}
    rid = await store.add_reminder(chat_id, text, due.isoformat())
    if call:
        await store.set_kv(f"rcall:{rid}", "1")  # vaqti kelganda egasiga qo'ng'iroq ham qilinadi
    return {"id": rid, "text": text, "due_at": due.isoformat(), "local": local_text(due.isoformat(), settings.report_tz), "call": call}


_tasks: set = set()


def _bg(coro):
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


async def phone_reminder(app, text: str, senders, retry_after: float = 120):
    """Eslatmani qo'ng'iroq bilan yetkazadi: ko'tarilmasa 2 daqiqadan keyin yana bir marta. Bo'lmasa sababi yoziladi."""
    orch = app.orch
    reason = ""
    for attempt in range(2):
        if not (getattr(orch, "call_owner", None) and orch.call_ready()):
            reason = "qo'ng'iroq moduli tayyor emas (Hisob → Telegram akkaunt → Ovozli qo'ng'iroq)"
            break
        try:
            if await orch.call_owner(f"Eslatma. {text}"):
                return True
        except Exception as e:  # noqa: BLE001
            log.exception("eslatma qo'ng'irog'i")
            reason = str(e)[:150]
        calls = getattr(app, "calls", None)
        reason = (getattr(calls, "last_error", "") or reason or "javob bo'lmadi")[:200]
        if attempt == 0:
            await asyncio.sleep(retry_after)
    for send in senders:
        try:
            await send(f"📞 Eslatma uchun qo'ng'iroq qila olmadim: {reason}. Eslatma: {text}")
        except Exception:  # noqa: BLE001
            log.exception("qo'ng'iroq xatosi haqida xabar")
    return False


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
        if await app.store.get_kv(f"rcall:{r['id']}"):
            await app.store.delete_kv(f"rcall:{r['id']}")
            _bg(phone_reminder(app, r["text"], senders))
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


# ---------- "menga qo'ng'iroq qil": modelga ishonmay, qoida bilan aniqlash ----------
_PHONE = re.compile(
    r"\btel\b|\btel\.|\btlf\b|\btelf\w*|telefon\w*|(?:q|k)[o']{0,2}ng'?ir[oa]?[qk']?\w*|zvon\w*|dozvon\w*|"
    r"звон\w*|позвон\w*|созвон\w*|\bтел\b|телефон\w*|набер\w*|[қк][ўу]?[нғ]+[ғг]?[иi]р[оа]қ\w*|call\s*me|ring\s*me|\bdial\b", re.I)
# "bog'lan", "chaqir" faqat o'ziga qaratilganda: "menga bog'lan", "meni chaqir" ("marketologni chaqir" — qo'ng'iroq emas)
_CONTACT = re.compile(r"\b(?:menga|manga|meni|mani|men\s+bilan|man\s+bilan)\b.{0,25}?\b(?:bog'?lan(?:ing|gin)?|aloqaga\s+chiq(?:ing|gin)?|"
                      r"chaqir(?:ing|gin)?)\b", re.I)
# "qil" ning turli ko'rinishlari ("ql", "qlb", "qilb", "qiling", ...) va o'xshash fe'llar ("ur", "chaqir", "bog'lan", "et")
_ASK = re.compile(
    r"\bq(?:i)?l(?:i)?(?:b|ib|ing|gin|sang|vor|ay)?\b|\b[қк]ил(?:иб|инг|гин)?\b|\bқл\b|\bur(?:ib|ing|gin)?\b|\bet(?:ing|gin)?\b|"
    r"\bulan\b|zvon\w*|звони\w*|позвони\w*|набери\w*|call\s*me|ring\s*me|\bdial\b|"
    r"(?:q|k)[o']{0,2}ng'?ir\w*\s+ber\w*|zvon\w*\s+ber\w*", re.I)
_NOT_REQUEST = re.compile(
    r"\?|olasan|oladimi|mumkinmi|bormi|qila ol|qilolasan|qiladimi|nega|nima uchun|qanday|nimaga|raqam|nomer|номер|batareya|zaryad|"
    r"narxi|sotib|modeli|sozla|ishlamay|ishlamaydi|buzil|qilgan|qilgandi|qildim|qildi\b|qilyapman|qilyapsan|qilishni|qilmoq|qilish\b", re.I)
_NOW = re.compile(r"\b(hozir|hali|darrov|darhol|tezda|zudlik|сейчас|now)\b", re.I)
_WORDNUM = {"bir": 1, "ikki": 2, "uch": 3, "to'rt": 4, "besh": 5, "olti": 6, "yetti": 7, "sakkiz": 8, "to'qqiz": 9, "o'n": 10,
            "yigirma": 20, "o'ttiz": 30, "yarim": 0.5}
_MONTHS = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr"]
_TIMEISH = re.compile(r"ertaga|indinga|bugun|kechqurun|kechki|kechasi|ertalab|peshin|tushdan|\bsoat\b|\d[:.]\d\d|" + "|".join(_MONTHS)
                      + r"|dushanba|seshanba|chorshanba|payshanba|juma|shanba|yakshanba|haftadan|oydan keyin|keyin", re.I)


def _norm(text: str) -> str:
    """Kichik harf va barcha turdagi tutuq belgilar (o', o\u02bb, o\u2018, o\u2019, o`) bitta ko'rinishga."""
    t = (text or "").lower()
    for ch in ("\u2019", "\u2018", "\u02bb", "\u02bc", "`", "\u00b4"):
        t = t.replace(ch, "'")
    return t


def call_requested(text: str) -> bool:
    """Egasi o'ziga qo'ng'iroq qilishni so'rayaptimi (savol/izoh emas)?"""
    t = _norm(text)
    if _NOT_REQUEST.search(t):
        return False
    return bool((_PHONE.search(t) and _ASK.search(t)) or _CONTACT.search(t))


def call_when(text: str, tz: str, now: datetime | None = None) -> tuple[str, str]:
    """('now', '') hozir | ('at', when) aniq vaqt (parse_when formatida) | ('ask', '') vaqt tushunarsiz: so'rash kerak."""
    zone = ZoneInfo(tz)
    now_local = (now or datetime.now(timezone.utc)).astimezone(zone)
    t = _norm(text)
    num = r"\b(\d+(?:[.,]\d+)?|" + "|".join(map(re.escape, _WORDNUM)) + r")"

    def val(x):
        return float(x.replace(",", ".")) if x[0].isdigit() else _WORDNUM[x]
    m = re.search(num + r"\s*(?:ta\s+)?(daqiqa|daq|minut|min|sekund|sek)\w*", t)
    if m:
        secs = val(m[1]) * (1 if m[2].startswith("sek") else 60)
        return "at", f"{int(secs)} s"
    m = re.search(num + r"\s*soat\w*\s+(?:dan\s+)?keyin", t) or re.search(r"(yarim|bir)\s+soat\w*", t)
    if m:
        return "at", f"{int(val(m[1]) * 60)} daq"
    if _NOW.search(t) and not re.search(r"soat\s*\d|\d[:.]\d\d", t):
        return "now", ""
    day = now_local.date()
    shifted = False
    if re.search(r"indinga|ertadan keyin|porertaga", t):
        day, shifted = day + timedelta(days=2), True
    elif re.search(r"ertaga", t):
        day, shifted = day + timedelta(days=1), True
    elif re.search(r"bugun", t):
        shifted = True
    m = re.search(r"\b(\d{1,2})[- ]?(" + "|".join(_MONTHS) + r")", t)
    if m:
        mon = _MONTHS.index(m[2]) + 1
        try:
            day = day.replace(month=mon, day=int(m[1]))
            if day < now_local.date():
                day = day.replace(year=day.year + 1)
            shifted = True
        except ValueError:
            return "ask", ""
    tm = re.search(r"soat\s*(\d{1,2})(?:[:.](\d\d))?|\b(\d{1,2})[:.](\d\d)\b|\b(\d{1,2})\s*(?:da|ga|dan)\b", t)
    if tm:
        hr = int(tm[1] or tm[3] or tm[5])
        mi = int(tm[2] or tm[4] or 0)
        if hr < 12 and re.search(r"kechqurun|kechki|kechasi|tushdan keyin|peshindan keyin|\bkech\b", t):
            hr += 12
        if hr > 23 or mi > 59:
            return "ask", ""
        if shifted:
            return "at", f"{day.year}-{day.month:02d}-{day.day:02d} {hr:02d}:{mi:02d}"
        return "at", f"{hr:02d}:{mi:02d}"
    if m:   # sana bor, soat yo'q: ertalab 9:00
        return "at", f"{day.year}-{day.month:02d}-{day.day:02d} 09:00"
    return ("ask", "") if _TIMEISH.search(t) else ("now", "")
