from __future__ import annotations

import asyncio
import httpx
import hashlib
import hmac
import json
import logging
import re
import secrets
import shutil
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from aiohttp import web

from .push import available as push_available
from .briefing import settings_of as morning_settings
from .app import App
from . import voicereply
from .report import build_report, day_start_utc
from .providers import ProviderError, VoiceError, VoiceUnavailable, suggest_model
from .tguser import TgError, TgStale
from .tools import ToolError
from .team import CORE
from .util import clip

def _version() -> str:
    """Ishlayotgan kod versiyasi (git commit): panel eskirganini aniqlash uchun."""
    import subprocess
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=Path(__file__).parent,
                             capture_output=True, text=True, timeout=3)
        return out.stdout.strip() or "dev"
    except (OSError, subprocess.SubprocessError):
        return "dev"


VERSION = _version()
STARTED = time.monotonic()
PROV_NAMES = {"anthropic": "Claude", "gemini": "Gemini", "openai": "ChatGPT"}
STATIC = Path(__file__).parent / "webui"
STATIC_FILES = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css",
                "/sw.js": "sw.js", "/manifest.webmanifest": "manifest.webmanifest",
                "/icon-192.png": "icon-192.png", "/icon-512.png": "icon-512.png",
                "/apple-touch-icon.png": "apple-touch-icon.png"}
CTYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
          ".webmanifest": "application/manifest+json", ".png": "image/png"}
CSP = ("default-src 'self'; img-src 'self' data:; media-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; manifest-src 'self'; worker-src 'self'")
MAX_FAILS, FAIL_WINDOW = 8, 60
log = logging.getLogger("web")


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("10.255.255.255", 1))  # paket yuborilmaydi, faqat yo'nalish aniqlanadi
            return sk.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def web_url(s) -> tuple[str, bool]:
    """(kirish havolasi, telefondan ochilishi mumkinmi). WEB_PUBLIC_URL berilsa shu ishlatiladi."""
    if s.web_public_url:
        base, reachable = s.web_public_url, True
    else:
        loopback = s.web_host in ("127.0.0.1", "localhost", "::1")
        host = "127.0.0.1" if loopback else lan_ip() if s.web_host in ("0.0.0.0", "::") else s.web_host
        base, reachable = f"http://{host}:{s.web_port}", not loopback
    return f"{base}/#token={s.web_token}", reachable


def json_ok(data, status=200):
    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False))


def make_web_app(app: App) -> web.Application:
    s = app.settings
    fails: dict[str, list[float]] = {}
    jobs: set[asyncio.Task] = set()
    chat_id = s.owner_id or 0

    def limited(ip: str) -> bool:
        now = time.monotonic()
        fails[ip] = [t for t in fails.get(ip, []) if now - t < FAIL_WINDOW]
        return len(fails[ip]) >= MAX_FAILS

    def fail(ip: str):
        fails.setdefault(ip, []).append(time.monotonic())

    @web.middleware
    async def guard(request: web.Request, handler):
        path = request.path
        if path.startswith("/api/"):
            ip = request.remote or "?"
            if limited(ip):
                return json_ok({"error": "juda ko'p urinish"}, 429)
            if path == "/api/widget":
                want, got = s.widget_token, request.query.get("token", "")
            else:
                auth = request.headers.get("Authorization", "")
                want, got = s.web_token, auth[7:] if auth.startswith("Bearer ") else ""
            if not want or not hmac.compare_digest(want.encode(), got.encode()):
                fail(ip)
                return json_ok({"error": "ruxsat yo'q"}, 401)
        if request.method != "GET":
            _cache.clear()
        try:
            resp = await handler(request)
        except web.HTTPException as e:
            if not path.startswith("/api/"):
                raise
            resp = json_ok({"error": e.reason}, e.status)
        if path.startswith("/api/") and isinstance(resp, web.Response) and resp.body is not None and len(resp.body) > 1024 \
                and "gzip" in request.headers.get("Accept-Encoding", ""):
            resp.enable_compression()
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        if path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    async def body(request) -> dict:
        try:
            data = await request.json()
        except (json.JSONDecodeError, ValueError):
            raise web.HTTPBadRequest(reason="JSON noto'g'ri")
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(reason="JSON obyekt bo'lishi kerak")
        return data

    # ---------- statik ----------
    async def static(request):
        name = STATIC_FILES[request.path]
        f = STATIC / name
        body = f.read_bytes()
        etag = '"' + hashlib.md5(body).hexdigest()[:16] + '"'
        if request.headers.get("If-None-Match") == etag:
            return web.Response(status=304, headers={"ETag": etag, "Cache-Control": "no-cache"})  # o'zgarmagan: qayta yuklanmaydi
        resp = web.Response(body=body, content_type=CTYPES[f.suffix].split(";")[0],
                            charset="utf-8" if f.suffix != ".png" else None,
                            headers={"Cache-Control": "no-cache", "ETag": etag})
        if f.suffix in (".js", ".css", ".html", ".webmanifest", ".json") and len(body) > 1024:
            resp.enable_compression()  # gzip: LTE'da 3-4 barobar kam trafik
        return resp

    # ---------- ma'lumot ----------
    async def today_spend() -> float:
        tz = ZoneInfo(s.report_tz)
        return await app.store.spent_since(day_start_utc(datetime.now(tz)).isoformat())

    _cache: dict = {}

    async def state_data() -> dict:
        """Panel har 3-8 soniyada so'raydi: 1.5 soniyalik kesh bazaga yukni kamaytiradi (yozuvlar keshni tozalaydi)."""
        hit = _cache.get("state")
        if hit and time.monotonic() - hit[0] < 1.5:
            return hit[1]
        data = await _state_data()
        _cache["state"] = (time.monotonic(), data)
        return data

    async def _state_data() -> dict:
        budgets = await app.router.status()
        recent = await app.store.list_tasks(100)
        running = [t for t in recent if t["status"] == "running"]
        day_iso = day_start_utc(datetime.now(ZoneInfo(s.report_tz))).isoformat()
        done_today = sum(1 for t in recent if t["status"] == "done" and (t["finished_at"] or "") >= day_iso)
        return {
            "paused": await app.store.get_kv("paused") == "1",
            "eco": await app.store.get_kv("eco") != "0",
            "push": {"available": bool(app.push and push_available()), "prefs": await app.push.prefs() if app.push else {},
                     "devices": len(await app.push.subs()) if app.push else 0},
            "bot_push": (await app.store.get_kv("bot_push")) or "all",
            "morning": await morning_settings(app.store),
            "watch_smart": (await app.store.get_kv("watch_smart")) == "1",
            "watch_on": {k: (await app.store.get_kv(f"watch_on:{k}")) != "0" for k in ("tg", "price")},
            "today": round(await today_spend(), 4),
            "budgets": [{"provider": n, **v} for n, v in budgets.items() if n not in app.router.FREE],
            "working": [{"agent": a, "task_id": b["task_id"]} for a, b in app.team.busy.items()],
            "done_today": done_today,
            "running_tasks": [{"id": t["id"], "request": t["request"][:100]} for t in running],
            "pending": len(app.center.list()) if app.center else 0,
            "version": VERSION,
            "primary": await app.router.primary(),
            "providers": sorted(n for n in app.router.providers if n not in app.router.FREE),
        }

    async def h_state(request):
        return json_ok(await state_data())

    async def team_data() -> list[dict]:
        costs = {r["agent"]: float(r["cost"]) for r in await app.store.spent_by_agent()}
        steps = await app.store.agent_message_counts()
        out = []
        for a in await app.store.list_agents():
            busy = app.team.busy.get(a["name"])
            meta = await app.team.meta(a["name"])
            out.append({"name": a["name"], "role": a["role"], "tier": a["tier"], **meta,
                        "tools": [t for t in a["tools"].split(",") if t], "created_by": a["created_by"],
                        "core": a["name"] in CORE, "busy": bool(busy), "task_id": busy["task_id"] if busy else None,
                        "cost": round(costs.get(a["name"], 0.0), 4), "steps": steps.get(a["name"], 0)})
        return out

    async def h_overview(request):
        """Kompyuter dashboardi uchun hamma narsa bitta so'rovda (uzoq bazada har so'rov qimmat)."""
        tz = ZoneInfo(s.report_tz)
        today = datetime.now(tz).date()
        days = [(today - timedelta(days=i)) for i in range(13, -1, -1)]
        since = day_start_utc(datetime.now(tz) - timedelta(days=13)).isoformat()
        state, team, spend, recent, rems = await asyncio.gather(
            state_data(), team_data(), app.store.daily_spend_local(since, tz), app.store.list_tasks(40), app.store.list_reminders(limit=6))
        cost_by_day = {r["day"]: r["cost"] for r in spend}
        done_by_day: dict[str, int] = {}
        for t in recent:
            if t["status"] == "done" and t["finished_at"]:
                try:
                    d = datetime.fromisoformat(t["finished_at"]).astimezone(tz).date().isoformat()
                except ValueError:
                    continue
                done_by_day[d] = done_by_day.get(d, 0) + 1
        shown = recent[:9]
        costs = await app.store.spent_by_task([t["id"] for t in shown])
        from .reminders import local_text
        return json_ok({
            "state": state, "team": team,
            "daily": [{"day": d.isoformat(), "cost": round(cost_by_day.get(d.isoformat(), 0.0), 4),
                       "done": done_by_day.get(d.isoformat(), 0)} for d in days],
            "tasks": [{"id": t["id"], "status": t["status"], "request": t["request"][:160], "note": t["note"],
                       "created_at": t["created_at"], "cost": round(costs.get(t["id"], 0.0), 4)} for t in shown],
            "counts": {k: sum(1 for t in recent if t["status"] == k) for k in ("running", "done", "failed", "limit", "cancelled")},
            "approvals": app.center.list() if app.center else [],
            "reminders": [{"id": r["id"], "text": r["text"], "local": local_text(r["due_at"], s.report_tz)} for r in rems],
        })

    async def h_team(request):
        return json_ok(await team_data())

    async def h_org(request):
        """Tuzilma: bo'limlar, xodimlar kartochkalari, avtonom agentlar va sozlamalar (videodagi kabi daraxt uchun)."""
        from .team import DEPT_LEADS, DEPTS, MODEL_CHOICES
        return json_ok({"depts": [{"key": k, "title": v, "lead": DEPT_LEADS.get(k)} for k, v in DEPTS.items()],
                        "agents": await team_data(), "auto": await app.auto.list() if app.auto else [],
                        "dept_leads": await app.store.get_kv("dept_leads") == "1",
                        "models": [m for m in MODEL_CHOICES if m == "auto" or m in app.router.providers]})

    async def h_agent_meta(request):
        from .team import DEPTS, MODEL_CHOICES
        d = await body(request)
        name = str(d.get("name", ""))
        a = await app.store.get_agent(name)
        if not a:
            raise web.HTTPNotFound(reason="xodim topilmadi")
        if "dept" in d and d["dept"] not in DEPTS:
            raise web.HTTPBadRequest(reason="noma'lum bo'lim")
        if "model" in d and (d["model"] not in MODEL_CHOICES or (d["model"] != "auto" and d["model"] not in app.router.providers)):
            raise web.HTTPBadRequest(reason="bu AI ulanmagan (kalit yo'q)")
        if "tier" in d:
            if d["tier"] not in ("cheap", "mid", "strong"):
                raise web.HTTPBadRequest(reason="daraja: cheap, mid yoki strong")
            await app.store.set_agent_profile(name, a["role"], a["system_prompt"], d["tier"])
        meta = await app.team.set_meta(name, dept=d.get("dept"), model=d.get("model"))
        await app.store.audit("owner", "agent_meta", f"{name}: {json.dumps(d, ensure_ascii=False)[:200]}")
        return json_ok(meta)

    async def h_auto(request):
        d = await body(request)
        try:
            st = await app.auto.set_mode(str(d.get("name", "")), str(d.get("mode", "")))
        except ValueError as e:
            raise web.HTTPBadRequest(reason=str(e))
        return json_ok({"mode": st["mode"]})

    async def h_auto_run(request):
        from .autonomy import JOBS
        name = str((await body(request)).get("name", ""))
        if name not in JOBS:
            raise web.HTTPBadRequest(reason="noma'lum agent")
        return json_ok({"result": await app.auto.run(name, (), force=True)})

    async def h_dept_leads(request):
        on = bool((await body(request)).get("enabled"))
        await app.store.set_kv("dept_leads", "1" if on else "0")
        return json_ok({"enabled": on})

    async def h_hire(request):
        d = await body(request)
        name, why = str(d.get("name", "")).strip(), str(d.get("why", "")).strip()
        if not (2 <= len(name) <= 40 and 3 <= len(why) <= 500):
            raise web.HTTPBadRequest(reason="nom (2-40) va sabab (3-500 belgi) kerak")
        hired = await app.team.hire(name, why, created_by="owner")
        if not hired:
            raise web.HTTPConflict(reason="jamoa to'lgan")
        return json_ok({"name": hired})

    async def h_fire(request):
        d = await body(request)
        if not await app.team.fire(str(d.get("name", "")).strip()):
            raise web.HTTPConflict(reason="topilmadi yoki asosiy xodim")
        return json_ok({"ok": True})

    async def h_tasks(request):
        archived = request.query.get("archived") == "1"
        view = request.query.get("view")
        rows = await app.store.list_tasks(60, archived=archived, view=view if view in ("active", "done", "archive") else None)
        return json_ok([{"id": t["id"], "status": t["status"], "request": t["request"][:160],
                         "created_at": t["created_at"], "finished_at": t["finished_at"],
                         "archived": bool(t["archived"]), "note": t["note"], "based_on": t["based_on"],
                         "cost": round(await app.store.spent_task(t["id"]), 4)} for t in rows])

    def task_files(task_id: int) -> list[str]:
        ws = s.workspace_dir / f"task_{task_id}"
        from .orchestrator import Orchestrator
        return Orchestrator._files(ws) if ws.is_dir() else []

    async def h_task(request):
        t = await app.store.get_task(int(request.match_info["id"]))
        if not t:
            raise web.HTTPNotFound(reason="topilmadi")
        msgs = await app.store.task_messages(t["id"])
        return json_ok({"id": t["id"], "status": t["status"], "request": t["request"], "result": t["result"],
                        "archived": bool(t["archived"]), "note": t["note"], "based_on": t["based_on"],
                        "approvals": [{"agent": a["agent"], "command": a["description"], "status": a["status"], "kind": a["kind"],
                                       "at": a["decided_at"] or a["created_at"]} for a in await app.store.task_approvals(t["id"])],
                        "cost": round(await app.store.spent_task(t["id"]), 4), "files": task_files(t["id"]),
                        "messages": [{"agent": m["agent"], "content": clip(m["content"] or "", 6000)} for m in msgs]})

    async def h_file(request):
        tid, rel = int(request.match_info["id"]), request.match_info["path"]
        root = (s.workspace_dir / f"task_{tid}").resolve()
        f = (root / rel).resolve()
        if not f.is_relative_to(root) or not f.is_file():
            raise web.HTTPNotFound(reason="fayl topilmadi")
        return web.FileResponse(f, headers={"Content-Type": "application/octet-stream",
                                            "Content-Disposition": f'attachment; filename="{f.name}"'})

    async def h_approvals(request):
        return json_ok(app.center.list() if app.center else [])

    async def h_decide(request):
        d = await body(request)
        if not app.center or not app.center.resolve(int(request.match_info["id"]), bool(d.get("approve"))):
            raise web.HTTPConflict(reason="muddati o'tgan yoki allaqachon hal qilingan")
        return json_ok({"ok": True})

    # ---------- suhbat ----------
    async def h_chat_get(request):
        after = int(request.query.get("after", 0) or 0)
        rows = await app.store.chat_since(chat_id, after)
        # "[Vazifa #N ...]" - rahbar xotirasi uchun ichki xulosa, foydalanuvchiga natija kartasi ko'rsatiladi
        rows = [r for r in rows if not (r["role"] == "ceo" and r["text"].startswith("[Vazifa #"))]
        return json_ok([{"id": r["id"], "role": r["role"], "text": r["text"], "ts": r["created_at"]} for r in rows])

    def spawn(coro):
        t = asyncio.create_task(coro)
        jobs.add(t)
        t.add_done_callback(jobs.discard)

    async def notify(msg: str):
        last = await app.store.recent_chat(chat_id, 1)
        if last and last[-1]["role"] == "ceo" and last[-1]["text"] == msg:
            return  # rahbar javobi allaqachon yozilgan, takrorlamaymiz
        await app.store.add_chat(chat_id, "sys", msg)

    async def post_result(res: dict):
        if res.get("kind") != "task":
            return
        if res.get("error"):
            await app.store.add_chat(chat_id, "sys", res["error"])
        # qisqa ko'rinish: to'liq natija va fayllar vazifa sahifasida (chat qatori 4000 belgidan oshmasligi kerak)
        payload = {"task_id": res["task_id"], "status": res["status"], "text": (res.get("result") or "")[:500],
                   "files": res.get("files", [])[:8]}
        await app.store.add_chat(chat_id, "result", json.dumps(payload, ensure_ascii=False))

    async def report_failure(e: Exception):
        log.exception("veb vazifa/suhbat xatosi")
        try:
            await app.store.add_chat(chat_id, "sys", f"Xatolik: {e}")
        except Exception:  # noqa: BLE001 — baza ham ishlamasa, kamida logda qoladi
            log.exception("xatoni suhbatga yozib bo'lmadi")

    def text_of(d: dict) -> str:
        text = str(d.get("text", "")).strip()
        if not text or len(text) > 4000:
            raise web.HTTPBadRequest(reason="matn 1-4000 belgi bo'lishi kerak")
        return text

    async def h_chat_post(request):
        """Suhbat: oddiy xabarlar vazifa emas. Ish so'ralsa rahbar vazifa taklif qiladi."""
        d = await body(request)
        text = text_of(d)
        want_voice = await voicereply.wanted(app.store, text, bool(d.get("voice")))   # mikrofon bilan aytilgan bo'lsa: voice=true

        async def job():
            try:
                await app.orch.handle(text, chat_id, notify, allow_tasks=False, voice=want_voice)
            except Exception as e:  # noqa: BLE001
                await report_failure(e)
        spawn(job())
        return json_ok({"ok": True, "voice": want_voice})

    async def h_task_submit(request):
        """«Vazifa berish»: to'g'ridan-to'g'ri vazifa (suhbatsiz), ixtiyoriy biriktirilgan fayllar bilan."""
        d = await body(request)
        text = text_of(d)
        attachments = uploads_for(d.get("files"))
        based_on = d.get("based_on")
        if based_on is not None:
            if not isinstance(based_on, int) or not await app.store.get_task(based_on):
                raise web.HTTPBadRequest(reason="asos vazifa topilmadi")

        async def job():
            try:
                await post_result(await app.orch.submit_task(text, chat_id, notify, attachments or None, based_on))
            except Exception as e:  # noqa: BLE001
                await report_failure(e)
        spawn(job())
        return json_ok({"ok": True})

    async def get_task_or_404(request):
        t = await app.store.get_task(int(request.match_info["id"]))
        if not t:
            raise web.HTTPNotFound(reason="topilmadi")
        return t

    async def h_task_stop(request):
        t = await get_task_or_404(request)
        if app.orch.stop_task(t["id"]):
            return json_ok({"ok": True})
        if t["status"] == "running":  # jarayon yo'q (dastur qayta ishga tushgan): belgini tuzatamiz
            await app.store.update_task(t["id"], status="interrupted")
            return json_ok({"ok": True})
        raise web.HTTPConflict(reason="vazifa ishlamayapti")

    async def h_task_archive(request):
        t = await get_task_or_404(request)
        if t["status"] == "running":
            raise web.HTTPConflict(reason="avval vazifani to'xtating")
        await app.store.set_archived(t["id"], True)
        return json_ok({"ok": True})

    async def h_task_restore(request):
        t = await get_task_or_404(request)
        await app.store.set_archived(t["id"], False)
        return json_ok({"ok": True})

    async def h_task_delete(request):
        """Butunlay o'chirish: faqat arxivdagi vazifa."""
        t = await get_task_or_404(request)
        if not t["archived"] and t["status"] != "cancelled":
            raise web.HTTPConflict(reason="faqat arxivdagi vazifani butunlay o'chirish mumkin")
        ws = (s.workspace_dir / f"task_{t['id']}").resolve()
        if ws.is_dir() and ws.parent == s.workspace_dir.resolve():
            shutil.rmtree(ws, ignore_errors=True)
        await app.store.delete_task(t["id"])
        return json_ok({"ok": True})

    async def h_provider(request):
        """Asosiy AI: 'auto' (eng arzoni) yoki ulangan provayderlardan biri. Qayta ishga tushirish shart emas."""
        value = str((await body(request)).get("primary", "")).strip().lower()
        if value != "auto" and value not in app.router.providers:
            raise web.HTTPBadRequest(reason="bunday provayder ulanmagan (kalit yo'q)")
        await app.store.set_kv("primary_provider", value)
        return json_ok({"primary": value})

    async def h_free_ai(request):
        """Bepul AI (Groq / OpenRouter) yoqish-o'chirish: faqat oddiy, maxfiy bo'lmagan fon ishlari uchun."""
        d = await body(request)
        name = str(d.get("name") or "")
        if name not in app.router.FREE:
            raise web.HTTPBadRequest(reason="noma'lum bepul AI")
        if d.get("enabled") and name not in app.router.providers:
            raise web.HTTPBadRequest(reason=f"{name.upper()}_API_KEY .env da yo'q")
        await app.store.set_kv(f"{name}_on", "1" if d.get("enabled") else "0")
        app.router._free_down.pop(name, None)
        await app.store.audit("owner", name, "yoqildi" if d.get("enabled") else "o'chirildi")
        return json_ok(await app.router.free_status())

    async def h_voice(request):
        """Ovozli xabar -> matn (Gemini). Tana: audio baytlari, Content-Type: audio/*"""
        mime = request.headers.get("Content-Type", "audio/ogg").split(";")[0].strip()
        if not mime.startswith(("audio/", "video/webm", "video/mp4")):
            raise web.HTTPBadRequest(reason="audio fayl kerak")
        data = await request.read()
        if len(data) < 200:
            raise web.HTTPBadRequest(reason="ovoz juda qisqa")
        try:
            return json_ok({"text": await app.router.transcribe(data, mime)})
        except VoiceUnavailable as e:
            raise web.HTTPServiceUnavailable(reason=str(e))
        except VoiceError as e:
            raise web.HTTPUnprocessableEntity(reason=str(e))

    async def h_location(request):
        """Joylashuvni yuborish (brauzer geolokatsiyasi yoki iPhone Shortcuts): {"lat":..., "lon":...}"""
        from .tools_ext import save_location
        d = await body(request)
        try:
            lat, lon = float(d["lat"]), float(d["lon"])
        except (KeyError, TypeError, ValueError):
            raise web.HTTPBadRequest(reason="lat va lon kerak")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise web.HTTPBadRequest(reason="koordinata noto'g'ri")
        await save_location(app.store, lat, lon, bool(d.get("live")))
        return json_ok({"ok": True})

    # ---------- ulanishlar, joylashuv, hisobot, jurnal, xotira, fayl ----------
    def tool_env():
        from .tools import ToolEnv
        return ToolEnv(workspace=s.workspace_dir, store=app.store, settings=s)

    async def h_integrations(request):
        tg = app.tg
        return json_ok({
            "telegram_bot": bool(s.telegram_token),
            "telegram_account": {"configured": bool(tg and tg.configured()), "mode": (await tg.access(app.store, s) if tg else "read"),
                                 "keys": bool(tg and tg.has_keys()), "pending": bool(tg and tg.login_pending()),
                                 "proxy": bool(tg and tg.proxy), "login": (tg.login_info() if tg else {}),
                                 "listen": await listen_state(),
                                 "calls": await app.calls.status() if app.calls else {"available": False},
                                 "me": (tg.me if tg and tg.configured() else ""), "allowed": list(s.tg_allowed),
                                 "private_providers": list(s.private_providers)},
            "maps": "google" if s.google_maps_key else "osm",
            "voice": any(p.supports_audio for p in app.router.providers.values()),
            "stt": await stt_status(),
            "search": "brave" if s.brave_key else "duckduckgo",
            "providers": [{"name": n, "enabled": n in app.router.providers} for n in s.providers if n not in app.router.FREE],
            "free_ai": await app.router.free_status(),
            "voice_reply": await voicereply.mode(app.store),
            "primary": await app.router.primary(),
            "limits": {"task_usd": s.max_task_usd, "agents": s.max_agents, "parallel": s.max_parallel, "revisions": await app.orch._qa_rounds(),
                       "tool_turns": s.max_tool_turns, "command_s": s.command_timeout, "report": f"{s.report_hour}:00 ({s.report_tz})",
                       "tg_sends_per_hour": s.tg_max_sends},
        })

    async def listen_state() -> dict:
        lst = app.listener
        if lst is None:
            return {"enabled": False, "owners": [], "active": False, "error": ""}
        return {"enabled": await lst.enabled(), "owners": await lst.owner_ids(), "active": lst.active(), "error": lst.last_error}

    def kick_listener():
        if app.listener is not None:
            app.listener.kick()

    async def h_push_key(request):
        if not app.push or not push_available():
            raise web.HTTPServiceUnavailable(reason="Serverda bildirishnoma moduli (pywebpush) o'rnatilmagan")
        return json_ok({"key": await app.push.public_key()})

    async def h_push_subscribe(request):
        d = await body(request)
        try:
            n = await app.push.subscribe(d.get("subscription") or {}, request.headers.get("User-Agent", ""))
        except ValueError as e:
            raise web.HTTPBadRequest(reason=str(e))
        await app.store.audit("owner", "push_on", f"bildirishnoma yoqildi ({n} qurilma)")
        return json_ok({"devices": n})

    async def h_push_unsubscribe(request):
        d = await body(request)
        await app.push.unsubscribe(str(d.get("endpoint", "")))
        await app.store.audit("owner", "push_off", "bildirishnoma o'chirildi (qurilma)")
        return json_ok({"ok": True})

    async def h_push_prefs(request):
        return json_ok({"prefs": await app.push.set_prefs(await body(request))})

    async def h_push_test(request):
        n = await app.push.notify("done", "🔔 Sinov", "Bildirishnomalar ishlayapti", "/", force=True)
        if not n:
            raise web.HTTPBadGateway(reason="Hech qaysi qurilmaga yetmadi. Bildirishnomani qayta yoqing.")
        return json_ok({"sent": n})

    async def h_tg_calls(request):
        d = await body(request)
        for key, kv in (("enabled", "tg_calls"), ("notify", "tg_call_notify")):
            if key in d:
                await app.store.set_kv(kv, "1" if d[key] else "0")
                await app.store.audit("owner", kv, "yoqildi" if d[key] else "o'chirildi")
        kick_listener()
        return json_ok(await app.calls.status())

    async def h_tts_voice(request):
        from .voices import VOICES
        d = await body(request)
        if d.get("voice") not in VOICES:
            raise web.HTTPBadRequest(reason="noma'lum ovoz")
        await app.store.set_kv("tts_voice", d["voice"])
        if app.calls and app.calls._tgc is not None:
            app.calls._spawn(app.calls.warm_fillers())   # yangi ovozda "hmm"larni oldindan tayyorlash (bir marta)
        return json_ok({"voice": d["voice"]})

    async def h_tts_say(request):
        """Matnni ovozli xabar (mp3) qilib beradi: panel chatida javobni tinglash. Bir xil matn keshdan."""
        text = text_of(await body(request))
        audio = await voicereply.synth(app, text, "mp3")
        if not audio:
            raise web.HTTPBadRequest(reason="ovoz yaratib bo'lmadi (Gemini kaliti yoki Edge kerak)")
        return web.Response(body=audio, content_type="audio/mpeg", headers={"Cache-Control": "no-store"})

    async def h_qa_rounds(request):
        """QA e'tirozlarini tuzatish aylanishlari (0-6). Ko'proq = sifatliroq, lekin qimmatroq."""
        try:
            n = int((await body(request)).get("rounds"))
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(reason="0 dan 6 gacha son")
        if not 0 <= n <= app.orch.QA_MAX:
            raise web.HTTPBadRequest(reason="0 dan 6 gacha son")
        await app.store.set_kv("qa_rounds", str(n))
        return json_ok({"rounds": n})

    async def h_voice_reply(request):
        m = str((await body(request)).get("mode", ""))
        if m not in voicereply.MODES:
            raise web.HTTPBadRequest(reason="rejim: mirror, always yoki off")
        await app.store.set_kv("voice_reply", m)
        return json_ok({"mode": m})

    async def h_tts_mode(request):
        d = await body(request)
        if d.get("mode") not in app.router.TTS_MODES:
            raise web.HTTPBadRequest(reason="noma'lum rejim")
        eng = (await app.router.tts_status())["engines"]
        if not (any(e["ready"] for e in eng.values()) if d["mode"] == "auto" else eng[d["mode"]]["ready"]):
            raise web.HTTPBadRequest(reason="bu ovoz manbai mavjud emas (kalit yo'q yoki o'rnatilmagan)")
        await app.store.set_kv("tts_mode", d["mode"])
        app.router._tts_down.clear()
        if app.calls and app.calls._tgc is not None:
            app.calls._spawn(app.calls.warm_fillers())
        return json_ok(await app.router.tts_status())

    async def h_tts_preview(request):
        """Tanlangan ovozni eshitib ko'rish: WAV qaytaradi (Gemini TTS, bir necha sent ulushi)."""
        from .calls import OUT_RATE, pcm_to_wav
        from .voices import VOICES
        d = await body(request)
        voice = d.get("voice") if d.get("voice") in VOICES else None
        try:
            pcm = await app.router.speak("Assalomu alaykum, men sizning menejeringizman. Eshitaman.", voice=voice)
        except (VoiceUnavailable, VoiceError) as e:
            raise web.HTTPBadRequest(reason=str(e))
        return web.Response(body=pcm_to_wav(pcm, OUT_RATE), content_type="audio/wav")

    async def h_tg_call_test(request):
        c = app.calls
        if c is None or not c.available():
            raise web.HTTPBadRequest(reason="Qo'ng'iroq moduli (py-tgcalls) serverda o'rnatilmagan")
        if not await c.enabled():
            raise web.HTTPBadRequest(reason="Avval qo'ng'iroqni yoqing")
        if c._tgc is None:
            raise web.HTTPBadRequest(reason=c.last_error or "Telegram akkaunt hali ulanmagan yoki tinglash ishga tushmagan")
        ok = await c.call_owner("Salom! Bu agentdan sinov qo'ng'irog'i. Eshitayapsizmi? Savol bersangiz javob beraman.")
        if not ok:
            raise web.HTTPBadGateway(reason=c.last_error or "Qo'ng'iroq qilib bo'lmadi")
        return json_ok({"ok": True})

    async def h_tg_listen(request):
        """{"enabled": bool, "owners": "123, 456"}: agent akkaunti faqat shu chatlardan kelgan xabarga javob beradi."""
        from .tglisten import parse_ids
        d = await body(request)
        if "owners" in d:
            raw = str(d.get("owners") or "").strip()
            try:
                ids = parse_ids(raw)
            except ValueError as e:
                raise web.HTTPBadRequest(reason=f"ID noto'g'ri: {e}")
            if ids:
                await app.store.set_kv("tg_owner_ids", ",".join(map(str, ids)))
            else:
                await app.store.delete_kv("tg_owner_ids")
        if "enabled" in d:
            await app.store.set_kv("tg_listen", "1" if d["enabled"] else "0")
        await app.store.audit("owner", "tg_listen", json.dumps(d, ensure_ascii=False)[:200])
        kick_listener()
        return json_ok(await listen_state())

    # ---------- Telegram akkauntni panel orqali ulash ----------
    async def h_tg_keys(request):
        d = await body(request)
        kid, khash = str(d.get("api_id", "")).strip(), str(d.get("api_hash", "")).strip()
        if not kid.isdigit() or not re.fullmatch(r"[0-9a-zA-Z]{20,64}", khash):
            raise web.HTTPBadRequest(reason="api_id raqam, api_hash 32 belgili kod bo'lishi kerak (my.telegram.org)")
        await app.store.set_kv("tg_api_id", kid)
        await app.store.set_kv("tg_api_hash", khash)
        app.tg.api_id, app.tg.api_hash = int(kid), khash
        await app.store.audit("owner", "tg_keys", "Telegram API kalitlari panel orqali saqlandi")
        return json_ok({"ok": True})

    async def h_tg_code(request):
        d = await body(request)
        phone = re.sub(r"[\s()\-]", "", str(d.get("phone", "")))
        if "proxy" in d:  # ixtiyoriy: Telegram serveriga to'g'ridan-to'g'ri ulanib bo'lmasa
            from .tguser import parse_proxy
            proxy = str(d.get("proxy") or "").strip()
            try:
                parse_proxy(proxy)
            except ValueError as e:
                raise web.HTTPBadRequest(reason=f"Proksi noto'g'ri: {e}")
            if proxy:
                await app.store.set_kv("tg_proxy", proxy)
            else:
                await app.store.delete_kv("tg_proxy")
            app.tg.proxy = proxy or s.tg_proxy
        if not re.fullmatch(r"\+?\d{8,15}", phone):
            raise web.HTTPBadRequest(reason="telefon raqamini +998901234567 ko'rinishida yozing")
        try:
            await app.tg.login_start(phone if phone.startswith("+") else "+" + phone)
        except TgError as e:
            raise web.HTTPBadRequest(reason=str(e))
        return json_ok({"ok": True, **app.tg.login_info()})

    def qr_payload(st: dict) -> dict:
        if st.get("url"):
            try:
                import io

                import segno
            except ImportError:
                raise web.HTTPBadRequest(reason="QR uchun: pip install segno")
            buf = io.BytesIO()
            segno.make(st["url"], error="m").save(buf, kind="svg", scale=6, border=3, dark="#000", light="#fff")
            import base64
            st = {**st, "svg": "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()}
            st.pop("url")
        return st

    async def h_tg_qr_start(request):
        try:
            st = await app.tg.qr_start()
        except TgError as e:
            raise web.HTTPBadRequest(reason=str(e))
        return json_ok(qr_payload(st))

    async def h_tg_qr(request):
        st = app.tg.qr_state()
        if st["status"] == "ok":
            kick_listener()
            await app.store.set_kv("tg_me", app.tg.me)
            st["me"] = app.tg.me
        return json_ok(qr_payload(st))

    async def h_tg_resend(request):
        try:
            return json_ok({"ok": True, **await app.tg.login_resend()})
        except TgError as e:
            raise web.HTTPBadRequest(reason=str(e))

    async def h_tg_verify(request):
        d = await body(request)
        code, password = re.sub(r"\D", "", str(d.get("code", ""))), str(d.get("password", ""))
        try:
            status = await app.tg.login_verify(code, password)
        except TgError as e:
            raise web.HTTPBadRequest(reason=str(e))
        if status == "ok":
            kick_listener()
            await app.store.set_kv("tg_me", app.tg.me)
            await app.store.audit("owner", "tg_login", f"Telegram akkaunt ulandi: {app.tg.me}"[:200])
        return json_ok({"status": status, "me": app.tg.me if status == "ok" else ""})

    async def h_tg_me(request):
        """Hozir qaysi akkauntga ulanganini ko'rsatadi (eski tglogin sessiyasi bo'lishi mumkin)."""
        if app.tg.login_pending() or not app.tg.configured():  # kod kutilayotgan kirishni hech qachon buzmaymiz
            return json_ok({"me": "", "pending": app.tg.login_pending()})
        try:
            me = await (await app.tg.client()).get_me()
        except TgStale:  # kirilmagan qoldiq fayl: tozalaymiz, panel kirish qadamlarini ko'rsatadi
            await app.tg.logout()
            await app.store.delete_kv("tg_me")
            return json_ok({"me": "", "stale": True})
        except TgError as e:
            raise web.HTTPBadRequest(reason=str(e))
        app.tg.me = app.tg.name_of(me) + (f" (@{me.username})" if getattr(me, "username", None) else "")
        await app.store.set_kv("tg_me", app.tg.me)
        return json_ok({"me": app.tg.me})

    async def h_tg_access(request):
        mode = str((await body(request)).get("mode", ""))
        if mode not in ("read", "ask", "full"):
            raise web.HTTPBadRequest(reason="rejim read, ask yoki full bo'lishi kerak")
        await app.store.set_kv("tg_access", mode)
        await app.store.audit("owner", "tg_access", f"Telegram ruxsati: {mode}")
        return json_ok({"ok": True, "mode": mode})

    async def h_tg_logout(request):
        if app.listener is not None:
            app.listener.detach()
        await app.tg.logout()
        await app.store.delete_kv("tg_me")
        app.tg.me = ""
        await app.store.audit("owner", "tg_logout", "Telegram akkaunt uzildi")
        return json_ok({"ok": True})

    async def h_models(request):
        """`python -m aicompany check` ning paneldagi o'rni: kalit ishlayaptimi va models.yaml dagi nomlar haqiqiy modelga mosmi."""
        out = []
        for name, pc in s.providers.items():
            prov = app.router.providers.get(name)
            if not prov:
                out.append({"provider": name, "enabled": False, "tiers": []})
                continue
            try:
                available = set(await prov.list_models())
            except (ProviderError, NotImplementedError) as e:
                out.append({"provider": name, "enabled": True, "error": str(e)[:200], "tiers": []})
                continue
            tiers = []
            for tier, m in pc.models.items():
                fixed = await app.store.get_kv(f"model:{name}:{m.id}")
                found = m.id in available
                tiers.append({"tier": tier, "id": m.id, "found": found, "using": fixed or m.id, "fixed": bool(fixed),
                              "suggestion": None if found else suggest_model(m.id, sorted(available))})
            out.append({"provider": name, "enabled": True, "count": len(available), "tiers": tiers})
        return json_ok(out)

    async def h_widget_link(request):
        url, _ = web_url(s)
        base = url.split("/#token=")[0]
        return json_ok({"configured": bool(s.widget_token), "base_url": base, "token": s.widget_token or "",
                        "script": "docs/widget/ai-jamoa.js"})

    async def h_widget_script(request):
        """Scriptable skripti manzil va vidjet kaliti bilan to'ldirilgan holda (panelda «nusxalash» uchun)."""
        from .config import ROOT
        url, _ = web_url(s)
        src = (ROOT / "docs" / "widget" / "ai-jamoa.js").read_text(encoding="utf-8")
        src = src.replace('"https://SIZNING-MANZIL"', json.dumps(url.split("/#token=")[0]), 1)
        if s.widget_token:
            src = src.replace('"WIDGET_TOKEN_NI_SHU_YERGA"', json.dumps(s.widget_token), 1)
        return web.Response(text=src, content_type="text/plain", charset="utf-8")

    async def h_location_get(request):
        from .tools_ext import age_text, last_location
        loc = await last_location(app.store)
        places = {k: json.loads(v) for k, v in (await app.store.kv_prefix("place:")).items()}
        return json_ok({"last": ({**loc, "age": age_text(loc["ts"])} if loc else None),
                        "places": {k: {"label": p.get("label"), "lat": p["lat"], "lon": p["lon"]} for k, p in places.items()}})

    async def h_place(request):
        """Joy saqlash: {"name": "home", "address": "me" | manzil | "lat,lon"}"""
        from .tools_ext import save_place
        d = await body(request)
        try:
            msg = await save_place(tool_env(), {"name": str(d.get("name", "")), "address": str(d.get("address", ""))})
        except ToolError as e:
            raise web.HTTPBadRequest(reason=str(e))
        return json_ok({"ok": True, "message": msg})

    async def h_place_delete(request):
        await app.store.delete_kv(f"place:{request.match_info['name'].lower()}")
        return json_ok({"ok": True})

    async def h_review(request):
        n = len(await app.store.recent_task_ids(10))
        fired = await app.team.review(10)
        return json_ok({"fired": fired, "tasks": n})

    async def h_report(request):
        from datetime import timezone as _tz
        text = await build_report(app, datetime.now(_tz.utc) - timedelta(days=1), "Hisobot (so'nggi 24 soat)")
        return json_ok({"text": text})

    async def h_audit(request):
        rows = await app.store.recent_audit(60)
        return json_ok([{"ts": r["ts"], "actor": r["actor"], "action": r["action"], "detail": (r["detail"] or "")[:300]} for r in rows])

    async def h_memory_add(request):
        text = text_of(await body(request))
        await app.store.add_memory(text, source="owner")
        return json_ok({"ok": True})

    async def h_memory_delete(request):
        if not await app.store.delete_memory(int(request.match_info["id"])):
            raise web.HTTPNotFound(reason="topilmadi")
        return json_ok({"ok": True})

    async def h_chat_clear(request):
        await app.store.clear_chat(chat_id)
        return json_ok({"ok": True})

    async def h_upload(request):
        """Vazifaga fayl biriktirish: tana = fayl baytlari, ?name=asl nom. Kod qaytaradi."""
        raw = request.query.get("name", "file")
        safe = re.sub(r"[^\w.\- ]", "_", Path(raw).name)[:100] or "file"
        data = await request.read()
        if not data or len(data) > 10_000_000:
            raise web.HTTPBadRequest(reason="fayl 1 baytdan 10MB gacha bo'lishi kerak")
        token = secrets.token_hex(4)
        d = s.workspace_dir / "inbox" / token
        d.mkdir(parents=True, exist_ok=True)
        (d / safe).write_bytes(data)
        return json_ok({"id": token, "name": safe, "size": len(data)})

    def uploads_for(ids) -> list[Path]:
        out = []
        for token in (ids or [])[:8]:
            if not re.fullmatch(r"[0-9a-f]{8}", str(token)):
                raise web.HTTPBadRequest(reason="fayl kodi noto'g'ri")
            d = s.workspace_dir / "inbox" / token
            files = [f for f in d.iterdir() if f.is_file()] if d.is_dir() else []
            if not files:
                raise web.HTTPBadRequest(reason="yuklangan fayl topilmadi")
            out.append(files[0])
        return out

    async def h_reminders(request):
        from .reminders import local_text
        rows = await (app.store.list_reminders_all() if request.query.get("all") == "1" else app.store.list_reminders())
        return json_ok([{"id": r["id"], "text": r["text"], "due_at": r["due_at"], "local": local_text(r["due_at"], s.report_tz),
                         "status": r["status"], "call": bool(await app.store.get_kv(f"rcall:{r['id']}"))} for r in rows])

    PERIODS = ("day", "week", "month", "year", "other")

    def plan_out(p):
        try:
            items = json.loads(p["items"] or "[]")
        except ValueError:
            items = []
        return {"id": p["id"], "period": p["period"], "title": p["title"], "items": items, "target": p["target"],
                "done": sum(1 for i in items if i.get("done")), "total": len(items), "created_at": p["created_at"]}

    def clean_items(raw) -> str:
        out = []
        for it in (raw or [])[:60]:
            text, done = (it.get("text"), bool(it.get("done"))) if isinstance(it, dict) else (it, False)
            text = " ".join(str(text or "").split())[:300]
            if text:
                out.append({"text": text, "done": done})
        return json.dumps(out, ensure_ascii=False)

    async def h_plans(request):
        return json_ok([plan_out(p) for p in await app.store.list_plans()])

    async def h_plan_add(request):
        d = await body(request)
        title = " ".join(str(d.get("title", "")).split())[:200]
        period = d.get("period") if d.get("period") in PERIODS else "day"
        if not title:
            raise web.HTTPBadRequest(reason="reja nomini yozing")
        pid = await app.store.add_plan(period, title, clean_items(d.get("items")), str(d.get("target") or "")[:10] or None)
        return json_ok(plan_out(await app.store.get_plan(pid)))

    async def h_plan_update(request):
        pid = int(request.match_info["id"])
        if not await app.store.get_plan(pid):
            raise web.HTTPNotFound(reason="reja topilmadi")
        d = await body(request)
        fields = {}
        if "title" in d:
            t = " ".join(str(d["title"]).split())[:200]
            if not t:
                raise web.HTTPBadRequest(reason="reja nomi bo'sh bo'lmasin")
            fields["title"] = t
        if d.get("period") in PERIODS:
            fields["period"] = d["period"]
        if "items" in d:
            fields["items"] = clean_items(d["items"])
        if "target" in d:
            fields["target"] = str(d["target"] or "")[:10] or None
        if fields:
            await app.store.update_plan(pid, **fields)
        return json_ok(plan_out(await app.store.get_plan(pid)))

    async def h_plan_delete(request):
        if not await app.store.delete_plan(int(request.match_info["id"])):
            raise web.HTTPNotFound(reason="reja topilmadi")
        return json_ok({"ok": True})

    async def h_reminder_add(request):
        from . import reminders
        d = await body(request)
        try:
            r = await reminders.create(app.store, s, chat_id, str(d.get("text", "")), str(d.get("when", "")))
        except ValueError as e:
            raise web.HTTPBadRequest(reason=str(e))
        return json_ok(r)

    async def h_reminder_cancel(request):
        if not await app.store.cancel_reminder(int(request.match_info["id"])):
            raise web.HTTPNotFound(reason="kutilayotgan eslatma topilmadi")
        return json_ok({"ok": True})

    def watch_out(w):
        try:
            st = json.loads(w["state"] or "{}")
        except ValueError:
            st = {}
        return {"id": w["id"], "kind": w["kind"], "title": w["title"], "target": w["target"], "keywords": w["keywords"] or "",
                "description": w["description"] or "", "target_price": w["target_price"], "enabled": bool(w["enabled"]),
                "price": st.get("price"), "min": st.get("min"), "last_check": w["last_check"], "error": w["last_error"]}

    async def h_watches(request):
        hits = await app.store.watch_hits_recent(30)
        titles = {w["id"]: w["title"] for w in await app.store.list_watches()}
        return json_ok({"watches": [watch_out(w) for w in await app.store.list_watches()],
                        "hits": [{"watch": titles.get(h["watch_id"], "?"), "text": h["text"], "url": h["url"], "ts": h["ts"]} for h in hits]})

    def watch_fields(d, partial=False) -> dict:
        f = {}
        if not partial or "kind" in d:
            if d.get("kind") not in ("tg", "price"):
                raise web.HTTPBadRequest(reason="tur: tg yoki price")
            f["kind"] = d["kind"]
        if not partial or "target" in d:
            t = str(d.get("target", "")).strip()
            if not t:
                raise web.HTTPBadRequest(reason="kanal (@nom) yoki mahsulot havolasini yozing")
            if (d.get("kind") or "") == "price" and not t.startswith(("http://", "https://")):
                raise web.HTTPBadRequest(reason="mahsulot havolasi http(s):// bilan boshlansin")
            f["target"] = t[:500]
        for k, n in (("title", 120), ("keywords", 500), ("description", 500)):
            if k in d:
                f[k] = " ".join(str(d[k] or "").split())[:n]
        if "target_price" in d:
            try:
                f["target_price"] = float(d["target_price"]) if d["target_price"] not in (None, "") else None
            except (TypeError, ValueError):
                raise web.HTTPBadRequest(reason="kutilgan narx son bo'lsin")
        if "enabled" in d:
            f["enabled"] = 1 if d["enabled"] else 0
        return f

    async def h_watch_add(request):
        d = await body(request)
        f = watch_fields(d)
        f.setdefault("title", f["target"][:60])
        if not f.get("title"):
            f["title"] = f["target"][:60]
        wid = await app.store.add_watch(**f)
        await app.store.audit("owner", "watch_add", f"{f['kind']}: {f['target']}"[:200])
        return json_ok(watch_out(await app.store.get_watch(wid)))

    async def h_watch_update(request):
        wid = int(request.match_info["id"])
        if not await app.store.get_watch(wid):
            raise web.HTTPNotFound(reason="kuzatuv topilmadi")
        f = watch_fields(await body(request), partial=True)
        if f:
            await app.store.update_watch(wid, **f)
        return json_ok(watch_out(await app.store.get_watch(wid)))

    async def h_watch_delete(request):
        if not await app.store.delete_watch(int(request.match_info["id"])):
            raise web.HTTPNotFound(reason="kuzatuv topilmadi")
        return json_ok({"ok": True})

    async def h_watch_check(request):
        w = await app.store.get_watch(int(request.match_info["id"]))
        if not w:
            raise web.HTTPNotFound(reason="kuzatuv topilmadi")
        hits = await app.watch.check(w)
        w = await app.store.get_watch(w["id"])
        return json_ok({"hits": hits, "watch": watch_out(w)})

    async def h_watch_kind(request):
        d = await body(request)
        if d.get("kind") not in ("tg", "price"):
            raise web.HTTPBadRequest(reason="tur: tg yoki price")
        on = bool(d.get("enabled"))
        await app.store.set_kv(f"watch_on:{d['kind']}", "1" if on else "0")
        await app.store.audit("owner", "watch_kind", f"{d['kind']}: {'yoqildi' if on else 'o`chirildi'}")
        return json_ok({"kind": d["kind"], "enabled": on})

    async def h_watch_smart(request):
        on = bool((await body(request)).get("enabled"))
        await app.store.set_kv("watch_smart", "1" if on else "0")
        await app.store.audit("owner", "watch_smart", "yoqildi" if on else "o'chirildi")
        return json_ok({"watch_smart": on})

    async def stt_status():
        from .stt import SpeechChain
        return await SpeechChain(app.router).status()

    async def h_stt(request):
        from .stt import ORDER
        mode = str((await body(request)).get("mode", ""))
        if mode not in ("auto", *ORDER):
            raise web.HTTPBadRequest(reason="rejim: auto, google, azure, groq, whisper yoki gemini")
        if mode != "auto" and not ((await stt_status())["services"].get(mode) or {}).get("ready"):
            raise web.HTTPBadRequest(reason="bu xizmat tayyor emas (kalit yo'q yoki o'chiq)")
        await app.store.set_kv("stt_mode", mode)
        await app.store.audit("owner", "stt_mode", mode)
        return json_ok(await stt_status())

    async def h_morning(request):
        d = await body(request)
        if "on" in d:
            await app.store.set_kv("morning", "1" if d["on"] else "0")
        if "time" in d:
            t = str(d["time"]).strip()
            if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", t):
                raise web.HTTPBadRequest(reason="vaqt HH:MM ko'rinishida bo'lsin (masalan 08:00)")
            h, m = t.split(":")
            await app.store.set_kv("morning_time", f"{int(h):02d}:{m}")
            await app.store.delete_kv("morning_sent")   # yangi vaqt bugun ham ishlashi uchun
        await app.store.audit("owner", "morning", json.dumps(d, ensure_ascii=False)[:200])
        return json_ok(await morning_settings(app.store))

    async def h_morning_test(request):
        from .briefing import send
        text = await send(app, [lambda t: app.store.add_chat(s.owner_id or 0, "sys", t)])
        return json_ok({"text": text})

    async def h_bot_push(request):
        mode = str((await body(request)).get("mode", ""))
        if mode not in ("all", "result", "off"):
            raise web.HTTPBadRequest(reason="mode: all | result | off")
        await app.store.set_kv("bot_push", mode)
        await app.store.audit("owner", "bot_push", f"bot xabarlari: {mode}")
        return json_ok({"bot_push": mode})

    async def h_eco(request):
        on = bool((await body(request)).get("enabled"))
        await app.store.set_kv("eco", "1" if on else "0")
        await app.store.audit("owner", "eco", "tejamkor rejim " + ("yoqildi" if on else "o'chirildi (sifat rejimi)"))
        return json_ok({"eco": on})

    async def h_pause(request):
        await app.store.set_kv("paused", "1")
        return json_ok({"paused": True})

    async def h_resume(request):
        await app.store.set_kv("paused", "0")

        async def job():  # pauza tufayli to'xtagan vazifalar o'zi davom etadi
            try:
                await app.orch.resume_stopped(notify, ("paused",), delay=0)
            except Exception as e:  # noqa: BLE001
                await report_failure(e)
        spawn(job())
        return json_ok({"paused": False})

    async def h_memory(request):
        return json_ok([{"id": m["id"], "text": m["text"], "source": m["source"]}
                        for m in await app.store.recent_memories(50)])

    async def h_agent_spend(request):
        return json_ok([{"agent": r["agent"], "cost": round(float(r["cost"]), 4)} for r in await app.store.spent_by_agent()])

    async def h_widget(request):
        from .reminders import local_text
        st = await state_data()
        recent = await app.store.list_tasks(12)
        left = sum(b["budget"] - b["spent"] for b in st["budgets"] if b["enabled"])
        total = sum(b["budget"] for b in st["budgets"] if b["enabled"])
        rems = await app.store.list_reminders(limit=3)
        loc = None
        try:
            from .tools_ext import age_text, last_location
            l = await last_location(app.store)
            loc = {"age": age_text(l["ts"])} if l else None
        except Exception:  # noqa: BLE001 — joylashuv vidjet uchun shart emas
            pass
        return json_ok({
            "working": [w["agent"] for w in st["working"]], "pending": st["pending"], "today": st["today"],
            "budget_left": round(left, 2), "budget_total": round(total, 2), "paused": st["paused"],
            "done_today": st["done_today"], "running": [{"id": t["id"], "request": t["request"][:70]} for t in st["running_tasks"][:3]],
            "failed": sum(1 for t in recent if t["status"] == "failed"),
            "next_reminder": ({"text": rems[0]["text"][:60], "local": local_text(rems[0]["due_at"], s.report_tz)} if rems else None),
            "location": loc,
            "last_task": ({"id": recent[0]["id"], "status": recent[0]["status"], "request": recent[0]["request"][:60]} if recent else None),
            **(await widget_v2(st, recent))})

    async def widget_header() -> dict:
        """Sana, ob-havo va dollar kursi: soatiga bir marta yangilanadi (kesh), tarmoq sekin bo'lsa eski qiymat."""
        from .briefing import owner_point, rates, weather
        tz = ZoneInfo(s.report_tz)
        now = datetime.now(tz)
        days = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]
        months = ["yan", "fev", "mar", "apr", "may", "iyun", "iyul", "avg", "sen", "okt", "noy", "dek"]
        out = {"date": f"{days[now.weekday()]} {now.day}-{months[now.month - 1]}", "weather": "", "usd": ""}
        try:
            cached = json.loads(await app.store.get_kv("widget_wx") or "{}")
        except ValueError:
            cached = {}
        fresh = cached.get("ts") and datetime.now(timezone.utc) - datetime.fromisoformat(cached["ts"]) < timedelta(hours=1)
        if not fresh:
            try:
                lat, lon, _ = await owner_point(app.store)
                async with httpx.AsyncClient(timeout=6) as client:
                    w, r = await asyncio.gather(weather(lat, lon, s.report_tz, client), rates(client), return_exceptions=True)
                if isinstance(w, dict) and w.get("max") is not None:
                    cached["weather"] = f"{w.get('icon', '')} {round(w['max'])}°"
                if isinstance(r, dict) and "USD" in r:
                    cached["usd"] = f"{r['USD']['rate']:,.0f}".replace(",", " ")
                cached["ts"] = datetime.now(timezone.utc).isoformat()
                await app.store.set_kv("widget_wx", json.dumps(cached, ensure_ascii=False))
            except Exception:  # noqa: BLE001 — vidjet ob-havosiz ham chiqadi
                pass
        out.update({k: cached.get(k, "") for k in ("weather", "usd")})
        return out

    async def widget_v2(st, recent) -> dict:
        """Birlashgan vidjet (jonli jamoa + moliya + mening kunim). AI ishlatilmaydi."""
        from .reminders import local_text
        from .team import AGENT_UZ
        tz = ZoneInfo(s.report_tz)
        now_l = datetime.now(tz)
        # --- jonli: ishlayotgan vazifalar va xodimlar ---
        live = []
        for t in st["running_tasks"][:3]:
            tid = t["id"]
            row = await app.store.get_task(tid)
            try:
                steps_total = len(json.loads(row["plan"] or "{}").get("steps") or [])
            except (ValueError, TypeError, AttributeError):
                steps_total = 0
            try:
                done = len([k for k in json.loads(await app.store.get_kv(f"ckpt:{tid}") or "{}").get("outputs", {}) if not k.startswith("qa_fix")])
            except (ValueError, TypeError, AttributeError):
                done = 0
            try:
                elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(row["created_at"])).total_seconds() / 60
            except (ValueError, TypeError):
                elapsed = 0
            eta = round(elapsed / done * (steps_total - done) + 1) if done and steps_total >= done else None
            by_agent = await app.store.spent_task_by_agent(tid)
            agents = []
            for name, b in app.team.busy.items():
                if b.get("task_id") != tid:
                    continue
                act = app.team.activity.get(name, {})
                agents.append({"name": name, "label": AGENT_UZ.get(name, name), "act": act.get("text") or "🤔 o'ylayapti",
                               "provider": act.get("provider") or "", "cost": round(by_agent.get(name, 0.0), 2)})
            live.append({"id": tid, "request": t["request"][:70], "steps_total": steps_total, "steps_done": min(done, steps_total),
                         "elapsed_min": round(elapsed), "eta_min": eta, "cost": round(sum(by_agent.values()), 2),
                         "limit": s.max_task_usd, "phase": app.orch.phase.get(tid, ""), "agents": agents[:3]})
        # --- moliya ---
        month_start = now_l.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month = sum(r["cost"] for r in await app.store.daily_spend_local(month_start.astimezone(timezone.utc).isoformat(), tz))
        import calendar
        days_in = calendar.monthrange(now_l.year, now_l.month)[1]
        forecast = month / max(now_l.day - 1 + now_l.hour / 24, 0.5) * days_in if month else 0.0
        names = {"anthropic": "Claude", "gemini": "Gemini", "openai": "ChatGPT"}
        providers = [{"name": names.get(b["provider"], b["provider"]), "spent": round(b["spent"], 2), "budget": b["budget"]}
                     for b in st["budgets"] if b["enabled"]][:3]
        # --- mening kunim ---
        day_start = now_l.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
        today_tasks = sorted((t for t in await app.store.list_tasks(40) if (t["created_at"] or "") >= day_start), key=lambda t: t["id"])
        timeline = [("done" if t["status"] == "done" else "running" if t["status"] == "running" else
                     "failed" if t["status"] in ("failed", "limit", "interrupted") else "other") for t in today_tasks][-10:]
        rems = []
        for r in await app.store.list_reminders(limit=4):
            rems.append({"time": local_text(r["due_at"], s.report_tz)[-5:], "day": local_text(r["due_at"], s.report_tz)[:5],
                         "text": r["text"][:60], "call": (await app.store.get_kv(f"rcall:{r['id']}")) == "1"})
        plan = None
        for p in await app.store.list_plans(20):
            if p["period"] == "day":
                try:
                    items = json.loads(p["items"] or "[]")
                except ValueError:
                    items = []
                if items:
                    plan = {"title": p["title"][:50], "done": sum(1 for i in items if i.get("done")), "total": len(items)}
                    break
        hit = None
        try:
            h = json.loads(await app.store.get_kv("watch_last_hit") or "null")
            if h and datetime.now(timezone.utc) - datetime.fromisoformat(h["ts"]) < timedelta(hours=24):
                hit = {"text": h["text"]}
        except (ValueError, TypeError, KeyError):
            pass
        auto = []
        if app.auto:
            for j in await app.auto.list():
                if j["mode"] != "off":
                    auto.append(j["title"].split()[0] + (" ✅" if j["result"].startswith("✅") or not j["result"] else " ⚠️"))
        done_recent = [t for t in recent if t["status"] == "done"][:2]
        costs = await app.store.spent_by_task([t["id"] for t in done_recent])
        return {"v": 2, "header": await widget_header(), "live": live,
                "money": {"today": round(st["today"], 2), "month": round(month, 2), "forecast": round(forecast, 2), "providers": providers},
                "day": {"timeline": timeline, "reminders": rems[:2], "plan": plan, "watch_hit": hit, "auto": " · ".join(auto)},
                "recent_done": [{"id": t["id"], "request": t["request"][:60], "cost": round(costs.get(t["id"], 0.0), 2)} for t in done_recent]}

    async def h_selfcheck(request):
        """Sozlamalar → «Tizimni tekshirish»: hamma qism bir joyda, AI so'rovisiz (token sarflanmaydi)."""
        out = []

        def add(name, ok, note):
            out.append({"name": name, "ok": ok, "note": note})
        t0 = time.monotonic()
        try:
            await app.store.get_kv("health")
            add("Baza", True, f"{'Supabase/Postgres' if app.store.remote else 'SQLite'}, javob {round((time.monotonic() - t0) * 1000)} ms")
        except Exception as e:  # noqa: BLE001
            add("Baza", False, str(e)[:120])
        provs = sorted(app.router.providers)
        add("AI kalitlari", bool(provs), ", ".join(PROV_NAMES.get(p, p) for p in provs) or "hech biri ulanmagan")
        st = await app.router.status()
        low = [n for n, v in st.items() if v["enabled"] and v["budget"] and v["spent"] >= v["budget"] * 0.9]
        add("Byudjet", not low, "yetarli" if not low else "90% dan oshdi: " + ", ".join(low))
        add("Telegram bot", bool(s.telegram_token), "ulangan" if s.telegram_token else "TELEGRAM_BOT_TOKEN yo'q")
        tg, lst = app.tg, app.listener
        if tg and tg.configured():
            add("Telegram akkaunt", bool(lst and lst.active()), (tg.me or "ulangan") + (" · tinglayapti" if lst and lst.active() else " · tinglash ishlamayapti"))
        else:
            add("Telegram akkaunt", None, "ulanmagan (ixtiyoriy)")
        st_stt = await stt_status()
        ready = [v["name"] for v in st_stt["services"].values() if v["ready"]]
        add("Ovozni tanish", bool(ready), ("avto: " if st_stt["mode"] == "auto" else "tanlangan: " + st_stt["mode"] + " · ") + (", ".join(ready) or "hech biri ulanmagan"))
        if app.calls:
            c = await app.calls.status()
            add("Ovozli qo'ng'iroq", (c.get("ready") or None) if c.get("enabled") else None,
                "tayyor" if c.get("ready") else c.get("error") or ("o'chiq" if not c.get("enabled") else
                "Telegram akkaunt ulanmagan" if not (tg and tg.configured()) else "ishga tushmoqda"))
        if app.push:
            n = len(await app.push.subs())
            add("Bildirishnomalar", n > 0 or None, f"{n} ta qurilma" if n else "hech qaysi qurilmada yoqilmagan")
        from .briefing import settings_of
        m = await settings_of(app.store)
        add("Ertalabki xulosa", m["on"] or None, f"har kuni {m['time']}" if m["on"] else "o'chiq")
        bdir = Path("/opt/aijamoa/backups")
        if bdir.is_dir():
            files = sorted(bdir.glob("aijamoa-*"), key=lambda f: f.stat().st_mtime)
            if files:
                age_h = (time.time() - files[-1].stat().st_mtime) / 3600
                add("Zaxira nusxa", age_h < 30, f"oxirgisi {round(age_h)} soat oldin, jami {len(files)} ta")
            else:
                add("Zaxira nusxa", False, "nusxa yo'q")
        else:
            add("Zaxira nusxa", None, "sozlanmagan (deploy/install-backup.sh)")
        try:
            du = shutil.disk_usage(s.workspace_dir)
            free = du.free / 2**30
            add("Disk", free > 2, f"{free:.1f} GB bo'sh")
        except OSError:
            pass
        add("Versiya", True, f"{VERSION} · ishlayapti {round((time.monotonic() - STARTED) / 3600, 1)} soat")
        return json_ok(out)

    async def h_health(request):
        """Tokensiz: xosting (Render) va UptimeRobot uchun. Maxfiy ma'lumot qaytarmaydi."""
        ok = True
        try:
            await app.store.get_kv("health")
        except Exception:  # noqa: BLE001 — baza ishlamasa 503
            ok = False
        lst = app.listener
        return json_ok({"ok": ok, "version": VERSION, "uptime_s": int(time.monotonic() - STARTED),
                        "running_tasks": len(app.orch.running), "providers": len(app.router.providers),
                        "telegram_account": bool(lst and lst.active())}, 200 if ok else 503)

    a = web.Application(middlewares=[guard], client_max_size=16_000_000)
    for path in STATIC_FILES:
        a.router.add_get(path, static)
    a.add_routes([
        web.get("/api/state", h_state), web.get("/api/selfcheck", h_selfcheck), web.get("/api/team", h_team), web.get("/api/overview", h_overview),
        web.post("/api/team/hire", h_hire), web.post("/api/team/fire", h_fire), web.get("/api/org", h_org),
        web.post("/api/team/meta", h_agent_meta), web.post("/api/auto", h_auto), web.post("/api/auto/run", h_auto_run),
        web.post("/api/dept_leads", h_dept_leads),
        web.get("/api/tasks", h_tasks), web.get(r"/api/tasks/{id:\d+}", h_task),
        web.get(r"/api/tasks/{id:\d+}/files/{path:.+}", h_file),
        web.get("/api/approvals", h_approvals), web.post(r"/api/approvals/{id:\d+}", h_decide),
        web.get("/api/chat", h_chat_get), web.post("/api/chat", h_chat_post),
        web.post("/api/tasks", h_task_submit),
        web.post(r"/api/tasks/{id:\d+}/stop", h_task_stop), web.post(r"/api/tasks/{id:\d+}/archive", h_task_archive),
        web.post(r"/api/tasks/{id:\d+}/restore", h_task_restore), web.delete(r"/api/tasks/{id:\d+}", h_task_delete),
        web.post("/api/pause", h_pause), web.post("/api/eco", h_eco), web.post("/api/bot_push", h_bot_push), web.post("/api/morning", h_morning), web.post("/api/stt", h_stt), web.get("/api/watches", h_watches), web.post("/api/watches", h_watch_add), web.post(r"/api/watches/{id:\d+}", h_watch_update), web.delete(r"/api/watches/{id:\d+}", h_watch_delete), web.post(r"/api/watches/{id:\d+}/check", h_watch_check), web.post("/api/watch_smart", h_watch_smart), web.post("/api/watch_kind", h_watch_kind), web.post("/api/morning/test", h_morning_test), web.post("/api/resume", h_resume),
        web.post("/api/provider", h_provider), web.post("/api/free_ai", h_free_ai), web.post("/api/voice", h_voice), web.post("/api/location", h_location),
        web.get("/api/memory", h_memory), web.post("/api/memory", h_memory_add), web.delete(r"/api/memory/{id:\d+}", h_memory_delete),
        web.get("/api/integrations", h_integrations), web.get("/api/models", h_models), web.get("/api/widget-link", h_widget_link), web.get("/api/widget-script", h_widget_script), web.get("/api/location", h_location_get),
        web.post("/api/tg/keys", h_tg_keys), web.post("/api/tg/code", h_tg_code),
        web.post("/api/tg/verify", h_tg_verify), web.post("/api/tg/resend", h_tg_resend),
        web.post("/api/tg/qr", h_tg_qr_start), web.get("/api/tg/qr", h_tg_qr), web.post("/api/tg/logout", h_tg_logout), web.post("/api/tg/access", h_tg_access), web.post("/api/tg/listen", h_tg_listen), web.post("/api/tg/calls", h_tg_calls), web.get("/api/push/key", h_push_key), web.post("/api/push/subscribe", h_push_subscribe), web.post("/api/push/unsubscribe", h_push_unsubscribe), web.post("/api/push/prefs", h_push_prefs), web.post("/api/push/test", h_push_test), web.post("/api/tg/call_test", h_tg_call_test), web.post("/api/tts/voice", h_tts_voice), web.post("/api/tts/mode", h_tts_mode), web.post("/api/tts/say", h_tts_say), web.post("/api/voice_reply", h_voice_reply), web.post("/api/qa_rounds", h_qa_rounds), web.post("/api/tts/preview", h_tts_preview), web.get("/api/tg/me", h_tg_me),
        web.post("/api/place", h_place), web.delete("/api/place/{name}", h_place_delete),
        web.post("/api/team/review", h_review), web.get("/api/report", h_report), web.get("/api/audit", h_audit),
        web.post("/api/chat/clear", h_chat_clear), web.post("/api/upload", h_upload),
        web.get("/api/reminders", h_reminders), web.get("/api/plans", h_plans), web.post("/api/plans", h_plan_add), web.post(r"/api/plans/{id:\d+}", h_plan_update), web.delete(r"/api/plans/{id:\d+}", h_plan_delete), web.post("/api/reminders", h_reminder_add),
        web.delete(r"/api/reminders/{id:\d+}", h_reminder_cancel), web.get("/api/spend", h_agent_spend),
        web.get("/api/widget", h_widget), web.get("/health", h_health), web.get("/healthz", h_health),
    ])
    return a


async def start_web(app: App) -> web.AppRunner:
    s = app.settings
    if not s.web_token:
        raise SystemExit("WEB_TOKEN yo'q: veb-panel tokensiz ishga tushmaydi")
    runner = web.AppRunner(make_web_app(app), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, s.web_host, s.web_port).start()
    return runner
