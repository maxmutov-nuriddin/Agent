import json
from datetime import datetime, timedelta, timezone

from aiogram import Bot

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
    assert "done: 1" in text and "anthropic" in text and "Jamoa: 7" in text
    assert len(provs["anthropic"].calls) == calls


def test_next_run_schedule():
    t = datetime(2026, 10, 8, 10, 0)
    assert next_run(t, 9) == datetime(2026, 10, 9, 9, 0)
    assert next_run(datetime(2026, 10, 8, 8, 0), 9) == datetime(2026, 10, 8, 9, 0)


async def test_telegram_approver_approve_deny_timeout(make_app, monkeypatch):
    sent = []

    async def fake_call(self, method, request_timeout=None):
        sent.append(method)
        return True
    monkeypatch.setattr(Bot, "__call__", fake_call)
    app, _ = await make_app(scripted_company())
    bot = Bot("123456:ABC")
    ap = botmod.TelegramApprover(bot, 111)

    import asyncio
    t = asyncio.create_task(ap.ask(1, "dev", "rm -rf build"))
    await asyncio.sleep(0.05)
    assert sent and ap.resolve(1, True) and await t is True

    t = asyncio.create_task(ap.ask(1, "dev", "ls"))
    await asyncio.sleep(0.05)
    assert ap.resolve(2, False) and await t is False
    assert not ap.resolve(2, True)  # allaqachon hal qilingan

    monkeypatch.setattr(botmod, "APPROVAL_TIMEOUT", 0.05)
    assert await ap.ask(1, "dev", "slow") is False
    await bot.session.close()
