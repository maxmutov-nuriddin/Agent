import json

import httpx
import httpx2
import pytest

from aicompany.config import ModelCfg
from aicompany.providers import AnthropicProvider, GeminiProvider, OpenAIProvider, ProviderError

CFG = ModelCfg("m", 1.0, 2.0, 0.1)


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_openai_parsing_and_cached_split():
    def h(req):
        assert req.headers["authorization"] == "Bearer k"
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}],
                                         "usage": {"prompt_tokens": 1000, "completion_tokens": 100,
                                                   "prompt_tokens_details": {"cached_tokens": 400}}})
    r = await OpenAIProvider("k", client(h)).complete(CFG, "s", [{"role": "user", "content": "x"}], 100)
    assert r.text == "hi" and r.tokens_in == 600 and r.tokens_cached == 400
    assert r.cost_usd == pytest.approx((600 * 1.0 + 100 * 2.0 + 400 * 0.1) / 1e6)


async def test_gemini_parsing_counts_thinking_as_output():
    def h(req):
        assert req.headers["x-goog-api-key"] == "k"
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "a"}, {"text": "b"}]}}],
            "usageMetadata": {"promptTokenCount": 500, "candidatesTokenCount": 50, "thoughtsTokenCount": 150}})
    r = await GeminiProvider("k", client(h)).complete(CFG, "s", [{"role": "user", "content": "x"}], 100)
    assert r.text == "ab" and r.tokens_out == 200


async def test_http_error_raises_provider_error():
    p = OpenAIProvider("k", client(lambda req: httpx.Response(401, text="bad key")))
    with pytest.raises(ProviderError):
        await p.complete(CFG, "s", [{"role": "user", "content": "x"}], 10)


async def test_gemini_empty_response_is_error():
    p = GeminiProvider("k", client(lambda req: httpx.Response(200, json={"candidates": [{"finishReason": "SAFETY"}]})))
    with pytest.raises(ProviderError):
        await p.complete(CFG, "s", [{"role": "user", "content": "x"}], 10)


def aclient(handler):
    # anthropic SDK 1.x httpx2 ishlatadi
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


def anthropic_msg(content, stop="end_turn", usage=None):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5", "content": content,
            "stop_reason": stop, "stop_sequence": None,
            "usage": usage or {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 50,
                               "cache_creation_input_tokens": 10}}


async def test_anthropic_request_shape_and_cost():
    seen = {}

    def h(req):
        seen["body"] = json.loads(req.content)
        seen["path"] = req.url.path
        return httpx2.Response(200, json=anthropic_msg([{"type": "text", "text": "salom"}]))
    cfg = ModelCfg("claude-haiku-5-5", 0.10, 0.50, 0.01, effort="low")
    tools = [{"name": "t", "description": "d", "input_schema": {"type": "object", "properties": {}}}]
    p = AnthropicProvider("k", aclient(h))
    r = await p.complete(cfg, "SYS", [{"role": "user", "content": "hi"}], 500, tools)
    b = seen["body"]
    assert seen["path"] == "/v1/messages" and b["model"] == "claude-haiku-5-5" and b["max_tokens"] == 500
    assert b["system"] == [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}]
    assert b["output_config"] == {"effort": "low"} and b["tools"] == tools
    assert "temperature" not in b and "thinking" not in b and "tool_choice" not in b
    assert r.text == "salom" and r.tokens_cached == 50 and r.tokens_in == 110 and r.tool_calls == []
    assert r.cost_usd == pytest.approx((100 * 0.10 + 20 * 0.50 + 50 * 0.01 + 10 * 0.10 * 1.25) / 1e6)


async def test_anthropic_tool_use_and_roundtrip():
    bodies = []

    def h(req):
        bodies.append(json.loads(req.content))
        if len(bodies) == 1:
            return httpx2.Response(200, json=anthropic_msg(
                [{"type": "text", "text": "ok"}, {"type": "tool_use", "id": "tu_1", "name": "t", "input": {"a": 1}}],
                "tool_use"))
        return httpx2.Response(200, json=anthropic_msg([{"type": "text", "text": "final"}]))
    cfg = ModelCfg("claude-haiku-5-5", 0.1, 0.5, 0.01)
    p = AnthropicProvider("k", aclient(h))
    msgs = [{"role": "user", "content": "go"}]
    r1 = await p.complete(cfg, "S", msgs, 100, [{"name": "t", "description": "d", "input_schema": {"type": "object"}}])
    assert r1.tool_calls == [{"id": "tu_1", "name": "t", "input": {"a": 1}}]
    msgs += [{"role": "assistant", "content": r1.raw_content},
             {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": "res"}]}]
    r2 = await p.complete(cfg, "S", msgs, 100, [{"name": "t", "description": "d", "input_schema": {"type": "object"}}])
    assert r2.text == "final"
    sent = bodies[1]["messages"]
    assert sent[1]["content"][1]["type"] == "tool_use" and sent[1]["content"][1]["id"] == "tu_1"
    assert sent[2]["content"][0]["tool_use_id"] == "tu_1"


async def test_anthropic_refusal_and_api_error_become_provider_errors():
    cfg = ModelCfg("m", 1, 1, 1)
    p = AnthropicProvider("k", aclient(lambda r: httpx2.Response(200, json=anthropic_msg([], "refusal"))))
    with pytest.raises(ProviderError):
        await p.complete(cfg, "S", [{"role": "user", "content": "x"}], 10)
    err = {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}
    p = AnthropicProvider("k", aclient(lambda r: httpx2.Response(400, json=err)))
    with pytest.raises(ProviderError):
        await p.complete(cfg, "S", [{"role": "user", "content": "x"}], 10)
