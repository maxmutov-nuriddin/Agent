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
    lines += await audit_highlights(app, iso)
    if await app.store.get_kv("paused") == "1":
        lines.append("⏸ Tizim to'xtatilgan (/resume)")
    return "\n".join(lines)


def _one(detail: str, n: int = 90) -> str:
    return " ".join((detail or "").split())[:n]


async def audit_highlights(app: App, iso: str) -> list[str]:
    """Nazorat: muhim amallar jurnaldan (LLM'siz). Egasi har safar jurnalni ochmasligi uchun."""
    rows = await app.store.audit_since(iso)
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(r["action"], []).append(r)
    out = []
    sent = by.get("tg_send", [])
    ok = [r for r in sent if not (r["detail"] or "").startswith(("DENIED", "EXPIRED"))]
    if sent:
        out.append(f"📨 Telegram xabarlari: {len(ok)} ta yuborildi" + (f", {len(sent) - len(ok)} ta rad/muddati o'tgan" if len(sent) > len(ok) else ""))
        out += [f"   • {_one(r['detail'])}" for r in ok[:5]]
    acts = by.get("tg_action", [])
    if acts:
        out.append(f"🛠 Telegram amallari (guruh, forward, o'chirish…): {len(acts)} ta")
        out += [f"   • {_one(r['detail'])}" for r in acts[:5]]
    cmds = by.get("run_command", [])
    if cmds:
        bad = [r for r in cmds if (r["detail"] or "").startswith(("DENIED", "EXPIRED"))]
        out.append(f"💻 Buyruqlar: {len(cmds) - len(bad)} ta bajarildi" + (f", {len(bad)} ta rad etildi" if bad else ""))
        out += [f"   • {_one(r['detail'])}" for r in cmds[:3]]
    if by.get("private_fallback"):
        out.append(f"🛡 Maxfiy AI ishlamadi: {len(by['private_fallback'])} marta (matn boshqa AI'ga ketdi, maxfiy ma'lumotlar yashirilgan)")
    errs = by.get("task_error", []) + by.get("resume_error", [])
    if errs:
        out.append(f"❌ Xatolar: {len(errs)} ta (oxirgisi: {_one(errs[0]['detail'], 70)})")
    changed = [r for a in ("tg_access", "tg_listen", "tg_keys", "tg_login", "tg_logout", "hire", "fire") for r in by.get(a, [])]
    if changed:
        out.append("⚙️ O'zgarishlar: " + "; ".join(sorted({f"{r['action']}" for r in changed})))
    return (["", "🔎 Nazorat (muhim amallar)"] + out) if out else ["", "🔎 Nazorat: muhim amal bo'lmadi ✓"]


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
