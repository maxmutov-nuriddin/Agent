import json

import sqlalchemy as sa

from .conftest import scripted_company


def desk(decisions):
    """Front desk javoblari ketma-ket; qolgan chaqiruvlar oddiy kompaniya."""
    seq = list(decisions)
    base = scripted_company()
    seen, plans = [], []

    def handler(system, user, model):
        if "Plan the work" in user:
            plans.append(user)
        if "front desk" in system or "in a CHAT" in system:
            seen.append((user, model))
            d = seq.pop(0)
            return d if isinstance(d, str) else json.dumps(d)
        return base(system, user, model)
    handler.seen, handler.plans = seen, plans
    return handler


async def n_tasks(app):
    return len(await app.store.list_tasks(100))


async def test_greeting_is_chat_not_task(make_app):
    h = desk([{"mode": "chat", "reply": "Salom! Qanday yordam beray?"}])
    app, provs = await make_app(h)
    notes = []

    async def notify(s):
        notes.append(s)
    res = await app.orch.handle("salom", 1, notify)
    assert res == {"kind": "chat", "reply": "Salom! Qanday yordam beray?", "proposed_task": None}
    assert notes == ["Salom! Qanday yordam beray?"] and await n_tasks(app) == 0
    assert provs["anthropic"].calls == ["claude-haiku-5-5"]  # bitta arzon chaqiruv, jamoa ishlamadi


async def test_clear_task_runs_after_acknowledgement(make_app):
    h = desk([{"mode": "task", "reply": "Boshladim", "task": "Write 5 Instagram ideas for a coffee shop"}])
    app, _ = await make_app(h)
    notes = []

    async def notify(s):
        notes.append(s)
    res = await app.orch.handle("kofexona uchun 5 ta post g'oyasi", 1, notify)
    assert res["kind"] == "task" and res["status"] == "done"
    assert notes[0] == "Boshladim"
    t = await app.store.get_task(res["task_id"])
    assert t["request"] == "Write 5 Instagram ideas for a coffee shop"  # kengaytirilgan, to'liq vazifa


async def test_clarifying_question_then_task_uses_history(make_app):
    h = desk([
        {"mode": "chat", "reply": "Qaysi shahar uchun?"},
        {"mode": "task", "reply": "Tushunarli", "task": "Make a plan for a coffee shop in Tashkent"}])
    app, _ = await make_app(h)
    r1 = await app.orch.handle("reja tuz", 1)
    assert r1["kind"] == "chat" and await n_tasks(app) == 0
    r2 = await app.orch.handle("Toshkent", 1)
    assert r2["kind"] == "task"
    second_prompt = h.seen[1][0]
    assert "Owner: reja tuz" in second_prompt and "CEO: Qaysi shahar uchun?" in second_prompt
    assert second_prompt.rstrip().endswith("Toshkent")


async def test_followup_can_see_previous_task_result(make_app):
    h = desk([{"mode": "task", "reply": "", "task": "t1"}, {"mode": "chat", "reply": "ok"}])
    app, _ = await make_app(h)
    await app.orch.handle("birinchi", 1)
    await app.orch.handle("buni qisqartir", 1)
    assert "FINAL DELIVERABLE" in h.seen[1][0] and "Vazifa #1 done" in h.seen[1][0]


async def test_unparseable_decision_falls_back_to_chat_cheaply(make_app):
    app, _ = await make_app(desk(["Salom, men shu yerdaman!"]))
    res = await app.orch.handle("salom", 1)
    assert res["kind"] == "chat" and "shu yerdaman" in res["reply"] and await n_tasks(app) == 0


async def test_task_mode_without_task_text_uses_original_message(make_app):
    app, _ = await make_app(desk([{"mode": "task", "reply": "ok", "task": ""}]))
    res = await app.orch.handle("sayt yasab ber", 1)
    assert res["kind"] == "task"
    assert (await app.store.get_task(res["task_id"]))["request"] == "sayt yasab ber"


async def test_attachment_forces_task_without_front_desk(make_app, tmp_path):
    h = desk([])  # front desk chaqirilsa pop xatosi beradi
    app, _ = await make_app(h)
    f = tmp_path / "brief.txt"
    f.write_text("x")
    res = await app.orch.handle("buni ko'rib chiq", 1, attachments=[f])
    assert res["kind"] == "task" and h.seen == []


async def test_history_is_per_chat_and_clearable(make_app):
    h = desk([{"mode": "chat", "reply": "a"}, {"mode": "chat", "reply": "b"}, {"mode": "chat", "reply": "c"}])
    app, _ = await make_app(h)
    await app.orch.handle("one", 1)
    await app.orch.handle("two", 2)
    assert "Owner: one" not in h.seen[1][0]  # boshqa chat tarixi aralashmaydi
    await app.store.clear_chat(1)
    await app.orch.handle("three", 1)
    assert "Owner: one" not in h.seen[2][0]


async def test_chat_cost_is_tracked_under_ceo_chat(make_app):
    app, _ = await make_app(desk([{"mode": "chat", "reply": "hi"}]))
    await app.orch.handle("salom", 1)
    rows = await app.store._all(sa.text("select agent, model from usage"))
    assert rows == [{"agent": "ceo-chat", "model": "claude-haiku-5-5"}]


async def test_parallel_task_limit(make_app):
    import asyncio
    active, peak = 0, 0
    base = scripted_company()

    def handler(system, user, model):
        return base(system, user, model)
    app, _ = await make_app(handler, MAX_PARALLEL_TASKS="1")
    orig = app.orch._run

    async def tracked(*a, **k):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        try:
            return await orig(*a, **k)
        finally:
            active -= 1
    app.orch._run = tracked
    await asyncio.gather(app.orch.run_task("a", 1), app.orch.run_task("b", 1))
    assert peak == 1



async def test_front_desk_can_continue_an_earlier_task(make_app):
    h = desk([{"mode": "task", "reply": "", "task": "landing"},
              {"mode": "task", "reply": "O'zgartiraman", "task": "Sarlavhani qizil qil", "based_on": 1},
              {"mode": "task", "reply": "", "task": "yangi ish", "based_on": 77}])      # mavjud bo'lmagan vazifa
    app, _ = await make_app(h)
    await app.orch.handle("landing yarat", 1)
    (app.settings.workspace_dir / "task_1" / "page.html").write_text("eski")
    r2 = await app.orch.handle("sarlavhani qizil qil", 1)
    t2 = await app.store.get_task(r2["task_id"])
    assert t2["based_on"] == 1 and (app.settings.workspace_dir / f"task_{r2['task_id']}" / "page.html").exists()
    assert t2["request"] == "Sarlavhani qizil qil"                                         # bazada sizning matningiz
    assert "continues task #1" in h.plans[-1] and "page.html" in h.plans[-1]               # rahbar kontekstni oladi
    r3 = await app.orch.handle("yangi narsa", 1)
    assert (await app.store.get_task(r3["task_id"]))["based_on"] is None                   # noto'g'ri raqam e'tiborsiz


async def test_chat_proposal_carries_based_on(make_app):
    h = desk([{"mode": "task", "reply": "", "task": "landing"},
              {"reply": "Shu saytni o'zgartiraymi?", "proposed_task": "Rangni o'zgartir", "based_on": "#1"}])
    app, _ = await make_app(h)
    await app.orch.handle("landing yarat", 1)
    r = await app.orch.handle("rangini o'zgartirsa bo'ladimi?", 1, allow_tasks=False)
    assert r["kind"] == "chat" and r["proposed_task"] == "Rangni o'zgartir"
    prop = [m for m in await app.store.recent_chat(1, 20) if m["role"] == "proposal"][-1]
    assert json.loads(prop["text"]) == {"task": "Rangni o'zgartir", "based_on": 1}
