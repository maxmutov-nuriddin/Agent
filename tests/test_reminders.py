import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from aicompany import reminders
from aicompany.reminders import deliver_due, parse_when
from aicompany.tools import TOOLS, ToolEnv, ToolError

from .conftest import scripted_company

TZ = "Asia/Tashkent"  # UTC+5
NOW = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)  # Toshkentda 15:00


def local(dt):
    return dt.astimezone(__import__("zoneinfo").ZoneInfo(TZ)).strftime("%Y-%m-%d %H:%M")


@pytest.mark.parametrize("text,expected", [
    ("18:30", "2026-10-08 18:30"),             # bugun, hali o'tmagan
    ("09:00", "2026-10-09 09:00"),             # bugun o'tib ketgan -> ertaga
    ("ertaga 9:00", "2026-10-09 09:00"),
    ("ertaga soat 7.15", "2026-10-09 07:15"),
    ("2026-10-10 08:05", "2026-10-10 08:05"),
    ("2026-10-10T08:05", "2026-10-10 08:05"),
    ("30 daq", "2026-10-08 15:30"),
    ("+2h", "2026-10-08 17:00"),
    ("1 kun", "2026-10-09 15:00"),
    ("90 min keyin", "2026-10-08 16:30"),
])
def test_parse_when_understands_common_forms(text, expected):
    assert local(parse_when(text, TZ, NOW)) == expected


@pytest.mark.parametrize("text", ["bugun 10:00", "2026-01-01 10:00", "keyinroq", "", "400 kun"])
def test_parse_when_rejects_past_or_unclear(text):
    with pytest.raises(ValueError):
        parse_when(text, TZ, NOW)


async def test_create_list_cancel_and_deliver(make_app):
    app, _ = await make_app(lambda *a: "ok")
    r = await reminders.create(app.store, app.settings, 1, "Aliga qo'ng'iroq", "30 daq")
    assert r["id"] == 1 and r["text"] == "Aliga qo'ng'iroq"
    with pytest.raises(ValueError):
        await reminders.create(app.store, app.settings, 1, "", "30 daq")
    assert [x["text"] for x in await app.store.list_reminders()] == ["Aliga qo'ng'iroq"]
    sent = []

    async def ok(t):
        sent.append(t)

    async def broken(t):
        raise ConnectionError("tarmoq")
    assert await deliver_due(app, [ok]) == 0                                       # hali vaqti emas
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    await app.store.add_reminder(1, "Dori ich", past)
    assert await deliver_due(app, [broken, ok]) == 1                               # bitta kanal ishlamasa ham yetadi
    assert sent == ["⏰ Eslatma: Dori ich"] and await deliver_due(app, [ok]) == 0   # ikki marta yuborilmaydi
    await app.store.add_reminder(1, "Hech kimga yetmaydi", past)
    assert await deliver_due(app, [broken]) == 0
    import sqlalchemy as sa
    st = {r["text"]: r["status"] for r in await app.store._all(sa.text("select text, status from reminders"))}
    assert st == {"Aliga qo'ng'iroq": "pending", "Dori ich": "sent", "Hech kimga yetmaydi": "failed"}
    assert await app.store.cancel_reminder(1) and not await app.store.cancel_reminder(1)
    assert await app.store.list_reminders() == []


async def test_reminder_tools(make_app, tmp_path):
    app, _ = await make_app(lambda *a: "ok", OWNER_TELEGRAM_ID="7")
    env = ToolEnv(workspace=tmp_path, store=app.store, settings=app.settings)
    out = await TOOLS["set_reminder"].handler(env, {"text": "Uchrashuv", "when": "2 soat"})
    assert out.startswith("eslatma #1 qo'yildi") and "Uchrashuv" in out
    assert "#1" in await TOOLS["list_reminders"].handler(env, {})
    with pytest.raises(ToolError, match="o'tgan"):
        await TOOLS["set_reminder"].handler(env, {"text": "x", "when": "2020-01-01 10:00"})
    assert "bekor" in await TOOLS["cancel_reminder"].handler(env, {"id": 1})
    with pytest.raises(ToolError):
        await TOOLS["cancel_reminder"].handler(env, {"id": 1})
    assert (await app.store._all(__import__("sqlalchemy").text("select chat_id from reminders")))[0]["chat_id"] == 7


def desk(decision):
    base = scripted_company()

    def handler(system, user, model):
        if "front desk" in system or "in a CHAT" in system:
            assert "# Now" in user                                                 # rahbar hozirgi vaqtni biladi
            return json.dumps(decision)
        return base(system, user, model)
    return handler


@pytest.mark.parametrize("allow_tasks", [True, False])
async def test_front_desk_sets_reminders_directly_without_a_task(make_app, allow_tasks):
    app, _ = await make_app(desk({"mode": "chat", "reply": "", "reminder": {"when": "2 soat", "text": "Aliga qo'ng'iroq"}}))
    r = await app.orch.handle("2 soatdan keyin Aliga qo'ng'iroqni eslat", 1, allow_tasks=allow_tasks)
    assert r["kind"] == "chat" and r["reply"].startswith("⏰ Eslatma qo'yildi") and "Aliga" in r["reply"]
    assert await app.store.list_tasks() == [] and len(await app.store.list_reminders()) == 1


async def test_front_desk_bad_reminder_time_is_explained(make_app):
    app, _ = await make_app(desk({"mode": "chat", "reply": "", "reminder": {"when": "qachondir", "text": "x"}}))
    r = await app.orch.handle("eslat", 1)
    assert "qo'ya olmadim" in r["reply"] and await app.store.list_reminders() == []


async def test_seed_agents_get_new_tool_groups_on_upgrade(make_app):
    app, _ = await make_app(lambda *a: "ok")
    await app.store.set_agent_tools("assistant", "maps,telegram")                 # eski versiyadagi holat
    await app.store.create_agent("my_hr_agent", "r", "p", "mid", "hr", "files")
    await app.team.ensure_seed()
    assert "time" in (await app.store.get_agent("assistant"))["tools"]
    assert (await app.store.get_agent("my_hr_agent"))["tools"] == "files"          # boshqalarga tegilmaydi
