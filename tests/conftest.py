import json
import tempfile
from pathlib import Path

import pytest

from aicompany.app import build_app
from aicompany.config import load_settings
from aicompany.providers import LLMResult, Provider, ProviderError


class MockProvider(Provider):
    """Skriptlangan javoblar: handler(system, user_text, tier_model_id) -> str."""

    supports_tools = True

    def __init__(self, name, handler, cost=0.01, audio=None):
        self.name, self.handler, self.cost, self.calls = name, handler, cost, []
        self.tool_seen = []
        self.msgs_seen = []
        self.supports_audio = audio is not None
        self.models_list, self.list_calls = [], 0
        self.audio_text, self.audio_calls = audio, []

    async def list_models(self):
        self.list_calls += 1
        return list(self.models_list)

    async def transcribe(self, cfg, audio, mime):
        self.audio_calls.append((cfg.id, mime, len(audio)))
        return LLMResult(self.audio_text, 500, 20, 0, self.cost)

    async def complete(self, cfg, system, messages, max_tokens, tools=None):
        self.calls.append(cfg.id)
        self.tool_seen.append([t["name"] for t in tools or []])
        self.msgs_seen.append(list(messages))
        out = self.handler(system, messages[-1]["content"], cfg.id)
        if isinstance(out, Exception):
            raise out
        if isinstance(out, LLMResult):
            out.cost_usd = out.cost_usd or self.cost
            return out
        return LLMResult(out, 100, 50, 0, self.cost)


def settings(**env):
    base = {"ANTHROPIC_API_KEY": "x", "OPENAI_API_KEY": "x", "GEMINI_API_KEY": "x",
            "DATABASE_URL": "sqlite+aiosqlite:///" + tempfile.mkdtemp(prefix="aic_db_") + "/t.db", "MAX_TASK_USD": "1",
            "WORKSPACE_DIR": tempfile.mkdtemp(prefix="aic_ws_")}
    return load_settings({**base, **env}, models_path=Path(__file__).parent / "models_test.yaml")


@pytest.fixture
async def make_app():
    apps = []

    async def _make(handler, names=("anthropic",), audio=None, **env):
        provs = {n: MockProvider(n, handler, audio=audio if n == "gemini" else None) for n in names}
        app = await build_app(settings(**env), provs)
        apps.append(app)
        return app, provs

    yield _make
    for a in apps:
        await a.store.close()


def scripted_company(plan=None, qa=None):
    """Oddiy kompaniya: ceo reja/yakun, qa tekshiruv, qolganlar o'z ishini bajaradi."""
    plan = plan or {"summary": "test", "new_roles": [], "steps": [
        {"id": "s1", "agent": "researcher", "task": "research", "depends_on": []},
        {"id": "s2", "agent": "marketer", "task": "write", "depends_on": ["s1"]}]}
    qa_answers = list(qa or [{"verdict": "pass", "issues": []}])

    def handler(system, user, model):
        if "front desk" in system or "in a CHAT" in system:
            latest = user.split("# Latest owner message\n")[-1]
            return json.dumps({"mode": "task", "reply": "Boshladim", "task": latest})
        if "durable facts" in system:
            return json.dumps({"facts": ["Owner prefers Uzbek"]})
        if "'ceo'" in system:
            return json.dumps(plan) if "Plan the work" in user else "FINAL DELIVERABLE"
        if "'qa'" in system:
            return json.dumps(qa_answers.pop(0) if len(qa_answers) > 1 else qa_answers[0])
        if "'hr'" in system:
            return json.dumps({"role": "SEO expert", "tier": "strong"})
        return f"output of {system.split(chr(39))[1]}"
    return handler
