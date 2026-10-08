from __future__ import annotations

from dataclasses import dataclass

from .config import Settings, load_settings
from .db import Store
from .orchestrator import Orchestrator
from .providers import build_providers
from .router import Router
from .team import Team


@dataclass
class App:
    settings: Settings
    store: Store
    router: Router
    team: Team
    orch: Orchestrator


async def build_app(settings: Settings | None = None, providers=None) -> App:
    settings = settings or load_settings()
    store = Store(settings.database_url)
    await store.init()
    router = Router(settings, store, providers if providers is not None else build_providers(settings))
    team = Team(store, router, settings.max_agents)
    await team.ensure_seed()
    return App(settings, store, router, team, Orchestrator(store, team, settings.max_revisions))
