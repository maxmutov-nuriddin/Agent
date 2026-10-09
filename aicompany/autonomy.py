"""Avtonom agentlar: jadval bo'yicha fonda o'zi ishlaydi (videodagi "fully autonomous" darajasi).

Har biri Sozlamalarda (Jamoa → Tuzilma) yoqiladi/o'chiriladi, .env kerak emas. Rejimlar:
  off    — ishlamaydi
  report — tekshiradi va muammo bo'lsagina xabar beradi (hech narsani o'zgartirmaydi)
  auto   — tekshiradi va xavfsiz tuzatishni o'zi qiladi (masalan takror xotirani o'chiradi), keyin xabar beradi
Ko'pchiligi AI ishlatmaydi (bepul). AI ishlatadigani faqat "auto" rejimda va arzon modelda.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from datetime import datetime, timedelta, timezone

log = logging.getLogger("aicompany.autonomy")
HOUR, WEEK = 3600, 7 * 86400
JOBS = {
    "server_watch": {"title": "Server kuzatuvchi", "dept": "tech", "every": HOUR, "ai": False, "modes": ("off", "report"),
                     "replaces": "serverni qo'lda tekshirish: disk, xotira, xatolar", "default": "report"},
    "cost_watch": {"title": "Xarajat nazoratchisi", "dept": "tadqiqot", "every": HOUR, "ai": False, "modes": ("off", "report"),
                   "replaces": "xarajatni kuzatish: keskin o'sish va limitga yaqinlashish", "default": "report"},
    "link_watch": {"title": "Havola tekshiruvchi", "dept": "sifat", "every": WEEK, "ai": False, "modes": ("off", "report"),
                   "replaces": "natijalardagi havolalarni qo'lda tekshirish", "default": "off"},
    "memory_keeper": {"title": "Bilim bazasi nazoratchisi", "dept": "sifat", "every": WEEK, "ai": True, "modes": ("off", "report", "auto"),
                      "replaces": "xotira va qoidalardagi takrorlarni tozalash", "default": "off"},
}
ALERT_COOLDOWN = 6 * 3600


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AutoAgents:
    def __init__(self, app):
        self.app = app
        self.store = app.store

    async def state(self, name: str) -> dict:
        job = JOBS[name]
        st = {"mode": job["default"], "last": "", "result": "", "alert_at": "", "alert_key": ""}
        raw = await self.store.get_kv(f"auto:{name}")
        if raw:
            try:
                st.update(json.loads(raw))
            except ValueError:
                pass
        if st["mode"] not in job["modes"]:
            st["mode"] = job["default"]
        return st

    async def _save(self, name: str, st: dict):
        await self.store.set_kv(f"auto:{name}", json.dumps(st, ensure_ascii=False))

    async def set_mode(self, name: str, mode: str) -> dict:
        if name not in JOBS or mode not in JOBS[name]["modes"]:
            raise ValueError("noma'lum agent yoki rejim")
        st = await self.state(name)
        st["mode"] = mode
        await self._save(name, st)
        return st

    async def list(self) -> list[dict]:
        out = []
        for name, job in JOBS.items():
            st = await self.state(name)
            out.append({"name": name, "title": job["title"], "dept": job["dept"], "replaces": job["replaces"], "ai": job["ai"],
                        "modes": list(job["modes"]), "mode": st["mode"], "last": st["last"], "result": st["result"],
                        "every": "har soat" if job["every"] == HOUR else "haftada bir"})
        return out

    # ---------- ishga tushirish ----------
    async def run(self, name: str, senders=(), force: bool = False) -> str:
        st = await self.state(name)
        if st["mode"] == "off" and not force:
            return ""
        try:
            problems, info = await getattr(self, "_" + name)(st["mode"] if st["mode"] != "off" else "report")
        except Exception as e:  # noqa: BLE001 — bitta agent xatosi boshqalarini to'xtatmasin
            log.exception("avtonom agent %s", name)
            problems, info = [], f"xato: {type(e).__name__}"
        st["last"] = _now().isoformat()
        st["result"] = ("⚠️ " + "; ".join(problems) if problems else "✅ " + (info or "hammasi joyida"))[:400]
        key = "|".join(sorted(problems))
        recent = st.get("alert_at") and _now() - datetime.fromisoformat(st["alert_at"]) < timedelta(seconds=ALERT_COOLDOWN)
        if problems and (force or key != st.get("alert_key") or not recent):
            st["alert_at"], st["alert_key"] = _now().isoformat(), key
            await self._alert(f"🤖 {JOBS[name]['title']}: " + "; ".join(problems), senders)
        await self._save(name, st)
        return st["result"]

    async def _alert(self, text: str, senders):
        if getattr(self.app, "push", None):
            try:
                await self.app.push.notify("auto", "🤖 Avtonom agent", text[:180], "/?tab=team")
            except Exception:  # noqa: BLE001
                log.exception("avtonom push")
        for fn in senders:
            try:
                await fn(text)
            except Exception:  # noqa: BLE001
                log.exception("avtonom xabar yuborilmadi")

    async def loop(self, senders=(), poll: float = 300):
        while True:
            for name, job in JOBS.items():
                try:
                    st = await self.state(name)
                    if st["mode"] == "off":
                        continue
                    last = datetime.fromisoformat(st["last"]) if st["last"] else None
                    if last is None or (_now() - last).total_seconds() >= job["every"]:
                        await self.run(name, senders)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    log.exception("avtonom sikl: %s", name)
            await asyncio.sleep(poll)

    # ---------- agentlar ----------
    async def _server_watch(self, mode):
        problems = []
        ws = self.app.settings.workspace_dir
        du = shutil.disk_usage(ws if ws.exists() else "/")
        used = du.used / du.total * 100
        if used >= 90:
            problems.append(f"disk {used:.0f}% to'lgan (bo'sh {du.free / 1e9:.1f} GB)")
        try:
            mem = dict(re.findall(r"(\w+):\s+(\d+)", open("/proc/meminfo").read()))
            avail = int(mem["MemAvailable"]) / int(mem["MemTotal"]) * 100
            if avail < 10:
                problems.append(f"operativ xotira kam: {avail:.0f}% bo'sh")
        except (OSError, KeyError, ValueError):
            avail = None
        since = (_now() - timedelta(hours=1)).isoformat()
        errors = [a for a in await self.store.audit_since(since) if "error" in (a["action"] or "")]
        if len(errors) >= 10:
            problems.append(f"oxirgi soatda {len(errors)} ta AI/xizmat xatosi ({errors[0]['action']})")
        load = os.getloadavg()[0] if hasattr(os, "getloadavg") else 0
        info = f"disk {used:.0f}%" + (f", xotira {avail:.0f}% bo'sh" if avail is not None else "") + f", yuklama {load:.1f}"
        return problems, info

    async def _cost_watch(self, mode):
        from zoneinfo import ZoneInfo
        from .report import day_start_utc
        tz = ZoneInfo(self.app.settings.report_tz)
        today = datetime.now(tz).date()
        rows = await self.store.daily_spend_local(day_start_utc(datetime.now(tz) - timedelta(days=7)).isoformat(), tz)
        by_day = {r["day"]: float(r["cost"]) for r in rows}
        t = by_day.get(today.isoformat(), 0.0)
        prev = [v for d, v in by_day.items() if d != today.isoformat()]
        avg = sum(prev) / 7 if prev else 0.0
        problems = []
        if t >= 0.5 and t > 2 * max(avg, 0.05):
            problems.append(f"bugungi sarf {t:.2f}$, odatdagidan (kuniga ~{avg:.2f}$) {t / max(avg, 0.01):.1f} barobar ko'p")
        for name, b in (await self.app.router.status()).items():
            if b["enabled"] and b["budget"] and b["spent"] >= 0.9 * b["budget"] and name not in self.app.router.FREE:
                problems.append(f"{name} limiti tugayapti: {b['spent']:.2f}$ / {b['budget']:.2f}$")
        return problems, f"bugun {t:.2f}$, o'rtacha kuniga {avg:.2f}$"

    async def _link_watch(self, mode):
        from . import linkcheck
        from types import SimpleNamespace
        texts, where = [], {}
        for t in await self.store.list_tasks(20, view="done"):
            f = self.app.settings.workspace_dir / f"task_{t['id']}" / "NATIJA.md"
            try:
                txt = f.read_text(encoding="utf-8")
            except OSError:
                continue
            for u in linkcheck.extract_urls(txt.split("## Havolalar tekshiruvi")[0]):
                where.setdefault(u, t["id"])
            texts.append(txt)
        urls = list(where)[:linkcheck.MAX_URLS]
        res = await linkcheck.check(SimpleNamespace(http=None), urls)
        dead = [f"#{where[u]}: {u}" for u, s in res.items() if s.startswith("❌")]
        problems = [f"{len(dead)} ta o'lik havola: " + ", ".join(dead[:5])] if dead else []
        return problems, f"{len(urls)} ta havola tekshirildi"

    async def _memory_keeper(self, mode):
        mems = await self.store.recent_memories(500)
        seen, dups = {}, []
        for m in sorted(mems, key=lambda r: r["id"]):
            key = re.sub(r"\W+", " ", (m["text"] or "").lower()).strip()
            if key in seen:
                dups.append(m["id"])
            else:
                seen[key] = m["id"]
        if mode == "auto":
            for mid in dups:
                await self.store.delete_memory(mid)
            orch = getattr(self.app, "orch", None)
            if orch and hasattr(orch, "_consolidate_prefs"):
                try:
                    await orch._consolidate_prefs()
                except Exception:  # noqa: BLE001 — AI ishlamasa takrorlar baribir o'chirildi
                    log.exception("qoidalarni birlashtirish")
            return [], f"{len(dups)} ta takror xotira o'chirildi, qoidalar ixchamlandi"
        return ([f"{len(dups)} ta takror xotira bor («o'zi tuzatsin» rejimida tozalanadi)"] if dups else []), f"{len(mems)} ta xotira, takror yo'q"
