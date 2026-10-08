import httpx
import pytest

from aicompany.config import ModelCfg
from aicompany.providers import GeminiProvider, OpenAIProvider, ProviderError

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
