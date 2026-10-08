from __future__ import annotations

import asyncio
import hmac
import json
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from aiohttp import web

from .app import App
from .report import day_start_utc
from .team import CORE
from .util import clip

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
        rows = await app.store.list_tasks(30)
        return json_ok([{"id": t["id"], "status": t["status"], "request": t["request"][:160],
                         "created_at": t["created_at"], "finished_at": t["finished_at"],
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
        return json_ok([{"id": r["id"], "role": r["role"], "text": r["text"], "ts": r["created_at"]} for r in rows])

    async def h_chat_post(request):
        text = str((await body(request)).get("text", "")).strip()
        if not text or len(text) > 4000:
            raise web.HTTPBadRequest(reason="matn 1-4000 belgi bo'lishi kerak")

        async def notify(msg: str):
            last = await app.store.recent_chat(chat_id, 1)
            if last and last[-1]["role"] == "ceo" and last[-1]["text"] == msg:
                return  # rahbar javobi allaqachon yozilgan, takrorlamaymiz
            await app.store.add_chat(chat_id, "sys", msg)

        async def job():
            try:
                res = await app.orch.handle(text, chat_id, notify)
                if res["kind"] == "task":
                    if res.get("error"):
                        await app.store.add_chat(chat_id, "sys", res["error"])
                    payload = {"task_id": res["task_id"], "status": res["status"], "text": res.get("result") or ""}
                    await app.store.add_chat(chat_id, "result", json.dumps(payload, ensure_ascii=False))
            except Exception as e:  # noqa: BLE001
                await app.store.add_chat(chat_id, "sys", f"Xatolik: {e}")
        t = asyncio.create_task(job())
        jobs.add(t)
        t.add_done_callback(jobs.discard)
        return json_ok({"ok": True})

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

    a = web.Application(middlewares=[guard], client_max_size=1_000_000)
    for path in STATIC_FILES:
        a.router.add_get(path, static)
    a.add_routes([
        web.get("/api/state", h_state), web.get("/api/team", h_team),
        web.post("/api/team/hire", h_hire), web.post("/api/team/fire", h_fire),
        web.get("/api/tasks", h_tasks), web.get(r"/api/tasks/{id:\d+}", h_task),
        web.get(r"/api/tasks/{id:\d+}/files/{path:.+}", h_file),
        web.get("/api/approvals", h_approvals), web.post(r"/api/approvals/{id:\d+}", h_decide),
        web.get("/api/chat", h_chat_get), web.post("/api/chat", h_chat_post),
        web.post("/api/pause", h_pause), web.post("/api/resume", h_resume),
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
