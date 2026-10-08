from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

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
    listener: object | None = None  # TgListener: agent akkaunti egasiga bot kabi javob beradi
    push: object | None = None      # PushService: PWA bildirishnomalar
    calls: object | None = None     # CallService: Telegram ovozli qo'ng'iroq (ixtiyoriy)


def clean_inbox(workspace: Path, days: int = 7) -> int:
    """Eski yuklamalarni (vazifaga allaqachon nusxalangan) o'chiradi: disk to'lib ketmasin."""
    inbox, cutoff, n = workspace / "inbox", time.time() - days * 86400, 0
    if not inbox.is_dir():
        return 0
    for p in inbox.iterdir():
        try:
            if p.stat().st_mtime < cutoff:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
                n += 1
        except OSError:
            pass
    return n


async def build_app(settings: Settings | None = None, providers=None, approver=None) -> App:
    settings = settings or load_settings()
    store = Store(settings.database_url)
    await store.init()
    router = Router(settings, store, providers if providers is not None else build_providers(settings))
    team = Team(store, router, settings.max_agents, settings.max_tool_turns, settings.private_providers,
                settings.private_mode)
    await team.ensure_seed()
    await store.fail_stale_tasks()
    clean_inbox(settings.workspace_dir)
    center = ApprovalCenter()
    tg = TgUser(settings)
    if not tg.has_keys():  # .env da yo'q bo'lsa, panel orqali kiritilganini olamiz
        kid, khash = await store.get_kv("tg_api_id"), await store.get_kv("tg_api_hash")
        if kid and kid.isdigit() and khash:
            tg.api_id, tg.api_hash = int(kid), khash
    tg.proxy = await store.get_kv("tg_proxy") or tg.proxy
    tg.me = await store.get_kv("tg_me") or ""
    if store.remote:  # disksiz server: sessiya va vazifa fayllari bazadan tiklanadi
        from .persist import restore_workspaces, seal, unseal
        secret = settings.secret_key or settings.web_token
        tg.use_db_session = True
        tg.session_str = unseal(await store.get_kv("tg_session_sealed"), secret)

        async def keep_session(value):
            if value:
                await store.set_kv("tg_session_sealed", seal(value, secret))
            else:
                await store.delete_kv("tg_session_sealed")
        tg.on_session = keep_session
        try:
            restore_workspaces_n = await restore_workspaces(store, settings.workspace_dir)
            if restore_workspaces_n:
                await store.audit("system", "restore_files", f"{restore_workspaces_n} ta fayl bazadan tiklandi")
        except Exception as e:  # noqa: BLE001 — fayl tiklanmasa ham dastur ishlasin
            await store.audit("system", "restore_files_error", repr(e)[:300])
    orch = Orchestrator(store, team, settings, settings.max_revisions, approver or center, tg)
    app = App(settings, store, router, team, orch, center, tg)
    from .tglisten import TgListener
    app.listener = TgListener(app)
    from .calls import CallService
    app.calls = CallService(app)
    from .push import PushService
    app.push = PushService(app)
    center.announcers.append(app.push.approval)

    async def on_done(res):
        await app.push.task_done(res)
        await app.calls.task_done(res)
    orch.on_done = on_done
    orch.call_owner = app.calls.call_owner
    orch.call_ready = lambda: app.calls._tgc is not None
    return app
