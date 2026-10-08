import asyncio
import base64
import json

import httpx
import pytest

from aicompany.config import ModelCfg, load_settings
from aicompany.providers import (GeminiProvider, GeminiTurn, LLMResult, ProviderError, VoiceError, VoiceUnavailable,
                                 build_providers)
from aicompany.router import BudgetExhausted
from aicompany.tools import ToolEnv, tool_defs, tools_for

from .conftest import MockProvider, scripted_company, settings

CFG = ModelCfg("gemini-x", 1.0, 2.0, 0.1)
TOOLS = [{"name": "write_file", "description": "w", "input_schema": {
              "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}},
         {"name": "list_files", "description": "l", "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}]


def gclient(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def gresp(parts, **usage):
    return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}],
                                     "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 10, **usage}})


# ---------- Gemini asbob chaqirish ----------
async def test_gemini_sends_function_declarations_without_unsupported_keys():
    seen = {}

    def h(req):
        seen["body"] = json.loads(req.content)
        return gresp([{"text": "ok"}])
    await GeminiProvider("k", gclient(h)).complete(CFG, "S", [{"role": "user", "content": "hi"}], 100, TOOLS)
    decls = seen["body"]["tools"][0]["functionDeclarations"]
    assert decls[0]["name"] == "write_file" and decls[0]["parameters"]["properties"] == {"path": {"type": "string"}}
    assert "additionalProperties" not in json.dumps(decls)
    assert "parameters" not in decls[1]  # bo'sh parametrli asbob: Gemini rad etadi


async def test_gemini_parses_function_calls_and_roundtrips_thought_signatures():
    bodies = []

    def h(req):
        bodies.append(json.loads(req.content))
        if len(bodies) == 1:
            return gresp([{"functionCall": {"name": "write_file", "args": {"path": "a.txt"}}, "thoughtSignature": "SIG123"},
                          {"functionCall": {"name": "list_files", "args": {}}}], thoughtsTokenCount=40)
        return gresp([{"text": "tayyor"}])
    p = GeminiProvider("k", gclient(h))
    msgs = [{"role": "user", "content": "go"}]
    r1 = await p.complete(CFG, "S", msgs, 100, TOOLS)
    assert [(c["id"], c["name"], c["input"]) for c in r1.tool_calls] == [("g0", "write_file", {"path": "a.txt"}), ("g1", "list_files", {})]
    assert isinstance(r1.raw_content, GeminiTurn) and r1.tokens_out == 50  # fikrlash tokenlari ham hisobda
    msgs += [{"role": "assistant", "content": r1.raw_content},
             {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "g0", "content": "yozildi"},
                                          {"type": "tool_result", "tool_use_id": "g1", "content": "Xato: yo'q", "is_error": True},
                                          {"type": "text", "text": "Tez tugating"}]}]
    r2 = await p.complete(CFG, "S", msgs, 100, TOOLS)
    assert r2.text == "tayyor"
    sent = bodies[1]["contents"]
    assert sent[1]["role"] == "model" and sent[1]["parts"][0]["thoughtSignature"] == "SIG123"  # o'zgarishsiz qaytdi
    user_parts = sent[2]["parts"]
    assert sent[2]["role"] == "user"
    assert user_parts[0] == {"functionResponse": {"name": "write_file", "response": {"result": "yozildi"}}}
    assert user_parts[1] == {"functionResponse": {"name": "list_files", "response": {"error": "Xato: yo'q"}}}
    assert user_parts[2] == {"text": "Tez tugating"}


async def test_gemini_ignores_thought_text_and_rejects_empty_answers():
    r = await GeminiProvider("k", gclient(lambda q: gresp([{"text": "o'ylayapman", "thought": True}, {"text": "javob"}]))
                             ).complete(CFG, "S", [{"role": "user", "content": "x"}], 10)
    assert r.text == "javob"
    with pytest.raises(ProviderError):
        await GeminiProvider("k", gclient(lambda q: gresp([]))).complete(CFG, "S", [{"role": "user", "content": "x"}], 10)


# ---------- ovoz ----------
async def test_gemini_transcribe_request_shape():
    seen = {}

    def h(req):
        seen["body"] = json.loads(req.content)
        return gresp([{"text": "  Salom dunyo  "}])
    r = await GeminiProvider("k", gclient(h)).transcribe(CFG, b"\x00\x01audio", "audio/ogg")
    part = seen["body"]["contents"][0]["parts"][1]["inline_data"]
    assert part == {"mime_type": "audio/ogg", "data": base64.b64encode(b"\x00\x01audio").decode()}
    assert "Transcribe" in seen["body"]["contents"][0]["parts"][0]["text"] and r.text.strip() == "Salom dunyo"


async def test_router_transcribe_records_usage_and_handles_edge_cases(make_app):
    app, provs = await make_app(scripted_company(), names=("anthropic", "gemini"), audio="Salom, kofexona haqida gaplashamiz")
    assert await app.router.transcribe(b"x" * 1000, "audio/ogg") == "Salom, kofexona haqida gaplashamiz"
    assert provs["gemini"].audio_calls == [("gemini-3.1-flash-lite", "audio/ogg", 1000)]
    rows = await app.store._all(__import__("sqlalchemy").text("select provider, agent from usage"))
    assert rows == [{"provider": "gemini", "agent": "voice"}]
    provs["gemini"].audio_text = "[no speech]"
    with pytest.raises(VoiceError, match="gap topilmadi"):
        await app.router.transcribe(b"x" * 1000, "audio/ogg")
    with pytest.raises(VoiceError, match="katta"):
        await app.router.transcribe(b"x" * 16_000_000, "audio/ogg")


async def test_voice_needs_gemini_and_respects_its_budget(make_app):
    app, _ = await make_app(scripted_company(), names=("anthropic",))
    with pytest.raises(VoiceUnavailable, match="GEMINI_API_KEY"):
        await app.router.transcribe(b"x" * 1000, "audio/ogg")
    app, _ = await make_app(scripted_company(), names=("gemini",), audio="matn", BUDGET_USD_GEMINI="0.0000001")
    with pytest.raises(VoiceError):
        await app.router.transcribe(b"x" * 1000, "audio/ogg")


# ---------- faqat bitta kalit ----------
def test_only_providers_with_keys_are_built():
    only_claude = load_settings({"ANTHROPIC_API_KEY": "a"})
    only_gemini = load_settings({"GEMINI_API_KEY": "g"})
    both = load_settings({"ANTHROPIC_API_KEY": "a", "GEMINI_API_KEY": "g"})
    assert set(build_providers(only_claude)) == {"anthropic"}
    assert set(build_providers(only_gemini)) == {"gemini"}
    assert set(build_providers(both)) == {"anthropic", "gemini"}
    assert build_providers(load_settings({})) == {}


async def test_only_claude_key_never_touches_gemini_and_vice_versa(make_app):
    for names, expect in ((("anthropic",), "claude-"), (("gemini",), "gemini-")):
        app, provs = await make_app(scripted_company(), names=names, PRIMARY_PROVIDER="gemini" if names == ("anthropic",) else "anthropic")
        res = await app.orch.run_task("x", 1)  # asosiy AI ulanmagan bo'lsa ham mavjud biri ishlaydi
        assert res["status"] == "done"
        used = provs[names[0]].calls
        assert used and all(m.startswith(expect) for m in used)


# ---------- asosiy provayder ----------
async def call(app, tier="cheap", **kw):
    return await app.router.call(tier, "s", [{"role": "user", "content": "hi"}], **kw)


async def test_auto_is_cheapest_and_primary_overrides(make_app):
    app, provs = await make_app(lambda *a: "ok", names=("anthropic", "gemini"))
    assert (await call(app)).provider == "anthropic"          # auto: Haiku arzonroq
    await app.store.set_kv("primary_provider", "gemini")
    assert (await call(app)).provider == "gemini"
    await app.store.set_kv("primary_provider", "auto")
    assert (await call(app)).provider == "anthropic"


async def test_primary_from_env_and_kv_wins_over_env(make_app):
    app, _ = await make_app(lambda *a: "ok", names=("anthropic", "gemini"), PRIMARY_PROVIDER="gemini")
    assert (await call(app)).provider == "gemini"
    await app.store.set_kv("primary_provider", "anthropic")
    assert (await call(app)).provider == "anthropic"


async def test_tier_overrides(make_app):
    app, _ = await make_app(lambda *a: "ok", names=("anthropic", "gemini"), PROVIDER_BY_TIER="cheap:gemini,strong:anthropic")
    assert (await call(app, "cheap")).provider == "gemini"
    assert (await call(app, "mid")).provider == "gemini"       # override yo'q: auto = eng arzoni (Gemini mid 3$ < Claude 10$)
    assert (await call(app, "strong")).provider == "anthropic"  # auto Gemini bo'lardi (9$ < 20$): override ishladi


async def test_primary_falls_back_when_failing_or_over_budget(make_app):
    def handler(system, user, model):
        return ProviderError("gemini down") if model.startswith("gemini") else "ok"
    app, provs = await make_app(handler, names=("anthropic", "gemini"), PRIMARY_PROVIDER="gemini")
    assert (await call(app)).provider == "anthropic"           # zaxira ishladi
    app, provs = await make_app(lambda *a: "ok", names=("anthropic", "gemini"), PRIMARY_PROVIDER="gemini", BUDGET_USD_GEMINI="0.0001")
    assert (await call(app)).provider == "anthropic"           # limit tugagan


async def test_unconnected_primary_is_ignored(make_app):
    app, _ = await make_app(lambda *a: "ok", names=("anthropic",), PRIMARY_PROVIDER="gemini")
    assert (await call(app)).provider == "anthropic"


# ---------- ish davomida bitta provayder ----------
def tool_call(i=0):
    return LLMResult("", 1, 1, 0, 0, [{"id": f"t{i}", "name": "list_files", "input": {}}], [{"type": "tool_use"}])


async def test_tool_loop_stays_on_one_provider_even_if_other_is_cheaper(make_app, tmp_path):
    seq = {"anthropic": [tool_call(0), tool_call(1), "tayyor"], "gemini": ["boshqa"]}

    def mk(name):
        def h(system, user, model):
            if "durable facts" in system:
                return "{}"
            return seq[name].pop(0)
        return h
    app, provs = await make_app(lambda *a: "x", names=("anthropic", "gemini"), PRIMARY_PROVIDER="gemini")
    provs["anthropic"].handler, provs["gemini"].handler = mk("anthropic"), mk("gemini")
    # birinchi chaqiruv gemini'ga ketadi (asosiy) -> u asbobsiz javob beradi; sikl yo'q
    env = ToolEnv(workspace=tmp_path, store=app.store, settings=app.settings)
    assert await app.team.run_agent("developer", "x", env=env) == "boshqa"
    # asosiy = anthropic: butun sikl anthropic'da
    await app.store.set_kv("primary_provider", "anthropic")
    seq["gemini"].append("hech qachon")
    assert await app.team.run_agent("developer", "x", env=env) == "tayyor"
    assert provs["anthropic"].calls.count("claude-sonnet-5-5") == 3 and len(provs["gemini"].calls) == 1


async def test_failed_provider_mid_loop_restarts_whole_run_on_the_other(make_app, tmp_path):
    state = {"a": 0}

    def a_handler(system, user, model):
        state["a"] += 1
        return tool_call(0) if state["a"] == 1 else ProviderError("anthropic yiqildi")

    def g_handler(system, user, model):
        return "gemini bajardi"
    app, provs = await make_app(lambda *a: "x", names=("anthropic", "gemini"), PRIMARY_PROVIDER="anthropic")
    provs["anthropic"].handler, provs["gemini"].handler = a_handler, g_handler
    env = ToolEnv(workspace=tmp_path, store=app.store, settings=app.settings)
    assert await app.team.run_agent("developer", "x", env=env) == "gemini bajardi"
    first = provs["gemini"].msgs_seen[0]
    assert first == [{"role": "user", "content": "x"}]        # Gemini boshidan boshladi, Claude formatidagi xabarlarsiz
    audit = await app.store._all(__import__("sqlalchemy").text("select action from audit_log where action='provider_switch'"))
    assert audit


async def test_run_fails_cleanly_when_every_provider_dies_mid_loop(make_app, tmp_path):
    n = {"i": 0}

    def h(system, user, model):
        n["i"] += 1
        return tool_call(0) if n["i"] == 1 else ProviderError("down")
    app, _ = await make_app(h, names=("anthropic",))
    env = ToolEnv(workspace=tmp_path, store=app.store, settings=app.settings)
    with pytest.raises(BudgetExhausted):
        await app.team.run_agent("developer", "x", env=env)
    assert app.team.busy == {}


async def test_gemini_agent_tool_loop_end_to_end_with_real_provider_class(make_app, tmp_path):
    """Haqiqiy GeminiProvider + soxta HTTP: asbob chaqirish -> fayl yozish -> yakuniy javob."""
    calls = []

    def h(req):
        body = json.loads(req.content)
        calls.append(body)
        if len(calls) == 1:
            return gresp([{"functionCall": {"name": "write_file", "args": {"path": "n.txt", "content": "salom"}}, "thoughtSignature": "S"}])
        return gresp([{"text": "fayl yozildi"}])
    app, _ = await make_app(lambda *a: "x", names=("anthropic",))
    gem = GeminiProvider("k", gclient(h))
    app.router.providers = {"gemini": gem}
    env = ToolEnv(workspace=tmp_path, store=app.store, settings=app.settings, task_id=None)
    out = await app.team.run_agent("developer", "n.txt yarat", env=env)
    assert out == "fayl yozildi" and (tmp_path / "n.txt").read_text() == "salom"
    assert calls[1]["contents"][2]["parts"][0]["functionResponse"]["name"] == "write_file"
    assert calls[1]["contents"][1]["parts"][0]["thoughtSignature"] == "S"
