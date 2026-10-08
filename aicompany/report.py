from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .app import App


def day_start_utc(now_local: datetime) -> datetime:
    return now_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


async def build_report(app: App, since: datetime, title: str) -> str:
    """Hisobot LLM'siz tuziladi: pul sarflamaydi."""
    iso = since.isoformat()
    tasks = await app.store.tasks_since(iso)
    by_status: dict[str, int] = {}
    for t in tasks:
        by_status[t["status"]] = by_status.get(t["status"], 0) + 1
    lines = [f"📊 {title}"]
    lines.append("Vazifalar: " + (", ".join(f"{k}: {v}" for k, v in by_status.items()) or "yo'q"))
    lines.append(f"Sarf (shu davr): {await app.store.spent_since(iso):.3f}$")
    for name, v in (await app.router.status()).items():
        if v["enabled"]:
            lines.append(f"  {name}: {v['spent']:.2f}$ / {v['budget']:.2f}$ (qoldi {v['budget'] - v['spent']:.2f}$)")
    agents = await app.store.list_agents()
    lines.append(f"Jamoa: {len(agents)} xodim")
    if await app.store.get_kv("paused") == "1":
        lines.append("⏸ Tizim to'xtatilgan (/resume)")
    return "\n".join(lines)


def next_run(now_local: datetime, hour: int) -> datetime:
    run = now_local.replace(hour=hour, minute=0, second=0, microsecond=0)
    return run if run > now_local else run + timedelta(days=1)


async def daily_report_loop(app: App, send):
    tz = ZoneInfo(app.settings.report_tz)
    while True:
        now = datetime.now(tz)
        await asyncio.sleep((next_run(now, app.settings.report_hour) - now).total_seconds())
        since = datetime.now(tz) - timedelta(days=1)
        try:
            await send(await build_report(app, since, "Kunlik hisobot (so'nggi 24 soat)"))
        except Exception:  # noqa: BLE001 — hisobot xatosi botni to'xtatmasin
            continue
