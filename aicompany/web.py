from __future__ import annotations

import asyncio
import hmac
import json
import logging
import shutil
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from aiohttp import web

from .app import App
from .report import day_start_utc
from .providers import VoiceError, VoiceUnavailable
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
STATIC = Path(__file__).parent / "webui"
STATIC_FILES = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css",
                "/sw.js": "sw.js", "/manifest.webmanifest": "manifest.webmanifest",
                "/icon-192.png": "icon-192.png", "/icon-512.png": "icon-512.png",
                "/apple-touch-icon.png": "apple-touch-icon.png"}
CTYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
          ".webmanifest": "application/manifest+json", ".png": "image/png"}
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; "
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
        try:
            resp = await handler(request)
        except web.HTTPException as e:
            if not path.startswith("/api/"):
                raise
            resp = json_ok({"error": e.reason}, e.status)
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
        return web.Response(body=f.read_bytes(), content_type=CTYPES[f.suffix].split(";")[0],
                            charset="utf-8" if f.suffix != ".png" else None,
                            headers={"Cache-Control": "no-cache"})

    # ---------- ma'lumot ----------
    async def today_spend() -> float:
        tz = ZoneInfo(s.report_tz)
        return await app.store.spent_since(day_start_utc(datetime.now(tz)).isoformat())

    async def state_data() -> dict:
        budgets = await app.router.status()
        recent = await app.store.list_tasks(100)
        running = [t for t in recent if t["status"] == "running"]
        day_iso = day_start_utc(datetime.now(ZoneInfo(s.report_tz))).isoformat()
        done_today = sum(1 for t in recent if t["status"] == "done" and (t["finished_at"] or "") >= day_iso)
        return {
            "paused": await app.store.get_kv("paused") == "1",
            "today": round(await today_spend(), 4),
            "budgets": [{"provider": n, **v} for n, v in budgets.items()],
            "working": [{"agent": a, "task_id": b["task_id"]} for a, b in app.team.busy.items()],
            "done_today": done_today,
            "running_tasks": [{"id": t["id"], "request": t["request"][:100]} for t in running],
            "pending": len(app.center.list()) if app.center else 0,
            "version": VERSION,
            "primary": await app.router.primary(),
            "providers": sorted(app.router.providers),
        }

    async def h_state(request):
        return json_ok(await state_data())

    async def h_team(request):
        costs = {r["agent"]: float(r["cost"]) for r in await app.store.spent_by_agent()}
        steps = await app.store.agent_message_counts()
        out = []
        for a in await app.store.list_agents():
            busy = app.team.busy.get(a["name"])
            out.append({"name": a["name"], "role": a["role"], "tier": a["tier"],
                        "tools": [t for t in a["tools"].split(",") if t], "created_by": a["created_by"],
                        "core": a["name"] in CORE, "busy": bool(busy), "task_id": busy["task_id"] if busy else None,
                        "cost": round(costs.get(a["name"], 0.0), 4), "steps": steps.get(a["name"], 0)})
        return json_ok(out)

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
        rows = await app.store.list_tasks(60, archived=archived)
        return json_ok([{"id": t["id"], "status": t["status"], "request": t["request"][:160],
                         "created_at": t["created_at"], "finished_at": t["finished_at"],
                         "archived": bool(t["archived"]), "note": t["note"],
                         "cost": round(await app.store.spent_task(t["id"]), 4)} for t in rows])

    def task_files(task_id: int) -> list[str]:
        ws = s.workspace_dir / f"task_{task_id}"
        return sorted(str(f.relative_to(ws)) for f in ws.rglob("*") if f.is_file()) if ws.is_dir() else []

    async def h_task(request):
        t = await app.store.get_task(int(request.match_info["id"]))
        if not t:
            raise web.HTTPNotFound(reason="topilmadi")
        msgs = await app.store.task_messages(t["id"])
        return json_ok({"id": t["id"], "status": t["status"], "request": t["request"], "result": t["result"],
                        "archived": bool(t["archived"]), "note": t["note"],
                        "approvals": [{"agent": a["agent"], "command": a["description"], "status": a["status"],
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
        text = text_of(await body(request))

        async def job():
            try:
                await app.orch.handle(text, chat_id, notify, allow_tasks=False)
            except Exception as e:  # noqa: BLE001
                await report_failure(e)
        spawn(job())
        return json_ok({"ok": True})

    async def h_task_submit(request):
        """«Vazifa berish»: to'g'ridan-to'g'ri vazifa (suhbatsiz)."""
        text = text_of(await body(request))

        async def job():
            try:
                await post_result(await app.orch.submit_task(text, chat_id, notify))
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
        if not t["archived"]:
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

    async def h_pause(request):
        await app.store.set_kv("paused", "1")
        return json_ok({"paused": True})

    async def h_resume(request):
        await app.store.set_kv("paused", "0")
        return json_ok({"paused": False})

    async def h_memory(request):
        return json_ok([{"id": m["id"], "text": m["text"], "source": m["source"]}
                        for m in await app.store.recent_memories(50)])

    async def h_agent_spend(request):
        return json_ok([{"agent": r["agent"], "cost": round(float(r["cost"]), 4)} for r in await app.store.spent_by_agent()])

    async def h_widget(request):
        st = await state_data()
        tasks = await app.store.list_tasks(1)
        left = sum(b["budget"] - b["spent"] for b in st["budgets"] if b["enabled"])
        return json_ok({"working": [w["agent"] for w in st["working"]], "pending": st["pending"],
                        "today": st["today"], "budget_left": round(left, 2), "paused": st["paused"],
                        "last_task": ({"id": tasks[0]["id"], "status": tasks[0]["status"],
                                       "request": tasks[0]["request"][:60]} if tasks else None)})

    a = web.Application(middlewares=[guard], client_max_size=16_000_000)
    for path in STATIC_FILES:
        a.router.add_get(path, static)
    a.add_routes([
        web.get("/api/state", h_state), web.get("/api/team", h_team),
        web.post("/api/team/hire", h_hire), web.post("/api/team/fire", h_fire),
        web.get("/api/tasks", h_tasks), web.get(r"/api/tasks/{id:\d+}", h_task),
        web.get(r"/api/tasks/{id:\d+}/files/{path:.+}", h_file),
        web.get("/api/approvals", h_approvals), web.post(r"/api/approvals/{id:\d+}", h_decide),
        web.get("/api/chat", h_chat_get), web.post("/api/chat", h_chat_post),
        web.post("/api/tasks", h_task_submit),
        web.post(r"/api/tasks/{id:\d+}/stop", h_task_stop), web.post(r"/api/tasks/{id:\d+}/archive", h_task_archive),
        web.post(r"/api/tasks/{id:\d+}/restore", h_task_restore), web.delete(r"/api/tasks/{id:\d+}", h_task_delete),
        web.post("/api/pause", h_pause), web.post("/api/resume", h_resume),
        web.post("/api/provider", h_provider), web.post("/api/voice", h_voice),
        web.get("/api/memory", h_memory), web.get("/api/spend", h_agent_spend),
        web.get("/api/widget", h_widget),
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
