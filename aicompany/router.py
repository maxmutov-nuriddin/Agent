from __future__ import annotations

import dataclasses
import time

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
        self._tts_down: dict[str, float] = {}   # ovoz xizmati -> shu vaqtgacha (monotonic) dam oladi
        self._free_down: dict[str, float] = {}  # bepul provayder xato bergach shu vaqtgacha (monotonic) o'tkazib yuboriladi
        self.tts_last = ""                      # oxirgi muvaffaqiyatli ovoz xizmati

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

    FREE = ("groq", "openrouter")   # bepul AI'lar (tartib: avval Groq, tez va barqaror)
    FREE_COOLDOWN = 300

    async def free_on(self, name: str) -> bool:
        return await self.store.get_kv(f"{name}_on") == "1"

    async def free_status(self) -> dict:
        now = time.monotonic()
        return {n: {"ready": n in self.providers, "enabled": await self.free_on(n),
                    "cooldown_s": max(0, int(self._free_down.get(n, 0) - now)),
                    "model": self.s.providers[n].models["cheap"].id if n in self.s.providers else ""} for n in self.FREE}

    async def _candidates(self, tier, tools, only, exclude, free_ok=False):
        now = time.monotonic()
        free = []
        if free_ok and tier == "cheap" and not tools and only is None:
            free = [n for n in self.FREE if n in self.providers and n not in exclude
                    and await self.free_on(n) and now >= self._free_down.get(n, 0)]
        provs = [p for n, p in self.s.providers.items() if n in self.providers and n not in exclude
                 and (only is None or n == only) and (n not in self.FREE or n in free)]
        provs.sort(key=lambda p: p.models[tier].price_out)  # 'auto': eng arzoni birinchi
        prefer = self.s.tier_providers.get(tier) or await self.primary()
        if prefer in {p.name for p in provs}:  # tanlangani birinchi, qolganlari zaxira (arzonlik tartibida)
            provs.sort(key=lambda p: p.name != prefer)
        if tools:  # asbob qo'llaydigan provayderlar oldinda
            provs.sort(key=lambda p: not self.providers[p.name].supports_tools)
        if free:   # bepul modellar avval (yoqilgan tartibda); xato bersa, pullik zaxiralar ishlaydi
            provs.sort(key=lambda p: free.index(p.name) if p.name in free else len(free))
        return provs

    async def call(self, tier: str, system: str, messages: list[dict], *, task_id=None,
                   agent="?", use_cache=False, tools: list[dict] | None = None,
                   only: str | None = None, exclude: frozenset = frozenset(), free_ok: bool = False) -> LLMResult:
        if task_id is not None and await self.store.spent_task(task_id) >= self.s.max_task_usd:
            raise TaskBudgetExceeded(f"vazifa limiti {self.s.max_task_usd}$ tugadi")
        max_tokens = MAX_TOKENS[tier]
        key = cache_key(tier, system, messages) if use_cache and not tools else None
        if key and (hit := await self.store.cache_get(key)) is not None:
            return LLMResult(hit, 0, 0, 0, 0.0)

        candidates = await self._candidates(tier, tools, only, exclude, free_ok)
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
                    if pc.name in self.FREE:
                        self._free_down[pc.name] = time.monotonic() + self.FREE_COOLDOWN
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

    def has_audio(self) -> bool:
        return any(n in self.providers and self.providers[n].supports_audio for n in self.s.providers)

    async def transcribe(self, audio: bytes, mime: str, *, task_id=None) -> str:
        """Ovozni matnga aylantiradi: zanjir (Google → Azure → Whisper → Gemini) yoki Sozlamalarda tanlangan xizmat."""
        if len(audio) > 15_000_000:
            raise VoiceError("Ovoz fayli juda katta")
        from .stt import SpeechChain
        return await SpeechChain(self).transcribe(audio, mime, lambda a, m: self._transcribe_gemini(a, m, task_id=task_id))

    async def _transcribe_gemini(self, audio: bytes, mime: str, *, task_id=None) -> str:
        """Gemini ovozni ma'nosi bilan tushunadi (zanjirning oxirgi, eng aqlli bo'g'ini)."""
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

    async def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]] | None:
        """Ma'no bo'yicha qidiruv uchun vektorlar (Gemini). Kalit yo'q yoki xato: None (kalit so'z qidiruvi ishlaydi)."""
        if not texts:
            return []
        for name, prov in self.providers.items():
            if not hasattr(prov, "embed"):
                continue
            pc = self.s.providers.get(name)
            if pc and await self.store.spent(name) >= pc.budget_usd:
                return None
            try:
                vecs, cost = await prov.embed(texts, query=query)
            except ProviderError as e:
                await self.store.audit("router", "embed_error", str(e)[:300])
                return None
            await self.store.add_usage(name, "embedding", None, "memory", 0, 0, 0, cost)
            return vecs
        return None

    TTS_MODES = ("auto", "gemini", "edge")
    TTS_COOLDOWN = {"gemini": 120, "edge": 300}

    async def tts_mode(self) -> str:
        m = await self.store.get_kv("tts_mode")
        return m if m in self.TTS_MODES else "auto"

    async def tts_order(self) -> list[str]:
        """auto: Gemini, xato bo'lsa Edge. edge: Edge (bepul, tez), xato bo'lsa Gemini. gemini: faqat Gemini."""
        return {"auto": ["gemini", "edge"], "gemini": ["gemini"], "edge": ["edge", "gemini"]}[await self.tts_mode()]

    def _tts_ready(self, engine: str) -> bool:
        if engine == "gemini":
            return any(n in self.providers and hasattr(self.providers[n], "speak") for n in self.s.providers)
        from . import tts_edge
        return tts_edge.available()

    async def tts_status(self) -> dict:
        now = time.monotonic()
        eng = {}
        for e in ("gemini", "edge"):
            wait = max(0, int(self._tts_down.get(e, 0) - now))
            eng[e] = {"ready": self._tts_ready(e), "cooldown_s": wait}
        return {"mode": await self.tts_mode(), "order": await self.tts_order(), "last": self.tts_last, "engines": eng}

    async def tts_available(self) -> bool:
        return any(self._tts_ready(e) for e in await self.tts_order())

    async def speak(self, text: str, *, task_id=None, voice: str | None = None) -> bytes:
        return (await self.speak_ex(text, task_id=task_id, voice=voice))[0]

    async def speak_ex(self, text: str, *, task_id=None, voice: str | None = None) -> tuple[bytes, str]:
        """Matnni ovozga aylantiradi (PCM 24 kHz mono) va qaysi xizmat aytganini qaytaradi.
        Xizmat xato bersa, keyingisiga o'tadi va uni qisqa muddat (2-5 daqiqa) chetda ushlaydi: har gapda bekor urinmaydi."""
        order = [e for e in await self.tts_order() if self._tts_ready(e)]
        if not order:
            raise VoiceUnavailable("Ovoz bilan javob berish uchun GEMINI_API_KEY kerak (yoki edge-tts o'rnatilgan bo'lsin)")
        now = time.monotonic()
        order = [e for e in order if self._tts_down.get(e, 0) <= now] + [e for e in order if self._tts_down.get(e, 0) > now]
        voice = voice or await self.store.get_kv("tts_voice")
        errors = []
        for e in order:
            try:
                pcm = await (self._speak_gemini(text, task_id, voice) if e == "gemini" else self._speak_edge(text, voice))
            except (VoiceError, ProviderError) as ex:
                errors.append(f"{e}: {ex}")
                self._tts_down[e] = time.monotonic() + self.TTS_COOLDOWN[e]
                await self.store.audit("router", "tts_error", errors[-1][:500])
                continue
            self._tts_down.pop(e, None)
            self.tts_last = e
            return pcm, e
        raise VoiceError("Ovoz yaratib bo'lmadi (" + "; ".join(errors)[:300] + ")")

    async def _speak_edge(self, text: str, voice: str | None) -> bytes:
        from . import tts_edge
        try:
            return await tts_edge.synth(text[:1500], voice)
        except tts_edge.EdgeError as e:
            raise VoiceError(str(e)) from e

    async def _speak_gemini(self, text: str, task_id, voice: str | None) -> bytes:
        from .voices import resolve, shift_pitch
        cands = [p for n, p in self.s.providers.items() if n in self.providers and hasattr(self.providers[n], "speak")]
        last = ""
        for pc in cands:
            if await self.store.spent(pc.name) + 0.002 > pc.budget_usd:
                last = f"{pc.name}: limit"
                continue
            try:
                name, style, pitch = resolve(voice)
                pcm, cost = await self.providers[pc.name].speak(pc.models["cheap"], text[:1500], name, style)
                pcm = await shift_pitch(pcm, pitch)   # yetukroq, chuqurroq ohang
            except ProviderError as e:
                last = str(e)
                continue
            await self.store.add_usage(pc.name, "tts", task_id, "voice", 0, 0, 0, cost)
            return pcm
        raise VoiceError(last or "Gemini kaliti yo'q")

    async def _maybe_warn(self, pc, spent):
        if not self.on_warning or spent < pc.budget_usd * self.s.warn_ratio:
            return
        flag = f"warned:{pc.name}:{pc.budget_usd}"
        if await self.store.get_kv(flag):
            return
        await self.store.set_kv(flag, "1")
        await self.on_warning(f"⚠️ {pc.name}: {spent:.2f}$ / {pc.budget_usd:.2f}$ sarflandi (80%+)")
