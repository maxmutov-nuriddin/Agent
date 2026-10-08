from __future__ import annotations

from dataclasses import dataclass

from .approvals import ApprovalCenter
from .config import Settings, load_settings
from .db import Store
from .orchestrator import Orchestrator
from .providers import build_providers
from .router import Router
from .team import Team
from .tguser import TgUser


@dataclass
class App:
    settings: Settings
    store: Store
    router: Router
    team: Team
    orch: Orchestrator
    center: ApprovalCenter | None = None
    tg: TgUser | None = None


async def build_app(settings: Settings | None = None, providers=None, approver=None) -> App:
    settings = settings or load_settings()
    store = Store(settings.database_url)
    await store.init()
    router = Router(settings, store, providers if providers is not None else build_providers(settings))
    team = Team(store, router, settings.max_agents, settings.max_tool_turns, settings.private_providers)
    await team.ensure_seed()
    await store.fail_stale_tasks()
    center = ApprovalCenter()
    tg = TgUser(settings) if settings.tg_api_id else None
    orch = Orchestrator(store, team, settings, settings.max_revisions, approver or center, tg)
    return App(settings, store, router, team, orch, center, tg)
