import json

from aicompany.util import extract_json

from .conftest import scripted_company


def test_extract_json_variants():
    assert extract_json('text {"a": {"b": "}"}} tail') == {"a": {"b": "}"}}
    assert extract_json('```json\n{"x": 1}\n```') == {"x": 1}


async def test_full_task_flow(make_app):
    app, provs = await make_app(scripted_company())
    notes = []

    async def notify(s):
        notes.append(s)
    res = await app.orch.run_task("sayt yasab ber", 1, notify)
    assert res["status"] == "done" and res["result"] == "FINAL DELIVERABLE"
    msgs = await app.store.task_messages(res["task_id"])
    agents_used = [m["agent"] for m in msgs]
    assert agents_used.index("researcher") < agents_used.index("marketer")  # bog'liqlik tartibi
    assert "qa" in agents_used
    assert (await app.store.get_task(res["task_id"]))["status"] == "done"


async def test_qa_failure_triggers_revision_on_strong_tier(make_app):
    handler = scripted_company(qa=[{"verdict": "fail", "issues": ["no pricing"]}, {"verdict": "pass", "issues": []}])
    app, provs = await make_app(handler)
    res = await app.orch.run_task("x", 1)
    assert res["status"] == "done"
    assert "claude-opus-5-5" in provs["anthropic"].calls  # qayta ishlash strong modelda


async def test_qa_still_failing_is_flagged_not_hidden(make_app):
    handler = scripted_company(qa=[{"verdict": "fail", "issues": ["bad"]}])
    app, _ = await make_app(handler)
    res = await app.orch.run_task("x", 1)
    assert "QA hali ham e'tiroz" in res["result"] and "bad" in res["result"]


async def test_hr_hires_missing_role_and_downgrades_strong(make_app):
    plan = {"summary": "s", "new_roles": [{"name": "SEO Specialist", "why": "need seo"}],
            "steps": [{"id": "s1", "agent": "seo_specialist", "task": "do seo", "depends_on": []}]}
    app, _ = await make_app(scripted_company(plan=plan))
    res = await app.orch.run_task("x", 1)
    assert res["status"] == "done"
    a = await app.store.get_agent("seo_specialist")
    assert a and a["tier"] == "mid"  # HR "strong" so'rasa ham avtomatik mid
    msgs = [m["agent"] for m in await app.store.task_messages(res["task_id"])]
    assert "seo_specialist" in msgs


async def test_hire_limit_and_fire_protection(make_app):
    app, _ = await make_app(scripted_company(), MAX_AGENTS="7")  # seed = 7 ta, joy yo'q
    assert await app.team.hire("newbie", "why") is None
    assert not await app.team.fire("ceo")
    assert await app.team.fire("marketer")
    assert await app.team.hire("newbie", "why") == "newbie"


async def test_unknown_agent_falls_back_to_generalist(make_app):
    plan = {"summary": "s", "new_roles": [], "steps": [{"id": "s1", "agent": "ghost", "task": "t", "depends_on": []}]}
    app, _ = await make_app(scripted_company(plan=plan))
    res = await app.orch.run_task("x", 1)
    assert res["status"] == "done"
    assert "generalist" in [m["agent"] for m in await app.store.task_messages(res["task_id"])]


async def test_pause_stops_task(make_app):
    app, _ = await make_app(scripted_company())
    await app.store.set_kv("paused", "1")
    res = await app.orch.run_task("x", 1)
    assert res["status"] == "paused"


async def test_bad_plan_fails_cleanly(make_app):
    app, _ = await make_app(lambda s, u, m: "I refuse to answer in JSON")
    res = await app.orch.run_task("x", 1)
    assert res["status"] == "failed" and "reja" in res["error"]


async def test_budget_exhaustion_returns_partial(make_app):
    app, _ = await make_app(scripted_company(), MAX_TASK_USD="0.025")
    res = await app.orch.run_task("x", 1)
    assert res["status"] == "stopped" and res["result"]
