"""Tuzilma: bo'limlar, agent kartochkasi, har agentga o'z AI, avtonom agentlar, bo'lim boshliqlari."""
import json

from .conftest import scripted_company
from .test_web import web  # noqa: F401  (fixture)


async def test_agent_meta_defaults_and_per_agent_model(make_app):
    seen = []

    def handler(system, user, model):
        seen.append(model)
        return "ok"
    app, _ = await make_app(handler, names=("anthropic", "gemini"))
    assert (await app.team.meta("developer"))["dept"] == "tech"
    assert (await app.team.meta("fact_checker"))["dept"] == "tadqiqot"
    assert (await app.team.meta("researcher"))["model"] == "auto"
    await app.store.set_kv("primary_provider", "anthropic")
    await app.team.run_agent("researcher", "x")
    assert seen[-1].startswith("claude")                      # avto: asosiy AI
    await app.team.set_meta("researcher", model="gemini")
    await app.team.run_agent("researcher", "x")
    assert seen[-1].startswith("gemini")                      # shu xodim uchun tanlangan AI


async def test_new_seed_agents_and_hr_department(make_app):
    app, _ = await make_app(scripted_company())
    names = {a["name"] for a in await app.store.list_agents()}
    assert {"architect", "fact_checker", "finance_analyst"} <= names


async def test_server_cost_and_memory_watchers(make_app):
    app, _ = await make_app(scripted_company())
    auto = app.auto
    assert (await auto.state("server_watch"))["mode"] == "report" and (await auto.state("memory_keeper"))["mode"] == "off"
    res = await auto.run("server_watch", force=True)
    assert res.startswith(("✅", "⚠️")) and "disk" in (await auto.state("server_watch"))["result"] + res
    # xarajat: bugun keskin o'sdi -> ogohlantirish
    await app.store.add_usage("anthropic", "m", None, "x", 0, 0, 0, 1.2)
    sent = []

    async def send(t):
        sent.append(t)
    res = await auto.run("cost_watch", [send], force=True)
    assert res.startswith("⚠️") and sent and "Xarajat nazoratchisi" in sent[0]
    # takror xotira: «xabar beradi» faqat aytadi, «o'zi tuzatadi» o'chiradi
    for _ in range(3):
        await app.store.add_memory("Egasi o'zbekcha javob yoqtiradi", "test")
    await auto.set_mode("memory_keeper", "report")
    assert "2 ta takror" in await auto.run("memory_keeper")
    assert len(await app.store.recent_memories()) >= 3
    await auto.set_mode("memory_keeper", "auto")
    await auto.run("memory_keeper")
    assert len([m for m in await app.store.recent_memories() if "o'zbekcha" in m["text"]]) == 1
    import pytest
    with pytest.raises(ValueError):
        await auto.set_mode("server_watch", "auto")           # server kuzatuvchi hech narsani o'zgartirmaydi


async def test_off_agent_does_not_run(make_app):
    app, _ = await make_app(scripted_company())
    await app.auto.set_mode("server_watch", "off")
    assert await app.auto.run("server_watch") == ""


async def test_dept_leads_refine_steps(make_app):
    plan = {"summary": "s", "new_roles": [], "complexity": "normal", "steps": [
        {"id": "s1", "agent": "developer", "task": "sayt qil", "depends_on": []},
        {"id": "s2", "agent": "marketer", "task": "matn yoz", "depends_on": []}]}
    base = scripted_company(plan=plan)
    got = {}

    def handler(system, user, model):
        if "You lead this department" in user:
            sid = "s1" if "id s1" in user else "s2"
            return json.dumps({"steps": [{"id": sid, "task": f"ANIQ VAZIFA {sid}: talablar, standart va tekshiruv mezonlari"}]})
        if "'developer'" in system or "'marketer'" in system:
            got[user[:12]] = True
        return base(system, user, model)
    app, _ = await make_app(handler)
    await app.store.set_kv("dept_leads", "1")
    res = await app.orch.run_task("loyiha", 1)
    assert res["status"] == "done"
    assert any(k.startswith("ANIQ VAZIFA") for k in got)      # xodim aniqlashtirilgan topshiriqni oldi


async def test_org_api(web):
    from .test_web import post
    c, app = web
    r = await c.get("/api/org", headers={"Authorization": "Bearer " + app.settings.web_token})
    d = await r.json()
    assert {x["key"] for x in d["depts"]} >= {"tech", "sifat"} and d["auto"] and "auto" in d["models"]
    assert any(a["name"] == "architect" and a["dept"] == "tech" for a in d["agents"])
    assert (await post(c, "/api/team/meta", {"name": "developer", "dept": "nope"}))[0] == 400
    assert (await post(c, "/api/team/meta", {"name": "developer", "model": "groq"}))[0] == 400     # kalit yo'q
    assert (await post(c, "/api/team/meta", {"name": "developer", "dept": "marketing", "tier": "strong"}))[1]["dept"] == "marketing"
    assert (await app.store.get_agent("developer"))["tier"] == "strong"
    assert (await post(c, "/api/auto", {"name": "link_watch", "mode": "report"}))[1] == {"mode": "report"}
    assert (await post(c, "/api/auto", {"name": "link_watch", "mode": "auto"}))[0] == 400
    assert (await post(c, "/api/dept_leads", {"enabled": True}))[1] == {"enabled": True}
    status, d = await post(c, "/api/auto/run", {"name": "server_watch"})
    assert status == 200 and d["result"]


async def test_widget_v2_live_money_day(web, monkeypatch):
    import asyncio
    from aicompany import briefing
    c, app = web

    async def no_net(*a, **kw):
        raise RuntimeError("tarmoq yo'q")
    monkeypatch.setattr(briefing, "weather", no_net)
    monkeypatch.setattr(briefing, "rates", no_net)
    tid = await app.store.create_task(1, "Moliyaviy tahlil")
    await app.store.update_task(tid, status="running", plan=json.dumps({"steps": [{"id": "s1"}, {"id": "s2"}, {"id": "s3"}]}))
    await app.store.set_kv(f"ckpt:{tid}", json.dumps({"outputs": {"s1": "x"}}))
    await app.store.add_usage("gemini", "m", tid, "researcher", 0, 0, 0, 0.21)
    app.team.busy["researcher"] = {"count": 1, "task_id": tid}
    app.team._act("researcher", "🌐 cbu.uz o'qiyapti")
    app.orch.phase[tid] = "1/3 qadam: Tahlilchi tugatdi"
    r = await c.get("/api/widget?token=" + app.settings.widget_token)
    d = await r.json()
    assert d["v"] == 2 and "date" in d["header"]
    live = d["live"][0]
    assert live["id"] == tid and live["steps_total"] == 3 and live["steps_done"] == 1 and live["cost"] == 0.21
    assert live["agents"][0]["label"] == "Tahlilchi" and "cbu.uz" in live["agents"][0]["act"]
    assert d["money"]["month"] >= 0.21 and "providers" in d["money"]
    assert "running" in d["day"]["timeline"]
    assert "working" in d and "budget_left" in d                     # eski vidjet ham ishlayveradi
    app.team.busy.pop("researcher")


async def test_progress_push_is_opt_in_and_single_tag(make_app):
    app, _ = await make_app(scripted_company())
    sent = []

    async def fake_notify(kind, title, body="", url="/", **kw):
        sent.append((kind, kw.get("tag"), kw.get("renotify")))
        return 1
    assert (await app.push.prefs())["progress"] is False and (await app.push.prefs())["done"] is True
    app.push.notify = fake_notify
    await app.push.progress(7, "2/5 qadam")
    await app.push.task_done({"kind": "task", "task_id": 7, "status": "done", "result": "ok"})
    assert sent == [("progress", "task-7", False), ("done", "task-7", None)]   # bitta bildirishnoma o'rnida almashadi


def test_describe_tool_activity():
    from aicompany.team import describe_tool
    assert describe_tool("fetch_url", {"url": "https://www.cbu.uz/uz/"}) == "🌐 cbu.uz o'qiyapti"
    assert describe_tool("write_file", {"path": "unit_iqtisod.md"}) == "📝 unit_iqtisod.md yozyapti"
    assert describe_tool("web_search", {"query": "dollar kursi"}).startswith("🔎")


async def test_widget_script_is_prefilled(web):
    c, app = web
    r = await c.get("/api/widget-script", headers={"Authorization": "Bearer " + app.settings.web_token})
    src = await r.text()
    assert r.status == 200 and "WIDGET_TOKEN_NI_SHU_YERGA" not in src and app.settings.widget_token in src and "Versiya 2" in src
