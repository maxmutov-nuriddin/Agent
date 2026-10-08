"""Loyiha auditida topilgan xatolar uchun regressiya testlari."""
import asyncio
import json

import pytest

from aicompany.orchestrator import normalize_plan
from aicompany.router import BudgetExhausted, TaskBudgetExceeded
from aicompany.tools import ToolEnv

from aicompany.tguser import TgUser

from .conftest import scripted_company


async def test_busy_counter_does_not_leak_when_private_provider_check_fails(make_app, tmp_path):
    app, _ = await make_app(lambda *a: "ok", names=("gemini",), PRIVATE_PROVIDERS="anthropic", PRIVATE_MODE="strict", TG_API_ID="1", TG_API_HASH="h")
    env = ToolEnv(workspace=tmp_path, store=app.store, settings=app.settings, tg=TgUser(app.settings, client_factory=lambda: None))
    with pytest.raises(BudgetExhausted):
        await app.team.run_agent("assistant", "x", env=env)
    assert app.team.busy == {}   # oldin "assistant" abadiy band bo'lib qolardi


async def test_memory_failure_after_success_keeps_task_done(make_app, monkeypatch):
    app, _ = await make_app(scripted_company())

    async def boom(*a, **k):
        raise RuntimeError("disk to'ldi")
    monkeypatch.setattr(app.store, "add_memory", boom)
    res = await app.orch.run_task("x", 1)
    assert res["status"] == "done" and (await app.store.get_task(res["task_id"]))["status"] == "done"


async def test_memory_odd_json_shape_is_ignored(make_app):
    base = scripted_company()

    def handler(system, user, model):
        if "durable facts" in system:
            return json.dumps({"facts": "bitta matn, ro'yxat emas"})
        return base(system, user, model)
    app, _ = await make_app(handler)
    assert (await app.orch.run_task("x", 1))["status"] == "done"
    assert await app.store.recent_memories() == []


async def test_broken_notifications_do_not_fail_the_task(make_app):
    app, _ = await make_app(scripted_company())

    async def notify(_):
        raise ConnectionError("Telegram javob bermayapti")
    res = await app.orch.run_task("x", 1, notify)
    assert res["status"] == "done"


def test_normalize_plan_repairs_model_mistakes():
    plan = normalize_plan({"summary": None, "steps": [
        {"id": "s1", "agent": "researcher", "task": "a", "depends_on": "s0", "tier": "ultra"},
        "bu qadam emas",
        {"id": "s1", "agent": "marketer", "task": "b", "depends_on": ["s1", 5, {"x": 1}]},
        {"agent": "developer", "task": "c"},
        {"id": "s9", "agent": "", "task": "bo'sh agent"}],
        "new_roles": ["matn", {"why": "nomsiz"}, {"name": "seo", "why": "kerak"}]})
    assert [s["id"] for s in plan["steps"]] == ["s1", "s1_", "s4"]          # takroriy id tuzatildi
    assert plan["steps"][0]["depends_on"] == ["s0"] and plan["steps"][0]["tier"] is None
    assert plan["steps"][1]["depends_on"] == ["s1", "5"]
    assert plan["new_roles"] == [{"name": "seo", "why": "kerak"}] and plan["summary"] == ""


async def test_plan_with_string_dependencies_runs_in_order(make_app):
    plan = {"summary": "s", "steps": [{"id": "a", "agent": "researcher", "task": "t1"},
                                      {"id": "b", "agent": "marketer", "task": "t2", "depends_on": "a"}]}
    app, _ = await make_app(scripted_company(plan=plan))
    res = await app.orch.run_task("x", 1)
    agents = [m["agent"] for m in await app.store.task_messages(res["task_id"])]
    assert res["status"] == "done" and agents.index("researcher") < agents.index("marketer")


async def test_failing_parallel_step_cancels_its_siblings(make_app):
    plan = {"summary": "s", "steps": [{"id": "a", "agent": "researcher", "task": "t1"},
                                      {"id": "b", "agent": "marketer", "task": "t2"}]}
    app, _ = await make_app(scripted_company(plan=plan))
    orig, state = app.router.call, {"cancelled": False}

    async def call(*a, **k):
        if k.get("agent") == "researcher":
            await asyncio.sleep(0.05)
            raise TaskBudgetExceeded("limit")
        if k.get("agent") == "marketer":
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise
        return await orig(*a, **k)
    app.router.call = call
    res = await asyncio.wait_for(app.orch.run_task("x", 1), 3)
    assert res["status"] == "limit" and state["cancelled"] is True and app.team.busy == {}


def test_clean_inbox_removes_only_old(tmp_path):
    import os
    import time
    from aicompany.app import clean_inbox
    inbox = tmp_path / "inbox"
    (inbox / "old").mkdir(parents=True)
    (inbox / "old" / "a.txt").write_text("x")
    (inbox / "new").mkdir()
    (inbox / "1_f.txt").write_text("y")
    past = time.time() - 8 * 86400
    os.utime(inbox / "old", (past, past))
    os.utime(inbox / "1_f.txt", (past, past))
    assert clean_inbox(tmp_path) == 2
    assert [p.name for p in inbox.iterdir()] == ["new"]
    assert clean_inbox(tmp_path / "missing") == 0
