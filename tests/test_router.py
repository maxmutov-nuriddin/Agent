import pytest

from aicompany.providers import ProviderError, compute_cost
from aicompany.router import BudgetExhausted, TaskBudgetExceeded
from aicompany.config import ModelCfg

from .conftest import scripted_company


async def test_cheapest_provider_is_chosen_first(make_app):
    app, provs = await make_app(lambda *a: "ok", names=("anthropic", "openai", "gemini"))
    await app.router.call("cheap", "sys", [{"role": "user", "content": "hi"}])
    # cheap tier out-narxlar: anthropic 0.50 < openai 1.20 < gemini 1.50
    assert len(provs["anthropic"].calls) == 1 and not provs["openai"].calls


async def test_fallback_on_provider_error(make_app):
    def handler(system, user, model):
        return ProviderError("boom") if model.startswith("claude") else "from-other"
    app, provs = await make_app(handler, names=("anthropic", "openai"))
    res = await app.router.call("cheap", "s", [{"role": "user", "content": "hi"}])
    assert res.text == "from-other"


async def test_provider_over_budget_is_skipped(make_app):
    app, provs = await make_app(lambda *a: "ok", names=("anthropic", "openai"), BUDGET_USD_ANTHROPIC="0.01")
    await app.store.add_usage("anthropic", "m", None, "x", 1, 1, 0, 0.0099)
    await app.router.call("cheap", "s", [{"role": "user", "content": "hi"}])
    assert not provs["anthropic"].calls and len(provs["openai"].calls) == 1


async def test_all_budgets_exhausted(make_app):
    app, _ = await make_app(lambda *a: "ok", names=("anthropic",), BUDGET_USD_ANTHROPIC="0.0001")
    with pytest.raises(BudgetExhausted):
        await app.router.call("cheap", "s", [{"role": "user", "content": "hi"}])


async def test_task_budget_cap(make_app):
    app, _ = await make_app(lambda *a: "ok", MAX_TASK_USD="0.015")
    tid = await app.store.create_task(0, "r")
    await app.router.call("cheap", "s", [{"role": "user", "content": "a"}], task_id=tid)
    await app.router.call("cheap", "s", [{"role": "user", "content": "b"}], task_id=tid)
    with pytest.raises(TaskBudgetExceeded):
        await app.router.call("cheap", "s", [{"role": "user", "content": "c"}], task_id=tid)


async def test_cache_avoids_second_call(make_app):
    app, provs = await make_app(lambda *a: "ok")
    for _ in range(2):
        await app.router.call("cheap", "s", [{"role": "user", "content": "same"}], use_cache=True)
    assert len(provs["anthropic"].calls) == 1


async def test_warning_fires_once(make_app):
    app, _ = await make_app(lambda *a: "ok", BUDGET_USD_ANTHROPIC="0.0125")
    msgs = []

    async def warn(t):
        msgs.append(t)
    app.router.on_warning = warn
    for i in range(2):
        await app.router.call("cheap", "s", [{"role": "user", "content": str(i)}])
    assert len(msgs) == 1


def test_compute_cost():
    cfg = ModelCfg("m", 2.0, 10.0, 0.2)
    assert compute_cost(cfg, 1_000_000, 100_000, 500_000) == pytest.approx(2 + 1 + 0.1)
