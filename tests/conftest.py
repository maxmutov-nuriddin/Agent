import json

import pytest

from aicompany.app import build_app
from aicompany.config import load_settings
from aicompany.providers import LLMResult, Provider, ProviderError


class MockProvider(Provider):
    """Skriptlangan javoblar: handler(system, user_text, tier_model_id) -> str."""

    def __init__(self, name, handler, cost=0.01):
        self.name, self.handler, self.cost, self.calls = name, handler, cost, []

    async def complete(self, cfg, system, messages, max_tokens):
        self.calls.append(cfg.id)
        out = self.handler(system, messages[-1]["content"], cfg.id)
        if isinstance(out, Exception):
            raise out
        return LLMResult(out, 100, 50, 0, self.cost)


def settings(**env):
    base = {"ANTHROPIC_API_KEY": "x", "OPENAI_API_KEY": "x", "GEMINI_API_KEY": "x",
            "DATABASE_URL": "sqlite+aiosqlite:///:memory:", "MAX_TASK_USD": "1"}
    return load_settings({**base, **env})


@pytest.fixture
async def make_app():
    apps = []

    async def _make(handler, names=("anthropic",), **env):
        provs = {n: MockProvider(n, handler) for n in names}
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
        if "'ceo'" in system:
            return json.dumps(plan) if "Plan the work" in user else "FINAL DELIVERABLE"
        if "'qa'" in system:
            return json.dumps(qa_answers.pop(0) if len(qa_answers) > 1 else qa_answers[0])
        if "'hr'" in system:
            return json.dumps({"role": "SEO expert", "tier": "strong"})
        return f"output of {system.split(chr(39))[1]}"
    return handler
