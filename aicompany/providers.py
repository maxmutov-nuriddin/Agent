from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from .config import ModelCfg


class ProviderError(Exception):
    """Provayder xatosi: router keyingi provayderga o'tadi."""


@dataclass
class LLMResult:
    text: str
    tokens_in: int      # keshdan tashqari kirish
    tokens_out: int     # fikrlash tokenlari bilan
    tokens_cached: int  # keshdan o'qilgan
    cost_usd: float = 0.0


def compute_cost(cfg: ModelCfg, tin: int, tout: int, tcached: int, cache_write: int = 0) -> float:
    per = 1_000_000
    return (tin * cfg.price_in + tout * cfg.price_out + tcached * cfg.price_cache_read
            + cache_write * cfg.price_in * 1.25) / per


class Provider:
    name = "base"

    async def complete(self, cfg: ModelCfg, system: str, messages: list[dict], max_tokens: int) -> LLMResult:
        raise NotImplementedError

    async def list_models(self) -> list[str]:
        raise NotImplementedError


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, api_key: str):
        import anthropic
        self._anthropic = anthropic
        self.client = anthropic.AsyncAnthropic(api_key=api_key)

    async def complete(self, cfg, system, messages, max_tokens):
        kwargs = {}
        if cfg.effort:
            kwargs["output_config"] = {"effort": cfg.effort}
        try:
            r = await self.client.messages.create(
                model=cfg.id, max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=messages, **kwargs)
        except self._anthropic.APIError as e:
            raise ProviderError(f"anthropic: {e}") from e
        if r.stop_reason == "refusal":
            raise ProviderError("anthropic: model refused")
        text = "".join(b.text for b in r.content if b.type == "text")
        u = r.usage
        cached = getattr(u, "cache_read_input_tokens", 0) or 0
        written = getattr(u, "cache_creation_input_tokens", 0) or 0
        cost = compute_cost(cfg, u.input_tokens, u.output_tokens, cached, written)
        return LLMResult(text, u.input_tokens + written, u.output_tokens, cached, cost)

    async def list_models(self):
        try:
            page = await self.client.models.list(limit=100)
            return [m.id async for m in page]
        except self._anthropic.APIError as e:
            raise ProviderError(f"anthropic: {e}") from e


class _HttpProvider(Provider):
    base = ""

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None):
        self.api_key = api_key
        self.http = client or httpx.AsyncClient(timeout=120)

    def _headers(self) -> dict:
        raise NotImplementedError

    async def _request(self, method: str, url: str, **kw) -> dict:
        last = ""
        for attempt in range(3):
            try:
                r = await self.http.request(method, url, headers=self._headers(), **kw)
            except httpx.HTTPError as e:
                last = str(e)
            else:
                if r.status_code < 400:
                    return r.json()
                last = f"{r.status_code} {r.text[:300]}"
                if r.status_code not in (408, 429) and r.status_code < 500:
                    break
            await asyncio.sleep(2 ** attempt)
        raise ProviderError(f"{self.name}: {last}")


class OpenAIProvider(_HttpProvider):
    name = "openai"
    base = "https://api.openai.com/v1"

    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    async def complete(self, cfg, system, messages, max_tokens):
        body = {"model": cfg.id, "max_completion_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, *messages]}
        data = await self._request("POST", f"{self.base}/chat/completions", json=body)
        try:
            text = data["choices"][0]["message"].get("content") or ""
            u = data["usage"]
        except (KeyError, IndexError) as e:
            raise ProviderError(f"openai: unexpected response {str(data)[:200]}") from e
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
        tin = u["prompt_tokens"] - cached
        tout = u["completion_tokens"]
        return LLMResult(text, tin, tout, cached, compute_cost(cfg, tin, tout, cached))

    async def list_models(self):
        data = await self._request("GET", f"{self.base}/models")
        return [m["id"] for m in data.get("data", [])]


class GeminiProvider(_HttpProvider):
    name = "gemini"
    base = "https://generativelanguage.googleapis.com/v1beta"

    def _headers(self):
        return {"x-goog-api-key": self.api_key}

    async def complete(self, cfg, system, messages, max_tokens):
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "model" if m["role"] == "assistant" else "user",
                          "parts": [{"text": m["content"]}]} for m in messages],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        data = await self._request("POST", f"{self.base}/models/{cfg.id}:generateContent", json=body)
        try:
            cand = data["candidates"][0]
            text = "".join(p.get("text", "") for p in cand["content"]["parts"])
        except (KeyError, IndexError) as e:
            raise ProviderError(f"gemini: no content ({str(data)[:200]})") from e
        u = data.get("usageMetadata", {})
        cached = u.get("cachedContentTokenCount", 0) or 0
        tin = u.get("promptTokenCount", 0) - cached
        tout = (u.get("candidatesTokenCount", 0) or 0) + (u.get("thoughtsTokenCount", 0) or 0)
        return LLMResult(text, tin, tout, cached, compute_cost(cfg, tin, tout, cached))

    async def list_models(self):
        data = await self._request("GET", f"{self.base}/models", params={"pageSize": 200})
        return [m["name"].removeprefix("models/") for m in data.get("models", [])]


def build_providers(settings) -> dict[str, Provider]:
    classes = {"anthropic": AnthropicProvider, "openai": OpenAIProvider, "gemini": GeminiProvider}
    return {n: classes[n](p.api_key) for n, p in settings.providers.items() if p.api_key}
