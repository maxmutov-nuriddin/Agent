from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from typing import Any

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
    tool_calls: list = field(default_factory=list)  # [{"id", "name", "input"}]
    raw_content: Any = None  # asistent xabari (asboblar sikli uchun)
    provider: str = ""      # natijani qaysi provayder bergan


def compute_cost(cfg: ModelCfg, tin: int, tout: int, tcached: int, cache_write: int = 0) -> float:
    per = 1_000_000
    return (tin * cfg.price_in + tout * cfg.price_out + tcached * cfg.price_cache_read
            + cache_write * cfg.price_in * 1.25) / per


class VoiceError(Exception):
    """Ovozni matnga aylantirib bo'lmadi."""


class VoiceUnavailable(VoiceError):
    """Ovozni tushunadigan provayder (Gemini kaliti) ulanmagan."""


class Provider:
    name = "base"
    supports_tools = False
    supports_audio = False

    async def complete(self, cfg: ModelCfg, system: str, messages: list[dict], max_tokens: int,
                       tools: list[dict] | None = None) -> LLMResult:
        raise NotImplementedError

    async def list_models(self) -> list[str]:
        raise NotImplementedError


class AnthropicProvider(Provider):
    name = "anthropic"
    supports_tools = True

    def __init__(self, api_key: str, http_client: httpx.AsyncClient | None = None):
        import anthropic
        self._anthropic = anthropic
        self.client = anthropic.AsyncAnthropic(api_key=api_key, http_client=http_client)

    async def complete(self, cfg, system, messages, max_tokens, tools=None):
        kwargs = {}
        if cfg.effort:
            kwargs["output_config"] = {"effort": cfg.effort}
        if tools:
            kwargs["tools"] = tools
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
        calls = []
        if r.stop_reason == "tool_use":
            calls = [{"id": b.id, "name": b.name, "input": b.input} for b in r.content if b.type == "tool_use"]
        return LLMResult(text, u.input_tokens + written, u.output_tokens, cached, cost, calls, r.content)

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

    async def complete(self, cfg, system, messages, max_tokens, tools=None):
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


def _gemini_schema(node):
    """JSON Schema -> Gemini qabul qiladigan to'plam (additionalProperties va h.k. olib tashlanadi)."""
    if isinstance(node, dict):
        return {k: _gemini_schema(v) for k, v in node.items() if k not in ("additionalProperties", "$schema", "title")}
    if isinstance(node, list):
        return [_gemini_schema(v) for v in node]
    return node


@dataclass
class GeminiTurn:
    """Gemini asistent xabari: 'thoughtSignature'lar o'zgarishsiz qaytarilishi shart (Gemini 3 asboblari)."""
    content: dict
    calls: list  # [{"id", "name"}] tartib bilan


class GeminiProvider(_HttpProvider):
    name = "gemini"
    base = "https://generativelanguage.googleapis.com/v1beta"
    supports_tools = True
    supports_audio = True

    def _headers(self):
        return {"x-goog-api-key": self.api_key}

    @staticmethod
    def _contents(messages: list[dict]) -> list[dict]:
        out, last_calls = [], []
        for m in messages:
            c = m["content"]
            if isinstance(c, GeminiTurn):
                out.append(c.content)
                last_calls = list(c.calls)
            elif isinstance(c, str):
                out.append({"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": c}]})
            else:  # asbob natijalari (+ matn): oxirgi asistent xabaridagi chaqiruvlar tartibi bo'yicha
                parts, results = [], [b for b in c if b.get("type") == "tool_result"]
                names = {call["id"]: call["name"] for call in last_calls}
                for b in results:
                    key = "error" if b.get("is_error") else "result"
                    parts.append({"functionResponse": {"name": names.get(b["tool_use_id"], "tool"),
                                                       "response": {key: b["content"]}}})
                parts += [{"text": b["text"]} for b in c if b.get("type") == "text"]
                out.append({"role": "user", "parts": parts})
        return out

    async def complete(self, cfg, system, messages, max_tokens, tools=None):
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": self._contents(messages),
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        if tools:
            decls = []
            for t in tools:
                d = {"name": t["name"], "description": t["description"]}
                params = _gemini_schema(t["input_schema"])
                if params.get("properties"):  # bo'sh parametrli obyektni Gemini rad etadi
                    d["parameters"] = params
                decls.append(d)
            body["tools"] = [{"functionDeclarations": decls}]
        data = await self._request("POST", f"{self.base}/models/{cfg.id}:generateContent", json=body)
        try:
            cand = data["candidates"][0]
            content = cand["content"]
            parts = content.get("parts", [])
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"gemini: no content ({str(data)[:200]})") from e
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        calls = [{"id": f"g{i}", "name": p["functionCall"]["name"], "input": p["functionCall"].get("args") or {}}
                 for i, p in enumerate(x for x in parts if "functionCall" in x)]
        if not text and not calls:
            raise ProviderError(f"gemini: empty answer (finish={cand.get('finishReason')})")
        u = data.get("usageMetadata", {})
        cached = u.get("cachedContentTokenCount", 0) or 0
        tin = u.get("promptTokenCount", 0) - cached
        tout = (u.get("candidatesTokenCount", 0) or 0) + (u.get("thoughtsTokenCount", 0) or 0)
        turn = GeminiTurn({"role": "model", "parts": parts}, [{"id": c["id"], "name": c["name"]} for c in calls])
        return LLMResult(text, tin, tout, cached, compute_cost(cfg, tin, tout, cached), calls, turn)

    async def transcribe(self, cfg, audio: bytes, mime: str) -> LLMResult:
        prompt = ("Transcribe this voice message exactly as spoken, in its original language (Uzbek, Russian or "
                  "English). Output ONLY the transcript, no commentary or translation. If there is no intelligible "
                  "speech, output exactly: [no speech]")
        body = {"contents": [{"role": "user", "parts": [
                    {"text": prompt}, {"inline_data": {"mime_type": mime, "data": base64.b64encode(audio).decode()}}]}],
                "generationConfig": {"maxOutputTokens": 2048}}
        data = await self._request("POST", f"{self.base}/models/{cfg.id}:generateContent", json=body)
        try:
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"]).strip()
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"gemini: no transcript ({str(data)[:200]})") from e
        u = data.get("usageMetadata", {})
        tin, tout = u.get("promptTokenCount", 0), (u.get("candidatesTokenCount", 0) or 0) + (u.get("thoughtsTokenCount", 0) or 0)
        return LLMResult(text, tin, tout, 0, compute_cost(cfg, tin, tout, 0))

    async def list_models(self):
        data = await self._request("GET", f"{self.base}/models", params={"pageSize": 200})
        return [m["name"].removeprefix("models/") for m in data.get("models", [])]


def build_providers(settings) -> dict[str, Provider]:
    classes = {"anthropic": AnthropicProvider, "openai": OpenAIProvider, "gemini": GeminiProvider}
    return {n: classes[n](p.api_key) for n, p in settings.providers.items() if p.api_key}
