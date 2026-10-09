"""Ertalabki xulosa: ob-havo (Open-Meteo, kalitsiz), valyuta kursi (O'zbekiston Markaziy banki), bugungi eslatma va rejalar.
LLM ishlatilmaydi: token sarflanmaydi. Har kuni belgilangan vaqtda (standart 08:00) push, panel va Telegramga yuboriladi."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger("aicompany.briefing")
TASHKENT = (41.3111, 69.2797, "Toshkent")
CBU_URL = "https://cbu.uz/uz/arkhiv-kursov-valyut/json/"
METEO_URL = "https://api.open-meteo.com/v1/forecast"
WMO = {0: "ochiq osmon", 1: "asosan ochiq", 2: "qisman bulutli", 3: "bulutli", 45: "tuman", 48: "qirov tumani",
       51: "mayda yomg'ir", 53: "mayda yomg'ir", 55: "kuchli mayda yomg'ir", 56: "muzli yomg'ir", 57: "muzli yomg'ir",
       61: "yomg'ir", 63: "yomg'ir", 65: "kuchli yomg'ir", 66: "muzli yomg'ir", 67: "muzli yomg'ir",
       71: "qor", 73: "qor", 75: "kuchli qor", 77: "qor donalari", 80: "jala", 81: "jala", 82: "kuchli jala",
       85: "qor yog'ishi", 86: "kuchli qor", 95: "momaqaldiroq", 96: "do'l bilan momaqaldiroq", 99: "do'l bilan momaqaldiroq"}
ICON = {0: "☀️", 1: "🌤", 2: "⛅️", 3: "☁️", 45: "🌫", 48: "🌫"}
CURRENCIES = ("USD", "EUR", "RUB")


def _icon(code: int) -> str:
    if code in ICON:
        return ICON[code]
    return "🌧" if code < 70 or 80 <= code <= 82 else "❄️" if code < 90 else "⛈"


async def _get(url: str, params=None, client: httpx.AsyncClient | None = None):
    own = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        r = await client.get(url, params=params, headers={"User-Agent": "AI-Jamoa/1.0"})
        r.raise_for_status()
        return r.json()
    finally:
        if own:
            await client.aclose()


async def owner_point(store) -> tuple[float, float, str]:
    """Uy (saqlangan joy) > oxirgi joylashuv > Toshkent."""
    raw = await store.get_kv("place:home")
    if raw:
        try:
            p = json.loads(raw)
            return float(p["lat"]), float(p["lon"]), p.get("label") or "uy"
        except (ValueError, KeyError, TypeError):
            pass
    raw = await store.get_kv("loc:last")
    if raw:
        try:
            p = json.loads(raw)
            return float(p["lat"]), float(p["lon"]), "joylashuvingiz"
        except (ValueError, KeyError, TypeError):
            pass
    return TASHKENT


async def weather(lat: float, lon: float, tz: str, client=None) -> dict:
    d = await _get(METEO_URL, {
        "latitude": lat, "longitude": lon, "timezone": tz, "forecast_days": 1,
        "current": "temperature_2m,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"}, client)
    cur, day = d.get("current") or {}, d.get("daily") or {}
    code = int((day.get("weather_code") or [cur.get("weather_code", 0)])[0] or 0)
    return {"now": cur.get("temperature_2m"), "min": (day.get("temperature_2m_min") or [None])[0],
            "max": (day.get("temperature_2m_max") or [None])[0], "rain": (day.get("precipitation_probability_max") or [None])[0],
            "wind": cur.get("wind_speed_10m"), "code": code, "text": WMO.get(code, "noma'lum"), "icon": _icon(code)}


async def rates(client=None) -> dict:
    rows = await _get(CBU_URL, client=client)
    out = {}
    for r in rows:
        if r.get("Ccy") in CURRENCIES:
            out[r["Ccy"]] = {"rate": float(r["Rate"]), "diff": float(r.get("Diff") or 0), "date": r.get("Date")}
    return out


def weather_text(w: dict, place: str) -> str:
    s = f"{w['icon']} {place}: {w['text']}, hozir {round(w['now'])}°" if w.get("now") is not None else f"{w['icon']} {place}: {w['text']}"
    if w.get("min") is not None and w.get("max") is not None:
        s += f", kunduzi {round(w['min'])}…{round(w['max'])}°"
    if w.get("rain"):
        s += f", yog'ingarchilik ehtimoli {w['rain']}%"
    return s


def rates_text(r: dict) -> str:
    parts = []
    for c in CURRENCIES:
        if c in r:
            diff = r[c]["diff"]
            arrow = "▲" if diff > 0 else "▼" if diff < 0 else "="
            parts.append(f"{c} {r[c]['rate']:,.2f} {arrow}{abs(diff):.2f}".replace(",", " "))
    return "💱 " + " · ".join(parts) + " so'm" if parts else ""


async def build(app) -> tuple[str, str]:
    """(qisqa push matni, to'liq xabar). Bitta manba ishlamasa ham qolganlari yuboriladi."""
    s = app.settings
    tz = ZoneInfo(s.report_tz)
    lat, lon, place = await owner_point(app.store)
    lines, short = [], []
    async with httpx.AsyncClient(timeout=15) as client:
        w, r = await asyncio.gather(weather(lat, lon, s.report_tz, client), rates(client), return_exceptions=True)
    if isinstance(w, dict):
        lines.append(weather_text(w, place))
        if w.get("min") is not None:
            short.append(f"{w['icon']} {round(w['min'])}…{round(w['max'])}° {w['text']}")
    else:
        log.warning("ob-havo olinmadi: %s", w)
    if isinstance(r, dict) and r:
        lines.append(rates_text(r))
        if "USD" in r:
            short.append(f"$ {r['USD']['rate']:,.0f}".replace(",", " "))
    else:
        log.warning("valyuta kursi olinmadi: %s", r)
    today_end = datetime.now(tz).replace(hour=23, minute=59, second=59).astimezone(timezone.utc).isoformat()
    rems = [x for x in await app.store.list_reminders(limit=20) if x["due_at"] <= today_end]
    if rems:
        from .reminders import local_text
        lines.append("⏰ Bugun: " + "; ".join(f"{local_text(x['due_at'], s.report_tz)[-5:]} {x['text']}" for x in rems[:5]))
        short.append(f"⏰ {len(rems)} eslatma")
    open_items = 0
    for p in await app.store.list_plans():
        if p["period"] in ("day", "week"):
            try:
                open_items += sum(1 for i in json.loads(p["items"] or "[]") if not i.get("done"))
            except ValueError:
                pass
    if open_items:
        lines.append(f"📋 Rejalaringizda {open_items} ta bajarilmagan band bor")
        short.append(f"📋 {open_items} band")
    if (await app.store.get_kv("morning_news")) != "0":
        try:
            from .opendata import latest_news, plain_env
            items, _ = await asyncio.wait_for(latest_news(plain_env(), app.store, 5), 25)
            if items:
                lines.append("📰 Yangiliklar:\n" + "\n".join(f"• {x['title']} ({x['source']})\n  {x['link']}" for x in items))
        except Exception as e:  # noqa: BLE001 — yangilik bo'lmasa ham xulosa yuboriladi
            log.warning("yangiliklar olinmadi: %s", type(e).__name__)
    full = "☀️ Xayrli tong!\n" + "\n".join(lines or ["Bugun uchun ma'lumot olinmadi."])
    return " · ".join(short) or "Xayrli tong!", full


async def settings_of(store) -> dict:
    from .opendata import news_sites
    return {"on": (await store.get_kv("morning")) != "0", "time": (await store.get_kv("morning_time")) or "08:00",
            "news": (await store.get_kv("morning_news")) != "0", "sites": await news_sites(store)}


def next_run(now_local: datetime, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    t = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
    return t if t > now_local else t + timedelta(days=1)


async def send(app, senders) -> str:
    short, full = await build(app)
    if getattr(app, "push", None):
        try:
            await app.push.notify("morning", "☀️ Xayrli tong", short, "/?tab=plans")
        except Exception:  # noqa: BLE001 — push bo'lmasa ham boshqa kanallar ishlaydi
            log.exception("ertalabki push")
    for fn in senders:
        try:
            await fn(full)
        except Exception:  # noqa: BLE001
            log.exception("ertalabki xulosa yuborilmadi")
    return full


async def morning_loop(app, senders, poll: float = 30):
    """Har kuni belgilangan vaqtda bir marta. Server o'sha paytda o'chiq bo'lsa, 3 soat ichida yoqilganda yuboradi."""
    tz = ZoneInfo(app.settings.report_tz)
    while True:
        try:
            cfg = await settings_of(app.store)
            now = datetime.now(tz)
            today = now.date().isoformat()
            h, m = (int(x) for x in cfg["time"].split(":"))
            due = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if cfg["on"] and due <= now <= due + timedelta(hours=3) and await app.store.get_kv("morning_sent") != today:
                await app.store.set_kv("morning_sent", today)
                await send(app, senders)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — sikl to'xtamasin
            log.exception("ertalabki xulosa sikli")
        await asyncio.sleep(poll)
