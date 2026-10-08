from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
TIERS = ("cheap", "mid", "strong")
KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}


@dataclass(frozen=True)
class ModelCfg:
    id: str
    price_in: float
    price_out: float
    price_cache_read: float
    effort: str | None = None


@dataclass(frozen=True)
class ProviderCfg:
    name: str
    budget_usd: float
    models: dict[str, ModelCfg]
    api_key: str | None


@dataclass(frozen=True)
class Settings:
    providers: dict[str, ProviderCfg]
    database_url: str
    telegram_token: str | None
    owner_id: int | None
    max_task_usd: float
    max_agents: int
    max_parallel: int
    max_revisions: int
    warn_ratio: float = 0.8
    workspace_dir: Path = ROOT / "workspace"
    brave_key: str | None = None
    report_hour: int = 9
    report_tz: str = "Asia/Tashkent"
    max_tool_turns: int = 8
    command_timeout: int = 60


def load_settings(env: dict | None = None, models_path: Path | None = None) -> Settings:
    if env is None:
        load_dotenv(ROOT / ".env")
        env = os.environ
    raw = yaml.safe_load((models_path or ROOT / "models.yaml").read_text())["providers"]
    providers = {}
    for name, body in raw.items():
        models = {
            tier: ModelCfg(
                id=m["id"], price_in=float(m["in"]), price_out=float(m["out"]),
                price_cache_read=float(m.get("cache_read", m["in"])), effort=m.get("effort"),
            )
            for tier, m in body["models"].items()
        }
        providers[name] = ProviderCfg(
            name=name,
            budget_usd=float(env.get(f"BUDGET_USD_{name.upper()}", 5)),
            models=models,
            api_key=env.get(KEY_ENV[name]) or None,
        )
    owner = env.get("OWNER_TELEGRAM_ID")
    return Settings(
        providers=providers,
        database_url=env.get("DATABASE_URL", "sqlite+aiosqlite:///data/company.db"),
        telegram_token=env.get("TELEGRAM_BOT_TOKEN") or None,
        owner_id=int(owner) if owner else None,
        max_task_usd=float(env.get("MAX_TASK_USD", 1.0)),
        max_agents=int(env.get("MAX_AGENTS", 12)),
        max_parallel=int(env.get("MAX_PARALLEL_TASKS", 2)),
        max_revisions=int(env.get("MAX_REVISIONS", 1)),
        workspace_dir=Path(env.get("WORKSPACE_DIR", ROOT / "workspace")),
        brave_key=env.get("BRAVE_API_KEY") or None,
        report_hour=int(env.get("REPORT_HOUR", 9)),
        report_tz=env.get("REPORT_TZ", "Asia/Tashkent"),
        max_tool_turns=int(env.get("MAX_TOOL_TURNS", 8)),
        command_timeout=int(env.get("COMMAND_TIMEOUT", 60)),
    )
