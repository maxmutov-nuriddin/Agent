from __future__ import annotations

import asyncio
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

from .app import App
from .report import build_report, day_start_utc
from .providers import ProviderError, VoiceError, VoiceUnavailable, suggest_model
from .tguser import TgError
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
                         "archived": bool(t["archived"]), "note": t["note"], "based_on": t["based_on"],
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
        text = text_of(await body(request))

        async def job():
            try:
                await app.orch.handle(text, chat_id, notify, allow_tasks=False)
            except Exception as e:  # noqa: BLE001
                await report_failure(e)
        spawn(job())
        return json_ok({"ok": True})

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
            "telegram_account": {"configured": bool(tg and tg.configured()), "mode": s.tg_mode,
                                 "keys": bool(tg and tg.has_keys()), "pending": bool(tg and tg.login_pending()),
                                 "me": (tg.me if tg and tg.configured() else ""), "allowed": list(s.tg_allowed),
                                 "private_providers": list(s.private_providers)},
            "maps": "google" if s.google_maps_key else "osm",
            "voice": any(p.supports_audio for p in app.router.providers.values()),
            "search": "brave" if s.brave_key else "duckduckgo",
            "providers": [{"name": n, "enabled": n in app.router.providers} for n in s.providers],
            "primary": await app.router.primary(),
            "limits": {"task_usd": s.max_task_usd, "agents": s.max_agents, "parallel": s.max_parallel, "revisions": s.max_revisions,
                       "tool_turns": s.max_tool_turns, "command_s": s.command_timeout, "report": f"{s.report_hour}:00 ({s.report_tz})",
                       "tg_sends_per_hour": s.tg_max_sends},
        })

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
        phone = re.sub(r"[\s()\-]", "", str((await body(request)).get("phone", "")))
        if not re.fullmatch(r"\+?\d{8,15}", phone):
            raise web.HTTPBadRequest(reason="telefon raqamini +998901234567 ko'rinishida yozing")
        try:
            await app.tg.login_start(phone if phone.startswith("+") else "+" + phone)
        except TgError as e:
            raise web.HTTPBadRequest(reason=str(e))
        return json_ok({"ok": True})

    async def h_tg_verify(request):
        d = await body(request)
        code, password = re.sub(r"\D", "", str(d.get("code", ""))), str(d.get("password", ""))
        try:
            status = await app.tg.login_verify(code, password)
        except TgError as e:
            raise web.HTTPBadRequest(reason=str(e))
        if status == "ok":
            await app.store.set_kv("tg_me", app.tg.me)
            await app.store.audit("owner", "tg_login", f"Telegram akkaunt ulandi: {app.tg.me}"[:200])
        return json_ok({"status": status, "me": app.tg.me if status == "ok" else ""})

    async def h_tg_logout(request):
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
        rows = await app.store.list_reminders()
        return json_ok([{"id": r["id"], "text": r["text"], "due_at": r["due_at"], "local": local_text(r["due_at"], s.report_tz)} for r in rows])

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
        web.post("/api/provider", h_provider), web.post("/api/voice", h_voice), web.post("/api/location", h_location),
        web.get("/api/memory", h_memory), web.post("/api/memory", h_memory_add), web.delete(r"/api/memory/{id:\d+}", h_memory_delete),
        web.get("/api/integrations", h_integrations), web.get("/api/models", h_models), web.get("/api/widget-link", h_widget_link), web.get("/api/location", h_location_get),
        web.post("/api/tg/keys", h_tg_keys), web.post("/api/tg/code", h_tg_code),
        web.post("/api/tg/verify", h_tg_verify), web.post("/api/tg/logout", h_tg_logout),
        web.post("/api/place", h_place), web.delete("/api/place/{name}", h_place_delete),
        web.post("/api/team/review", h_review), web.get("/api/report", h_report), web.get("/api/audit", h_audit),
        web.post("/api/chat/clear", h_chat_clear), web.post("/api/upload", h_upload),
        web.get("/api/reminders", h_reminders), web.post("/api/reminders", h_reminder_add),
        web.delete(r"/api/reminders/{id:\d+}", h_reminder_cancel), web.get("/api/spend", h_agent_spend),
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
