import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aicompany import briefing

from .conftest import scripted_company
from .test_web import TOK, get, post, web  # noqa: F401  (fixture)

W = {"now": 18.4, "min": 12.1, "max": 24.6, "rain": 10, "wind": 5, "code": 1, "text": "asosan ochiq", "icon": "🌤"}
R = {"USD": {"rate": 12650.45, "diff": -10.2, "date": "09.10.2026"}, "EUR": {"rate": 13800.0, "diff": 5.0, "date": "09.10.2026"},
     "RUB": {"rate": 150.1, "diff": 0.0, "date": "09.10.2026"}}


def fake_sources(monkeypatch, fail_weather=False):
    seen = {}

    async def weather(lat, lon, tz, client=None):
        seen["point"] = (lat, lon)
        if fail_weather:
            raise RuntimeError("tarmoq yo'q")
        return W

    async def rates(client=None):
        return R
    monkeypatch.setattr(briefing, "weather", weather)
    monkeypatch.setattr(briefing, "rates", rates)
    from aicompany import opendata

    async def no_news(env, store, limit=10, query=""):
        return [], {}
    monkeypatch.setattr(opendata, "latest_news", no_news)   # testda tarmoqqa chiqilmaydi
    return seen


async def test_briefing_text_uses_home_reminders_and_plans(make_app, monkeypatch):
    seen = fake_sources(monkeypatch)
    app, _ = await make_app(scripted_company())
    await app.store.set_kv("place:home", json.dumps({"lat": 41.2, "lon": 69.1, "label": "Uy"}))
    from aicompany import reminders
    await reminders.create(app.store, app.settings, 1, "Dori ichish", "+30m")
    await app.store.add_plan("day", "Bugun", json.dumps([{"text": "a", "done": False}, {"text": "b", "done": True}]))
    short, full = await briefing.build(app)
    assert seen["point"] == (41.2, 69.1)
    assert "Uy: asosan ochiq" in full and "12…25°" in full and "USD 12 650.45 ▼10.20" in full
    assert "Dori ichish" in full and "1 ta bajarilmagan" in full
    assert "$ 12 650" in short and "1 eslatma" in short


async def test_briefing_survives_one_source_failing(make_app, monkeypatch):
    fake_sources(monkeypatch, fail_weather=True)
    app, _ = await make_app(scripted_company())
    short, full = await briefing.build(app)
    assert "USD" in full and "ochiq" not in full


async def test_morning_loop_sends_once_per_day(make_app, monkeypatch):
    fake_sources(monkeypatch)
    app, _ = await make_app(scripted_company())
    now = datetime.now(ZoneInfo(app.settings.report_tz)) - timedelta(minutes=1)
    await app.store.set_kv("morning_time", now.strftime("%H:%M"))
    sent, pushed = [], []

    async def notify(kind, title, body="", url="/", **kw):
        pushed.append((kind, body))
        return 1
    app.push.notify = notify

    async def sender(t):
        sent.append(t)
    import asyncio
    task = asyncio.create_task(briefing.morning_loop(app, [sender], poll=0.01))
    await asyncio.sleep(0.2)
    task.cancel()
    assert len(sent) == 1 and pushed[0][0] == "morning"                  # bir kunda faqat bir marta
    await app.store.set_kv("morning", "0")
    await app.store.delete_kv("morning_sent")
    task = asyncio.create_task(briefing.morning_loop(app, [sender], poll=0.01))
    await asyncio.sleep(0.1)
    task.cancel()
    assert len(sent) == 1                                                 # o'chirilgan: yuborilmaydi


async def test_morning_settings_api(web, monkeypatch):
    fake_sources(monkeypatch)
    c, app = web
    m = (await get(c, "/api/state"))[1]["morning"]
    assert m["on"] is True and m["time"] == "08:00" and m["news"] is True and "kun.uz" in m["sites"]
    assert (await post(c, "/api/morning", {"time": "25:00"}))[0] == 400
    code, d = await post(c, "/api/morning", {"on": False, "time": "7:30"})
    assert code == 200 and d["on"] is False and d["time"] == "07:30"
    code, d = await post(c, "/api/morning/test")
    assert code == 200 and "Xayrli tong" in d["text"]


def test_weather_and_currency_tools_registered():
    from aicompany.tools import TOOLS
    assert TOOLS["weather"].group == "web" and TOOLS["currency_rates"].group == "web"
