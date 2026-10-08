from __future__ import annotations

import dataclasses

from .config import TIERS, Settings
from .db import Store, cache_key
from .providers import (LLMResult, MalformedCall, ModelNotFound, Provider, ProviderError, VoiceError, VoiceUnavailable,
                        suggest_model)

MAX_TOKENS = {"cheap": 4096, "mid": 6000, "strong": 8000}


class BudgetExhausted(Exception):
    """Barcha provayderlarning limiti tugagan."""


class TaskBudgetExceeded(Exception):
    """Bitta vazifa uchun ajratilgan limit tugagan."""


class PinnedUnavailable(Exception):
    """Ish o'rtasida tanlangan provayder javob bera olmadi; ishni boshqa provayderda qayta boshlash kerak."""

    def __init__(self, provider: str, detail: str):
        super().__init__(f"{provider}: {detail}")
        self.provider = provider


class Router:
    """Vazifa darajasiga (tier) qarab eng arzon ishlaydigan provayderni tanlaydi."""

    def __init__(self, settings: Settings, store: Store, providers: dict[str, Provider]):
        self.s, self.store, self.providers = settings, store, providers
        self.on_warning = None  # async callable(str)
        self._bad_models: set[tuple[str, str]] = set()  # (provayder, model) shu jarayonda topilmagan
        self._model_lists: dict[str, list[str]] = {}

    def _estimate(self, cfg, system, messages, max_tokens) -> float:
        in_tok = (len(system) + sum(len(str(m["content"])) for m in messages)) // 3
        return (in_tok * cfg.price_in + max_tokens * cfg.price_out) / 1_000_000

    async def status(self) -> dict[str, dict]:
        out = {}
        for name, p in self.s.providers.items():
            spent = await self.store.spent(name)
            out[name] = {"spent": spent, "budget": p.budget_usd, "enabled": name in self.providers}
        return out

    async def primary(self) -> str:
        """Asosiy provayder: panel/Telegramdagi tanlov (kv) > .env (PRIMARY_PROVIDER) > 'auto' (eng arzoni)."""
        return (await self.store.get_kv("primary_provider")) or self.s.primary_provider

    async def _warn(self, text: str):
        await self.store.audit("router", "model", text[:500])
        if self.on_warning:
            try:
                await self.on_warning(text)
            except Exception:  # noqa: BLE001 — ogohlantirish yetkazilmasa ham ish davom etadi
                pass

    async def _model_for(self, pc, tier):
        """Tier uchun model sozlamasi: avval topilgan tuzatish (kv), keyin models.yaml."""
        cfg = pc.models[tier]
        fixed = await self.store.get_kv(f"model:{pc.name}:{cfg.id}")
        return dataclasses.replace(cfg, id=fixed) if fixed else cfg

    async def _resolve_model(self, pc, cfg) -> str | None:
        """Model topilmasa: provayderning haqiqiy ro'yxatidan eng yaqinini tanlab, eslab qoladi."""
        prov = self.providers[pc.name]
        if pc.name not in self._model_lists:
            try:
                self._model_lists[pc.name] = await prov.list_models()
            except (ProviderError, NotImplementedError):
                self._model_lists[pc.name] = []
        found = suggest_model(cfg.id, self._model_lists[pc.name])
        if found and found != cfg.id:
            await self.store.set_kv(f"model:{pc.name}:{cfg.id}", found)
            await self._warn(f"⚙️ {pc.name}: '{cfg.id}' topilmadi, '{found}' ishlatiladi. models.yaml ni yangilang.")
            return found
        return None

    async def _lower_tier_cfg(self, pc, tier):
        """Yaqin nom topilmasa: shu provayderning ishlaydigan arzonroq modeliga tushamiz."""
        for lower in reversed(TIERS[:TIERS.index(tier)]):
            cfg = await self._model_for(pc, lower)
            if (pc.name, cfg.id) not in self._bad_models:
                return cfg
        return None

    async def _candidates(self, tier, tools, only, exclude):
        provs = [p for n, p in self.s.providers.items() if n in self.providers and n not in exclude
                 and (only is None or n == only)]
        provs.sort(key=lambda p: p.models[tier].price_out)  # 'auto': eng arzoni birinchi
        prefer = self.s.tier_providers.get(tier) or await self.primary()
        if prefer in {p.name for p in provs}:  # tanlangani birinchi, qolganlari zaxira (arzonlik tartibida)
            provs.sort(key=lambda p: p.name != prefer)
        if tools:  # asbob qo'llaydigan provayderlar oldinda
            provs.sort(key=lambda p: not self.providers[p.name].supports_tools)
        return provs

    async def call(self, tier: str, system: str, messages: list[dict], *, task_id=None,
                   agent="?", use_cache=False, tools: list[dict] | None = None,
                   only: str | None = None, exclude: frozenset = frozenset()) -> LLMResult:
        if task_id is not None and await self.store.spent_task(task_id) >= self.s.max_task_usd:
            raise TaskBudgetExceeded(f"vazifa limiti {self.s.max_task_usd}$ tugadi")
        max_tokens = MAX_TOKENS[tier]
        key = cache_key(tier, system, messages) if use_cache and not tools else None
        if key and (hit := await self.store.cache_get(key)) is not None:
            return LLMResult(hit, 0, 0, 0, 0.0)

        candidates = await self._candidates(tier, tools, only, exclude)
        errors = []
        for pc in candidates:
            cfg = await self._model_for(pc, tier)
            if (pc.name, cfg.id) in self._bad_models:
                cfg = await self._lower_tier_cfg(pc, tier)
                if cfg is None:
                    errors.append(f"{pc.name}: model topilmadi")
                    continue
            spent = await self.store.spent(pc.name)
            if spent + self._estimate(cfg, system, messages, max_tokens) > pc.budget_usd:
                errors.append(f"{pc.name}: limit")
                continue
            prov = self.providers[pc.name]
            res = None
            for attempt in range(3):  # model nomi tuzatilsa yoki pastroq model olinsa, qayta urinamiz
                try:
                    res = await prov.complete(cfg, system, messages, max_tokens, tools if prov.supports_tools else None)
                    break
                except ModelNotFound as e:
                    self._bad_models.add((pc.name, cfg.id))
                    new_id = await self._resolve_model(pc, cfg)
                    if new_id:
                        cfg = dataclasses.replace(cfg, id=new_id)
                        continue
                    lower = await self._lower_tier_cfg(pc, tier)
                    if lower is None:
                        errors.append(f"{e} (python -m aicompany check)")
                        break
                    await self._warn(f"⚙️ {pc.name}: '{cfg.id}' topilmadi, '{lower.id}' ishlatiladi. "
                                     "To'g'ri nomni `python -m aicompany check` ko'rsatadi.")
                    cfg = lower
                except MalformedCall:
                    raise  # chaqiruvchi (agent sikli) qisqaroq so'rab qayta urinadi
                except ProviderError as e:
                    errors.append(str(e))
                    await self.store.audit("router", "provider_error", str(e)[:500])
                    break
            if res is None:
                continue
            res.provider = pc.name
            await self.store.add_usage(pc.name, cfg.id, task_id, agent, res.tokens_in,
                                       res.tokens_out, res.tokens_cached, res.cost_usd)
            await self._maybe_warn(pc, spent + res.cost_usd)
            if key:
                await self.store.cache_put(key, res.text)
            return res
        if only:  # ish o'rtasida: qayta boshlash uchun maxsus xato
            raise PinnedUnavailable(only, "; ".join(errors) or "ulanmagan")
        if not self.providers:
            raise BudgetExhausted("hech qaysi AI kaliti ulanmagan: GEMINI_API_KEY yoki ANTHROPIC_API_KEY ni Render Environment (yoki .env) ga qo'ying")
        raise BudgetExhausted("; ".join(errors) or "provayder ulanmagan")

    async def transcribe(self, audio: bytes, mime: str, *, task_id=None) -> str:
        """Ovozni matnga aylantiradi (hozircha faqat Gemini audio tushunadi)."""
        if len(audio) > 15_000_000:
            raise VoiceError("Ovoz fayli juda katta")
        cands = [p for n, p in self.s.providers.items() if n in self.providers and self.providers[n].supports_audio]
        if not cands:
            raise VoiceUnavailable("Ovozni tushunish uchun GEMINI_API_KEY kerak")
        last = ""
        for pc in cands:
            cfg = pc.models["cheap"]
            est = ((len(audio) // 500) * cfg.price_in + 2048 * cfg.price_out) / 1_000_000  # ~32 token/soniya
            if await self.store.spent(pc.name) + est > pc.budget_usd:
                last = f"{pc.name}: limit"
                continue
            try:
                res = await self.providers[pc.name].transcribe(cfg, audio, mime)
            except ProviderError as e:
                last = str(e)
                await self.store.audit("router", "voice_error", last[:500])
                continue
            await self.store.add_usage(pc.name, cfg.id, task_id, "voice", res.tokens_in, res.tokens_out, 0, res.cost_usd)
            text = res.text.strip()
            if not text or "[no speech]" in text.lower():
                raise VoiceError("Ovozda gap topilmadi")
            return text
        raise VoiceError(f"Ovozni matnga aylantirib bo'lmadi ({last})")

    async def speak(self, text: str, *, task_id=None) -> bytes:
        """Matnni ovozga aylantiradi (PCM 24 kHz mono). Faqat Gemini."""
        cands = [p for n, p in self.s.providers.items() if n in self.providers and hasattr(self.providers[n], "speak")]
        if not cands:
            raise VoiceUnavailable("Ovoz bilan javob berish uchun GEMINI_API_KEY kerak")
        last = ""
        for pc in cands:
            if await self.store.spent(pc.name) + 0.002 > pc.budget_usd:
                last = f"{pc.name}: limit"
                continue
            try:
                pcm, cost = await self.providers[pc.name].speak(pc.models["cheap"], text[:1500])
            except ProviderError as e:
                last = str(e)
                await self.store.audit("router", "tts_error", last[:500])
                continue
            await self.store.add_usage(pc.name, "tts", task_id, "voice", 0, 0, 0, cost)
            return pcm
        raise VoiceError(f"Ovoz yaratib bo'lmadi ({last})")

    async def _maybe_warn(self, pc, spent):
        if not self.on_warning or spent < pc.budget_usd * self.s.warn_ratio:
            return
        flag = f"warned:{pc.name}:{pc.budget_usd}"
        if await self.store.get_kv(flag):
            return
        await self.store.set_kv(flag, "1")
        await self.on_warning(f"⚠️ {pc.name}: {spent:.2f}$ / {pc.budget_usd:.2f}$ sarflandi (80%+)")
