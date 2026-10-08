from __future__ import annotations

import os
from dataclasses import dataclass, field
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
    web_host: str = "127.0.0.1"
    web_port: int = 8080
    web_token: str | None = None
    widget_token: str | None = None
    web_public_url: str | None = None
    primary_provider: str = "auto"
    google_maps_key: str | None = None
    tg_api_id: int | None = None
    tg_api_hash: str | None = None
    tg_session: str = "data/tg"
    tg_mode: str = "read"  # read | write (har xabar tasdiq bilan) | full (cheklovsiz)
    tg_allowed: tuple = ()
    tg_max_sends: int = 10
    tg_proxy: str | None = None
    secret_key: str | None = None  # Telegram sessiyasini bazada shifrlash (bo'lmasa WEB_TOKEN)  # tg://proxy?... (MTProxy) yoki socks5://host:port
    private_providers: tuple = ()
    tier_providers: dict = field(default_factory=dict)


def parse_tier_providers(raw: str) -> dict:
    """'cheap:gemini,strong:anthropic' -> {'cheap': 'gemini', 'strong': 'anthropic'}"""
    out = {}
    for part in (raw or "").split(","):
        tier, _, prov = part.partition(":")
        if tier.strip() in TIERS and prov.strip():
            out[tier.strip()] = prov.strip().lower()
    return out


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
            api_key=(env.get(KEY_ENV[name]) or "").strip().strip("'\"").strip() or None,  # nusxalashda qo'shtirnoq/bo'sh joy qolsa ham
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
        # Render/Railway kabi xostinglar PORT beradi va tashqaridan ulanish 0.0.0.0 da bo'lishi kerak
        # Render'da lokal .env dan ko'chirilgan WEB_HOST=127.0.0.1 / WEB_PORT=8080 ilovani ulab bo'lmaydigan qilardi: e'tiborsiz
        web_host=("0.0.0.0" if env.get("RENDER") and env.get("PORT") else
                  env.get("WEB_HOST") or ("0.0.0.0" if env.get("PORT") else "127.0.0.1")),
        web_port=int((env.get("PORT") if env.get("RENDER") else None) or env.get("WEB_PORT") or env.get("PORT") or 8080),
        web_token=env.get("WEB_TOKEN") or None,
        widget_token=env.get("WIDGET_TOKEN") or None,
        web_public_url=(env.get("WEB_PUBLIC_URL") or env.get("RENDER_EXTERNAL_URL") or "").rstrip("/") or None,
        google_maps_key=env.get("GOOGLE_MAPS_API_KEY") or None,
        tg_api_id=int(env["TG_API_ID"]) if str(env.get("TG_API_ID", "")).strip().isdigit() else None,
        tg_api_hash=env.get("TG_API_HASH") or None,
        tg_session=str(Path(env.get("TG_SESSION") or "data/tg") if Path(env.get("TG_SESSION") or "data/tg").is_absolute()
                       else ROOT / (env.get("TG_SESSION") or "data/tg")),  # ish papkasiga bog'liq bo'lmasin
        tg_mode={"write": "write", "full": "full"}.get((env.get("TG_MODE") or "").strip().lower(), "read"),
        tg_allowed=tuple(x.strip().lower().lstrip("@") for x in (env.get("TG_ALLOWED") or "").split(",") if x.strip()),
        tg_max_sends=int(env.get("TG_MAX_SENDS_PER_HOUR", 10)),
        tg_proxy=(env.get("TG_PROXY") or "").strip() or None,
        secret_key=env.get("SECRET_KEY") or None,
        private_providers=tuple(x.strip().lower() for x in (env.get("PRIVATE_PROVIDERS") or "").split(",") if x.strip()),
        primary_provider=(env.get("PRIMARY_PROVIDER") or "auto").strip().lower(),
        tier_providers=parse_tier_providers(env.get("PROVIDER_BY_TIER", "")),
    )


def ensure_secret(name: str, env_path: Path | None = None) -> str:
    """Maxfiy token yo'q bo'lsa yaratib .env ga yozadi (git'ga tushmaydi)."""
    import secrets
    load_dotenv(env_path or ROOT / ".env")  # allaqachon bor kalitni qayta yaratib yubormaslik uchun
    if os.environ.get(name):
        return os.environ[name]
    if os.environ.get("RENDER"):  # serverda .env har qayta ishga tushganda yo'qoladi: token o'zgarib, havola buzilardi
        raise SystemExit(f"{name} yo'q: Render -> Environment bo'limida {name} qo'ying (uzun tasodifiy satr)")
    value = secrets.token_urlsafe(32)
    path = env_path or ROOT / ".env"
    text = path.read_text() if path.exists() else ""
    line = f"{name}={value}"
    if f"{name}=" in text:
        import re
        text = re.sub(rf"^{name}=.*$", line, text, flags=re.M)
    else:
        text = text.rstrip("\n") + f"\n{line}\n"
    path.write_text(text)
    path.chmod(0o600)
    os.environ[name] = value
    return value
