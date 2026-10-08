"""Tejamkorlik (A1-A4) va kuch (B1-B4): oddiy savol, QA'siz oddiy ish, kontekst kesish, asbob keshi,
qabul mezonlari, xotira vektorlari, to'xtagan joydan davom etish, doimiy qoidalarni birlashtirish."""
import json

from aicompany.app import build_app
from aicompany.tools import Tool

from .conftest import MockProvider, scripted_company, settings


def counting(handler):
    seen = []

    def h(system, user, model):
        seen.append((system, user))
        return handler(system, user, model)
    return h, seen


def who(seen, name):
    return [u for s, u in seen if f"'{name}'" in s]


async def test_simple_question_answered_directly_without_team(make_app):
    plan = {"summary": "s", "complexity": "simple", "direct_answer": "Toshkent O'zbekistonning poytaxti, aholisi 3 mln dan ortiq.",
            "acceptance": ["poytaxtni aytish"], "steps": [{"id": "s1", "agent": "researcher", "task": "x"}]}
    h, seen = counting(scripted_company(plan))
    app, _ = await make_app(h)
    res = await app.orch.submit_task("O'zbekiston poytaxti qaysi?", 1)
    assert res["status"] == "done" and "Toshkent" in res["result"]
    assert not who(seen, "researcher") and not who(seen, "qa")                   # jamoa ham, QA ham chaqirilmadi
    assert not any("===ANSWER===" in u for _, u in seen)                         # qadoqlash so'rovi ham yo'q
    assert "NATIJA.md" in res["files"] and "PROMPT.md" in res["files"]


async def test_simple_single_step_skips_qa_and_packaging(make_app):
    plan = {"summary": "s", "complexity": "simple", "steps": [{"id": "s1", "agent": "marketer", "task": "slogan yoz"}]}
    h, seen = counting(scripted_company(plan))
    app, _ = await make_app(h)
    res = await app.orch.submit_task("Kofe uchun slogan", 1)
    assert res["status"] == "done" and res["result"] == "output of marketer"
    assert not who(seen, "qa") and not any("===ANSWER===" in u for _, u in seen)


async def test_acceptance_criteria_reach_qa_and_steps_are_saved_hidden(make_app):
    plan = {"summary": "s", "acceptance": ["3 ta raqobatchi", "narxlar jadvali"], "steps": [
        {"id": "s1", "agent": "researcher", "task": "research"},
        {"id": "s2", "agent": "marketer", "task": "write", "depends_on": ["s1"]}]}
    base = scripted_company(plan)
    long_out = "R" * 5000

    def handler(system, user, model):
        if "'researcher'" in system:
            return long_out
        return base(system, user, model)
    h, seen = counting(handler)
    app, _ = await make_app(h)
    res = await app.orch.submit_task("Raqobatchilar tahlili", 1)
    assert res["status"] == "done"
    qa = who(seen, "qa")
    assert qa and "Acceptance criteria" in qa[0] and "narxlar jadvali" in qa[0]
    mk = who(seen, "marketer")[0]
    assert ".steps/s1.md" in mk and mk.count("R") < 4000                          # kontekst qisqartirildi, to'liqi faylda
    ws = app.settings.workspace_dir / f"task_{res['task_id']}"
    assert (ws / ".steps" / "s1.md").read_text() == long_out
    assert not any(f.startswith(".steps") for f in res["files"])                  # ichki fayllar natijada ko'rinmaydi


async def test_tool_results_are_cached_within_and_across_tasks(make_app):
    app, _ = await make_app(scripted_company())
    calls = []

    async def fake_search(env, a):
        calls.append(a["query"])
        return "natija: " + a["query"]
    tool = Tool("web_search", "web", "d", {}, fake_search)
    from aicompany.tools import ToolEnv
    env1 = ToolEnv(workspace=app.settings.workspace_dir, store=app.store, settings=app.settings)
    env2 = ToolEnv(workspace=app.settings.workspace_dir, store=app.store, settings=app.settings)
    assert await app.team._cached_call(tool, env1, {"query": "kofe narxi"}) == "natija: kofe narxi"
    await app.team._cached_call(tool, env1, {"query": "kofe narxi"})
    await app.team._cached_call(tool, env2, {"query": "kofe narxi"})             # boshqa vazifa: bazadagi keshdan
    assert calls == ["kofe narxi"]
    other = Tool("write_file", "files", "d", {}, fake_search)                     # yozuvchi asboblar keshlanmaydi
    await app.team._cached_call(other, env1, {"query": "a"})
    await app.team._cached_call(other, env1, {"query": "a"})
    assert calls == ["kofe narxi", "a", "a"]


async def test_resume_continues_from_exact_step(make_app):
    plan = {"summary": "s", "steps": [
        {"id": "s1", "agent": "researcher", "task": "research"},
        {"id": "s2", "agent": "marketer", "task": "write", "depends_on": ["s1"]}]}
    base = scripted_company(plan)
    state = {"fail": True}

    def handler(system, user, model):
        if "'marketer'" in system and state["fail"]:
            return RuntimeError("tarmoq uzildi")
        return base(system, user, model)
    h, seen = counting(handler)
    app, _ = await make_app(h)
    first = await app.orch.submit_task("Reklama matni", 1)
    assert first["status"] == "failed" and await app.store.get_kv(f"ckpt:{first['task_id']}")
    state["fail"] = False
    n_research = len(who(seen, "researcher"))
    n_plans = sum(1 for s, u in seen if "Plan the work" in u)
    second = await app.orch.submit_task("Reklama matni", 1, based_on=first["task_id"])
    assert second["status"] == "done"
    assert len(who(seen, "researcher")) == n_research                             # bajarilgan qadam qayta qilinmadi
    assert sum(1 for s, u in seen if "Plan the work" in u) == n_plans              # qayta rejalashtirilmadi
    assert await app.store.get_kv(f"ckpt:{second['task_id']}") is None             # tugagach nazorat nuqtasi o'chirildi


async def test_preferences_are_merged_without_losing_them(make_app):
    merged = [f"qoida {i}" for i in range(10)]
    reply = {"rules": merged}

    def handler(system, user, model):
        if "standing rules" in system:
            return json.dumps(reply)
        return scripted_company()(system, user, model)
    app, _ = await make_app(handler)
    for i in range(16):
        await app.store.add_memory(f"eski qoida {i}", source="owner-pref")
    await app.orch._consolidate_prefs()
    assert sorted(p["text"] for p in await app.store.owner_prefs(100)) == sorted(merged)
    backup = json.loads(await app.store.get_kv("prefs_backup"))
    assert len(backup["rules"]) == 16                                              # eski ro'yxat zaxirada
    # juda qisqargan javob (qoidalar yo'qolishi xavfi) qabul qilinmaydi
    for i in range(6):
        await app.store.add_memory(f"yangi qoida {i}", source="owner-pref")
    reply["rules"] = ["bitta"]
    await app.orch._consolidate_prefs()
    assert len(await app.store.owner_prefs(100)) == 16


class EmbedProvider(MockProvider):
    """Soxta embedding: 'qahva/kofe/kafe' mavzusi bir xil vektor, qolganlari boshqa."""
    TOPIC = ("qahva", "kofe", "kafe")

    async def embed(self, texts, query=False):
        out = []
        for t in texts:
            low = t.lower()
            out.append([1.0, 0.0, 0.1] if any(w in low for w in self.TOPIC) else [0.0, 1.0, 0.1])
        return out, 0.0


async def test_semantic_memory_finds_related_meaning(make_app):
    prov = EmbedProvider("gemini", scripted_company())
    app = await build_app(settings(), {"gemini": prov})
    try:
        await app.store.add_memory("Egasi qahvaxona ochmoqchi, Chilonzorda", source="owner")
        await app.store.add_memory("Egasi har kuni sport bilan shug'ullanadi", source="owner")
        found = await app.orch._recall("kafe biznesi uchun reja", 3)
        assert found and "qahvaxona" in found[0]["text"]
        assert all(m["emb"] for m in await app.store.recent_memories())            # eski xotiralar vektorlandi
        plain = await app.store.search_memories("kafe biznesi uchun reja", 3)        # vektorsiz: so'z mos kelmaydi
        assert not any("qahvaxona" in m["text"] for m in plain)
    finally:
        await app.store.close()
