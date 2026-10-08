from __future__ import annotations

from .config import Settings
from .db import Store, cache_key
from .providers import LLMResult, Provider, ProviderError

MAX_TOKENS = {"cheap": 4096, "mid": 6000, "strong": 8000}


class BudgetExhausted(Exception):
    """Barcha provayderlarning limiti tugagan."""


class TaskBudgetExceeded(Exception):
    """Bitta vazifa uchun ajratilgan limit tugagan."""


class Router:
    """Vazifa darajasiga (tier) qarab eng arzon ishlaydigan provayderni tanlaydi."""

    def __init__(self, settings: Settings, store: Store, providers: dict[str, Provider]):
        self.s, self.store, self.providers = settings, store, providers
        self.on_warning = None  # async callable(str)

    def _estimate(self, cfg, system, messages, max_tokens) -> float:
        in_tok = (len(system) + sum(len(str(m["content"])) for m in messages)) // 3
        return (in_tok * cfg.price_in + max_tokens * cfg.price_out) / 1_000_000

    async def status(self) -> dict[str, dict]:
        out = {}
        for name, p in self.s.providers.items():
            spent = await self.store.spent(name)
            out[name] = {"spent": spent, "budget": p.budget_usd, "enabled": name in self.providers}
        return out

    async def call(self, tier: str, system: str, messages: list[dict], *, task_id=None,
                   agent="?", use_cache=False, tools: list[dict] | None = None) -> LLMResult:
        if task_id is not None and await self.store.spent_task(task_id) >= self.s.max_task_usd:
            raise TaskBudgetExceeded(f"vazifa limiti {self.s.max_task_usd}$ tugadi")
        max_tokens = MAX_TOKENS[tier]
        key = cache_key(tier, system, messages) if use_cache and not tools else None
        if key and (hit := await self.store.cache_get(key)) is not None:
            return LLMResult(hit, 0, 0, 0, 0.0)

        candidates = sorted(
            (p for n, p in self.s.providers.items() if n in self.providers),
            key=lambda p: p.models[tier].price_out)
        if tools:  # asbob qo'llaydigan provayderlar birinchi; qolganlari asbobsiz javob beradi
            candidates.sort(key=lambda p: not self.providers[p.name].supports_tools)
        errors = []
        for pc in candidates:
            cfg = pc.models[tier]
            spent = await self.store.spent(pc.name)
            if spent + self._estimate(cfg, system, messages, max_tokens) > pc.budget_usd:
                errors.append(f"{pc.name}: limit")
                continue
            try:
                prov = self.providers[pc.name]
                res = await prov.complete(cfg, system, messages, max_tokens,
                                          tools if prov.supports_tools else None)
            except ProviderError as e:
                errors.append(str(e))
                await self.store.audit("router", "provider_error", str(e)[:500])
                continue
            await self.store.add_usage(pc.name, cfg.id, task_id, agent, res.tokens_in,
                                       res.tokens_out, res.tokens_cached, res.cost_usd)
            await self._maybe_warn(pc, spent + res.cost_usd)
            if key:
                await self.store.cache_put(key, res.text)
            return res
        raise BudgetExhausted("; ".join(errors) or "provayder ulanmagan")

    async def _maybe_warn(self, pc, spent):
        if not self.on_warning or spent < pc.budget_usd * self.s.warn_ratio:
            return
        flag = f"warned:{pc.name}:{pc.budget_usd}"
        if await self.store.get_kv(flag):
            return
        await self.store.set_kv(flag, "1")
        await self.on_warning(f"⚠️ {pc.name}: {spent:.2f}$ / {pc.budget_usd:.2f}$ sarflandi (80%+)")
