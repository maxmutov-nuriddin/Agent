import asyncio
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
    await app.store.set_kv("eco", "0")                      # sifat rejimi
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
    app, _ = await make_app(scripted_company(), MAX_AGENTS="8")  # seed = 8 ta, joy yo`q
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
    assert res["status"] == "limit" and res["result"]


# ---------- to'xtatish va migratsiya ----------
async def test_stopped_task_keeps_partial_work_and_frees_resources(make_app):
    import asyncio
    gate = asyncio.Event()
    app, _ = await make_app(scripted_company())
    orig = app.router.call

    async def slow(*a, **k):
        if k.get("agent") == "marketer":
            await gate.wait()
        return await orig(*a, **k)
    app.router.call = slow
    t = asyncio.create_task(app.orch.run_task("x", 1))
    for _ in range(200):
        await asyncio.sleep(0.02)
        if "marketer" in app.team.busy:
            break
    assert app.orch.stop_task(1) and not app.orch.stop_task(999)
    res = await t
    assert res["status"] == "cancelled" and "to'xtatdingiz" in res["error"]
    assert "researcher" in res["result"]  # to'xtatishgacha bajarilgan ish saqlanadi
    assert app.team.busy == {} and app.orch.running == {} and app.orch._stopping == set()


async def test_external_cancellation_is_not_swallowed(make_app):
    import asyncio
    app, _ = await make_app(scripted_company())
    orig = app.router.call
    gate = asyncio.Event()

    async def slow(*a, **k):
        if k.get("agent") == "researcher":
            await gate.wait()
        return await orig(*a, **k)
    app.router.call = slow
    t = asyncio.create_task(app.orch.run_task("x", 1))
    await asyncio.sleep(0.2)
    t.cancel()  # dastur to'xtayotgan holat: foydalanuvchi to'xtatishi emas
    import pytest
    with pytest.raises(asyncio.CancelledError):
        await t


async def test_run_command_is_killed_when_task_is_cancelled(make_app, tmp_path):
    import asyncio
    from aicompany.approvals import AutoApprover
    from aicompany.tools import TOOLS, ToolEnv
    app, _ = await make_app(scripted_company())
    env = ToolEnv(workspace=tmp_path, store=app.store, settings=app.settings, task_id=1, approver=AutoApprover(True))
    procs = []
    orig = asyncio.create_subprocess_shell

    async def spy(*a, **k):
        p = await orig(*a, **k)
        procs.append(p)
        return p
    asyncio.create_subprocess_shell = spy
    try:
        t = asyncio.create_task(TOOLS["run_command"].handler(env, {"command": "sleep 30"}))
        await asyncio.sleep(0.4)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.3)
    finally:
        asyncio.create_subprocess_shell = orig
    assert procs and procs[0].returncode is not None  # jarayon o'ldirilgan


async def test_old_database_is_migrated_without_data_loss(tmp_path):
    import sqlite3
    from aicompany.db import Store
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
      CREATE TABLE tasks (id INTEGER PRIMARY KEY, chat_id INTEGER, request TEXT NOT NULL, status TEXT NOT NULL,
                          plan TEXT, result TEXT, created_at TEXT, finished_at TEXT);
      INSERT INTO tasks (id, chat_id, request, status, created_at) VALUES (7, 1, 'eski vazifa', 'done', '2026-01-01');
      CREATE TABLE agents (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, role TEXT NOT NULL,
                           system_prompt TEXT NOT NULL, tier TEXT NOT NULL, status TEXT NOT NULL, created_by TEXT,
                           created_at TEXT, fired_at TEXT);
      INSERT INTO agents (name, role, system_prompt, tier, status) VALUES ('ceo', 'r', 'p', 'mid', 'active');
    """)
    con.commit()
    con.close()
    store = Store(f"sqlite+aiosqlite:///{path}")
    await store.init()
    await store.init()  # ikkinchi marta ham xavfsiz
    tasks = await store.list_tasks()
    assert [t["request"] for t in tasks] == ["eski vazifa"] and tasks[0]["archived"] == 0
    assert (await store.list_agents())[0]["tools"] == ""
    await store.set_archived(7, True)
    assert await store.list_tasks() == [] and len(await store.list_tasks(archived=True)) == 1
    await store.close()


async def test_file_database_survives_heavy_concurrent_use(make_app):
    """Agentlar, veb-so'rovlar va Telegram bir vaqtda bazaga yozadi/o'qiydi: hech narsa yo'qolmasligi kerak."""
    import asyncio
    app, _ = await make_app(scripted_company())
    st = app.store

    async def writer(i):
        await st.add_chat(1, "sys", f"m{i}")
        await st.add_usage("anthropic", "m", None, f"a{i % 5}", 1, 1, 0, 0.001)
        await st.add_memory(f"fact {i}")
        await st.set_kv(f"k{i % 7}", str(i))

    async def reader(_):
        await st.recent_chat(1, 10)
        await st.spent("anthropic")
        await st.list_agents()
        await st.search_memories("fact", 3)

    await asyncio.gather(*[writer(i) if i % 2 else reader(i) for i in range(120)], *[writer(1000 + i) for i in range(40)])
    assert len(await st.chat_since(1, 0, 500)) == 60 + 40
    assert round(await st.spent("anthropic"), 6) == round(0.001 * 100, 6)
    assert len(await st.recent_memories(500)) == 100


# ---------- vazifa holatlari: nega to'xtadi? ----------
async def test_status_tells_why_a_task_did_not_finish(make_app):
    from aicompany.providers import ProviderError
    app, _ = await make_app(lambda *a: ProviderError("anthropic: 401 bad key"))
    r = await app.orch.run_task("x", 1)
    t = await app.store.get_task(r["task_id"])
    assert r["status"] == t["status"] == "failed" and "bad key" in t["note"]

    app, _ = await make_app(scripted_company(), MAX_TASK_USD="0.025")
    r = await app.orch.run_task("x", 1)
    t = await app.store.get_task(r["task_id"])
    assert t["status"] == "limit" and "limiti" in t["note"] and r["result"]

    app, _ = await make_app(scripted_company())
    r = await app.orch.run_task("x", 1)
    t = await app.store.get_task(r["task_id"])
    assert t["status"] == "done" and t["note"] is None


async def test_old_database_gets_every_new_column_including_text_defaults(tmp_path):
    """Foydalanuvchi bazasi eski: tasks (note/archived yo'q), approvals (kind yo'q), agents (tools yo'q)."""
    import sqlite3
    import sqlalchemy as sa
    from aicompany.db import Store
    path = tmp_path / "old2.db"
    con = sqlite3.connect(path)
    con.executescript("""
      CREATE TABLE tasks (id INTEGER PRIMARY KEY, chat_id INTEGER, request TEXT NOT NULL, status TEXT NOT NULL,
                          plan TEXT, result TEXT, created_at TEXT, finished_at TEXT);
      CREATE TABLE approvals (id INTEGER PRIMARY KEY, task_id INTEGER, agent TEXT, description TEXT, status TEXT,
                              created_at TEXT, decided_at TEXT);
      INSERT INTO approvals (task_id, agent, description, status) VALUES (1, 'developer', 'npm install', 'approved');
    """)
    con.commit()
    con.close()
    store = Store(f"sqlite+aiosqlite:///{path}")
    await store.init()
    rows = await store._all(sa.text("select description, kind, status from approvals"))
    assert rows == [{"description": "npm install", "kind": "command", "status": "approved"}]   # eski qator 'command' bo'ldi
    await store.create_approval(1, "assistant", "Kimga: Ali", "telegram")
    assert [r["kind"] for r in await store.task_approvals(1)] == ["command", "telegram"]
    await store.create_task(1, "yangi")
    t = (await store.list_tasks())[0]
    assert t["note"] is None and t["archived"] == 0
    await store.init()  # takroriy ishga tushirish xavfsiz
    await store.close()


async def test_finished_task_has_result_file_prompt_and_short_answer(make_app):
    base = scripted_company()

    def handler(system, user, model):
        if "'ceo'" in system and "===ANSWER===" in user:
            assert "sayt yasab ber" in user and "TO'LIQ HISOBOT" in user      # qadoqlovchi so'rov va to'liq natijani ko'radi
            return "===ANSWER===\nSayt tayyor: index.html ni oching.\n===PROMPT===\n# Maqsad\nKofexona uchun sayt..."
        if "'ceo'" in system and "Plan the work" not in user:
            return "TO'LIQ HISOBOT: uzun natija"
        return base(system, user, model)
    app, _ = await make_app(handler)
    res = await app.orch.run_task("sayt yasab ber", 1)
    ws = app.settings.workspace_dir / f"task_{res['task_id']}"
    assert res["result"] == "Sayt tayyor: index.html ni oching."                   # Natija: qisqa, aniq javob
    assert (ws / "NATIJA.md").read_text() == "TO'LIQ HISOBOT: uzun natija"         # bitta tayyor to'liq natija
    assert (ws / "PROMPT.md").read_text().startswith("# Maqsad")                    # boshqa AI uchun tayyor prompt
    assert res["files"][:2] == ["NATIJA.md", "PROMPT.md"]                           # birinchi bo'lib ko'rinadi


async def test_packaging_falls_back_when_ceo_ignores_format(make_app):
    base = scripted_company()

    def handler(system, user, model):
        if "'ceo'" in system and "===ANSWER===" in user:
            return ""                                                               # format buzildi
        return base(system, user, model)
    app, _ = await make_app(handler)
    res = await app.orch.run_task("reja tuz", 1)
    ws = app.settings.workspace_dir / f"task_{res['task_id']}"
    assert res["status"] == "done" and res["result"] == "FINAL DELIVERABLE"        # natija yo'qolmaydi
    repro = (ws / "PROMPT.md").read_text()
    assert "reja tuz" in repro and "FINAL DELIVERABLE" in repro                     # zaxira prompt so'rov + namunadan


async def test_interrupted_task_is_resumed_once_after_restart(make_app):
    app, _ = await make_app(scripted_company())
    tid = await app.store.create_task(0, "sayt yasab ber")            # server o'rtada o'chgan: running qolgan
    assert await app.store.fail_stale_tasks() == 1
    notes = []

    async def notify(s):
        notes.append(s)
    await app.orch.resume_interrupted(notify, delay=0)
    tasks = {t["id"]: t for t in await app.store.list_tasks(10)}
    assert len(tasks) == 2 and tasks[tid]["status"] == "interrupted" and "vtomatik" in tasks[tid]["note"]
    new = tasks[max(tasks)]
    assert new["status"] == "done" and new["based_on"] == tid
    assert any("avtomatik davom ettirilmoqda" in n for n in notes)
    await app.orch.resume_interrupted(notify, delay=0)               # ikkinchi qayta ishga tushish: takrorlamaydi
    assert len(await app.store.list_tasks(10)) == 2
    # nusxa ham uzilsa, cheksiz takrorlanmaydi
    await app.store.update_task(new["id"], status="interrupted", finished_at=__import__("aicompany.db", fromlist=["now"]).now())
    await app.orch.resume_interrupted(notify, delay=0)
    assert len(await app.store.list_tasks(10)) == 2


async def test_daily_report_lists_important_actions(make_app):
    from datetime import datetime, timedelta, timezone
    from aicompany.report import build_report
    app, _ = await make_app(scripted_company())
    since = datetime.now(timezone.utc) - timedelta(days=1)
    quiet = await build_report(app, since, "Hisobot")
    assert "muhim amal bo'lmadi" in quiet
    await app.store.audit("assistant", "tg_send", "APPROVED: Kimga: Ali\n\nErtaga ko'rishamiz")
    await app.store.audit("assistant", "tg_send", "DENIED: Kimga: Vali\n\nsalom")
    await app.store.audit("developer", "run_command", "APPROVED: npm install")
    await app.store.audit("team", "private_fallback", "assistant: ulanmagan")
    await app.store.audit("orchestrator", "task_error", "#3: boom")
    text = await build_report(app, since, "Hisobot")
    assert "1 ta yuborildi, 1 ta rad" in text and "Ali" in text and "npm install" in text
    assert "Maxfiy AI ishlamadi: 1 marta" in text and "Xatolar: 1" in text


async def test_resume_gives_progress_and_files_to_the_new_task(make_app):
    seen = []
    base = scripted_company()

    def handler(system, user, model):
        if "Plan the work" in user:
            seen.append(user)
        return base(system, user, model)
    app, _ = await make_app(handler)
    tid = await app.store.create_task(0, "sayt yasab ber")
    ws = app.settings.workspace_dir / f"task_{tid}"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "index.html").write_text("<h1>yarim</h1>")
    await app.store.add_message(tid, "researcher", "TADQIQOT NATIJASI: 3 ta raqobatchi")
    await app.store.add_message(tid, "ceo", "===ANSWER===\nqisqa javob\n===PROMPT===\nprompt")   # qadoqlash: kontekstga tushmasligi kerak
    await app.store.update_task(tid, status="cancelled", finished_at="2026-10-09T10:00:00+00:00")
    res = await app.orch.submit_task("sayt yasab ber", 0, None or (lambda s: asyncio.sleep(0)), None, based_on=tid)
    assert res["status"] == "done"
    plan_prompt = seen[-1]
    assert "RESUME" in plan_prompt and "do NOT redo" in plan_prompt and "TADQIQOT NATIJASI" in plan_prompt and "index.html" in plan_prompt
    assert "===ANSWER===" not in plan_prompt
    assert (app.settings.workspace_dir / f"task_{res['task_id']}" / "index.html").exists()   # fayl nusxalandi


async def test_paused_tasks_continue_after_resume_once(make_app):
    app, _ = await make_app(scripted_company())
    tid = await app.store.create_task(0, "reja tuz")
    from aicompany.db import now
    await app.store.update_task(tid, status="paused", finished_at=now())
    notes = []

    async def notify(s):
        notes.append(s)
    assert await app.orch.resume_stopped(notify, ("paused",), delay=0) == 1
    assert await app.orch.resume_stopped(notify, ("paused",), delay=0) == 0           # ikkinchi marta takrorlamaydi
    tasks = await app.store.list_tasks(10)
    assert len(tasks) == 2 and {t["status"] for t in tasks} == {"paused", "done"}
    assert any("davom ettirilmoqda" in n for n in notes) and any("🏁" in n for n in notes)


async def test_eco_mode_is_default_and_never_uses_the_strongest_tier(make_app):
    handler = scripted_company(plan={"summary": "t", "new_roles": [], "steps": [
        {"id": "s1", "agent": "researcher", "task": "a", "tier": "strong", "depends_on": []},
        {"id": "s2", "agent": "marketer", "task": "b", "tier": "strong", "depends_on": ["s1"]}]},
        qa=[{"verdict": "fail", "issues": ["x"]}, {"verdict": "pass", "issues": []}])
    app, provs = await make_app(handler)
    res = await app.orch.run_task("x", 1)
    assert res["status"] == "done"
    assert "claude-opus-5-5" not in provs["anthropic"].calls            # strong qadam va QA qayta yozishi ham o'rta darajada
    assert provs["anthropic"].calls[0] == "claude-haiku-5-5"             # reja arzon modelda
    await app.store.set_kv("eco", "0")
    provs["anthropic"].calls.clear()
    await app.orch.run_task("y", 1)
    assert "claude-opus-5-5" in provs["anthropic"].calls                 # sifat rejimida strong ishlaydi


async def test_eco_single_step_skips_the_rewrite_call(make_app):
    one = {"summary": "t", "new_roles": [], "steps": [{"id": "s1", "agent": "researcher", "task": "a", "depends_on": []}]}
    app, provs = await make_app(scripted_company(plan=one))
    calls = []
    orig = app.orch._synthesize

    async def spy(*a, **k):
        calls.append(1)
        return await orig(*a, **k)
    app.orch._synthesize = spy
    res = await app.orch.run_task("x", 1)
    assert res["status"] == "done" and calls == []                       # qayta yozish chaqirilmadi
    await app.store.set_kv("eco", "0")
    await app.orch.run_task("y", 1)
    assert calls == [1]
