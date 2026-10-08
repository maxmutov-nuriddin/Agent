import json
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.types import User, Update

from aicompany import bot as botmod
from aicompany.approvals import AutoApprover
from aicompany.providers import LLMResult
from aicompany.report import build_report, next_run

from .conftest import scripted_company


def plan_with(step_tier):
    return {"summary": "s", "new_roles": [], "steps": [
        {"id": "s1", "agent": "researcher", "task": "t", "tier": step_tier, "depends_on": []}]}


async def test_per_step_tier_is_respected(make_app):
    app, provs = await make_app(scripted_company(plan=plan_with("strong")))
    await app.orch.run_task("x", 1)
    assert "claude-opus-5-5" in provs["anthropic"].calls  # researcher odatda cheap, bu qadam strong


async def test_invalid_step_tier_falls_back_to_agent_default(make_app):
    app, provs = await make_app(scripted_company(plan=plan_with("ultra")))
    await app.orch.run_task("x", 1)
    assert "claude-opus-5-5" not in provs["anthropic"].calls


async def test_files_returned_and_attachments_copied(make_app, tmp_path):
    src = tmp_path / "brief.txt"
    src.write_text("hello")
    app, _ = await make_app(scripted_company())
    res = await app.orch.run_task("work on the brief", 1, attachments=[src])
    assert "brief.txt" in res["files"]


async def test_memory_is_learned_and_injected_into_plan(make_app):
    prompts = []
    base = scripted_company()

    def handler(system, user, model):
        if "'ceo'" in system and "Plan the work" in user:
            prompts.append(user)
        return base(system, user, model)
    app, _ = await make_app(handler)
    await app.orch.run_task("first task", 1)
    assert [m["text"] for m in await app.store.recent_memories()] == ["Owner prefers Uzbek"]
    await app.orch.run_task("Owner wants something Uzbek", 1)
    assert "Owner prefers Uzbek" in prompts[1] and "Owner prefers Uzbek" not in prompts[0]


async def test_hr_review_fires_only_idle_hr_hired_agents(make_app):
    app, _ = await make_app(scripted_company())
    await app.store.create_agent("idle_hr", "r", "p", "mid", "hr")
    await app.store.create_agent("idle_owner", "r", "p", "mid", "owner")
    await app.store.create_agent("busy_hr", "r", "p", "mid", "hr")
    for i in range(10):
        tid = await app.store.create_task(1, f"t{i}")
        await app.store.add_message(tid, "busy_hr", "work")
    # yangi yollangan xodim hali ishlash imkoniga ega bo'lmagan (cutoff'dan keyin) -> tegilmaydi
    await app.store.create_agent("newcomer", "r", "p", "mid", "hr")
    fired = await app.team.review(10)
    assert fired == ["idle_hr"]
    assert await app.store.get_agent("idle_owner") and await app.store.get_agent("busy_hr")
    assert await app.store.get_agent("newcomer") and await app.store.get_agent("ceo")


async def test_review_needs_enough_history(make_app):
    app, _ = await make_app(scripted_company())
    await app.store.create_agent("idle_hr", "r", "p", "mid", "hr")
    assert await app.team.review(10) == []


async def test_review_runs_automatically_every_10th_task(make_app):
    app, _ = await make_app(scripted_company())
    await app.store.create_agent("idle_hr", "r", "p", "mid", "hr")
    notes = []

    async def notify(s):
        notes.append(s)
    for _ in range(10):
        await app.orch.run_task("x", 1, notify)
    assert any("idle_hr" in n for n in notes) and not await app.store.get_agent("idle_hr")


async def test_report_contents_and_no_llm_cost(make_app):
    app, provs = await make_app(scripted_company())
    await app.orch.run_task("x", 1)
    calls = len(provs["anthropic"].calls)
    text = await build_report(app, datetime.now(timezone.utc) - timedelta(days=1), "Test")
    assert "done: 1" in text and "anthropic" in text and "Jamoa: 8" in text
    assert len(provs["anthropic"].calls) == calls


def test_next_run_schedule():
    t = datetime(2026, 10, 8, 10, 0)
    assert next_run(t, 9) == datetime(2026, 10, 9, 9, 0)
    assert next_run(datetime(2026, 10, 8, 8, 0), 9) == datetime(2026, 10, 8, 9, 0)


async def test_approval_center_approve_deny_timeout_and_announce():
    import asyncio
    from aicompany.approvals import ApprovalCenter
    seen = []

    async def announce(aid, task_id, agent, desc, kind):
        seen.append((aid, agent, desc))

    async def broken(*a):
        raise RuntimeError("kanal ishlamadi")
    c = ApprovalCenter(timeout=0.2)
    c.announcers += [broken, announce]  # buzilgan kanal boshqasini to'sib qo'ymaydi

    t = asyncio.create_task(c.ask(1, 5, "dev", "rm -rf build"))
    await asyncio.sleep(0.05)
    assert seen == [(1, "dev", "rm -rf build")] and c.list()[0]["description"] == "rm -rf build"
    assert c.resolve(1, True) and await t is True and c.list() == []

    t = asyncio.create_task(c.ask(2, 5, "dev", "ls"))
    await asyncio.sleep(0.05)
    assert c.resolve(2, False) and await t is False
    assert not c.resolve(2, True)  # allaqachon hal qilingan
    assert await c.ask(3, 5, "dev", "slow") is False and c.list() == []  # vaqt tugadi


async def test_telegram_callback_resolves_center_and_busy_state(make_app, monkeypatch):
    import asyncio
    from aiogram.types import CallbackQuery
    sent = []

    async def fake_call(self, method, request_timeout=None):
        sent.append(method)
        return True
    monkeypatch.setattr(Bot, "__call__", fake_call)
    app, _ = await make_app(scripted_company(), OWNER_TELEGRAM_ID="111")
    bot = Bot("123456:ABC")
    dp = botmod.make_dispatcher(app, bot)
    t = asyncio.create_task(app.center.ask(7, 1, "dev", "echo hi"))
    await asyncio.sleep(0.05)
    assert any(getattr(m, "reply_markup", None) for m in sent)  # tugmali xabar yuborildi
    cb = CallbackQuery(id="1", from_user=User(id=111, is_bot=False, first_name="x"), chat_instance="c", data="ap:7:y")
    await dp.feed_update(bot, Update(update_id=2, callback_query=cb))
    assert await t is True
    await bot.session.close()


async def test_busy_state_while_agent_runs(make_app):
    import asyncio
    gate = asyncio.Event()
    base = scripted_company()

    async def slow_wait():
        await gate.wait()
    app, _ = await make_app(base)
    orig = app.router.call

    async def slow_call(*a, **k):
        if k.get("agent") == "researcher":
            await gate.wait()
        return await orig(*a, **k)
    app.router.call = slow_call
    t = asyncio.create_task(app.orch.run_task("x", 1))
    for _ in range(50):
        await asyncio.sleep(0.02)
        if "researcher" in app.team.busy:
            break
    assert "researcher" in app.team.busy and app.team.busy["researcher"]["task_id"]
    gate.set()
    await t
    assert app.team.busy == {}


async def test_stale_running_tasks_are_marked_on_startup(make_app):
    app, _ = await make_app(scripted_company())
    tid = await app.store.create_task(1, "x")
    assert await app.store.fail_stale_tasks() == 1
    assert (await app.store.get_task(tid))["status"] == "interrupted"
