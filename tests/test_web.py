import asyncio
import dataclasses
import json
import re
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aicompany.web import make_web_app

from .conftest import scripted_company

TOK = {"Authorization": "Bearer web-secret"}


def front(base=None):
    base = base or scripted_company()

    def handler(system, user, model):
        if "front desk" in system:
            latest = user.split("# Latest owner message\n")[-1]
            if "salom" in latest:
                return json.dumps({"mode": "chat", "reply": "Salom!"})
            return json.dumps({"mode": "task", "reply": "Boshladim", "task": latest})
        return base(system, user, model)
    return handler


@pytest.fixture
async def web(make_app):
    app, provs = await make_app(front(), OWNER_TELEGRAM_ID="1")
    app.settings = dataclasses.replace(app.settings, web_token="web-secret", widget_token="widget-secret")
    client = TestClient(TestServer(make_web_app(app)))
    await client.start_server()
    yield client, app
    await client.close()


async def get(c, path, **kw):
    r = await c.get(path, headers=TOK, **kw)
    return r.status, await r.json()


async def post(c, path, data=None, **kw):
    r = await c.post(path, headers=TOK, data=json.dumps(data or {}), **kw)
    return r.status, await r.json()


# ---------- xavfsizlik ----------
async def test_api_requires_token(web):
    c, _ = web
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "web-secret"}):
        assert (await c.get("/api/state", headers=headers)).status == 401
    assert (await c.post("/api/chat", data="{}")).status == 401
    assert (await c.get("/api/state", headers=TOK)).status == 200


async def test_failed_auth_is_rate_limited(web):
    c, _ = web
    codes = [(await c.get("/api/state", headers={"Authorization": "Bearer x"})).status for _ in range(10)]
    assert codes[:8] == [401] * 8 and codes[8:] == [429, 429]
    assert (await c.get("/api/state", headers=TOK)).status == 429  # to'g'ri kalit ham vaqtincha bloklanadi


async def test_widget_token_is_separate_and_read_only(web):
    c, _ = web
    r = await c.get("/api/widget?token=widget-secret")
    data = await r.json()
    assert r.status == 200 and set(data) >= {"working", "pending", "today", "budget_left", "last_task"}
    assert (await c.get("/api/widget?token=web-secret")).status == 401
    assert (await c.get("/api/state?token=widget-secret")).status == 401
    assert (await c.get("/api/team", headers={"Authorization": "Bearer widget-secret"})).status == 401


async def test_server_refuses_to_start_without_token(make_app):
    from aicompany.web import start_web
    app, _ = await make_app(lambda *a: "ok")
    with pytest.raises(SystemExit):
        await start_web(app)


async def test_static_files_and_security_headers(web):
    c, _ = web
    for path, ctype in [("/", "text/html"), ("/app.js", "javascript"), ("/style.css", "text/css"),
                        ("/manifest.webmanifest", "manifest+json"), ("/sw.js", "javascript"), ("/icon-192.png", "image/png"),
                        ("/apple-touch-icon.png", "image/png")]:
        r = await c.get(path)
        assert r.status == 200 and ctype in r.headers["Content-Type"], path
        assert "default-src 'self'" in r.headers["Content-Security-Policy"]
        assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"
    assert (await c.get("/etc/passwd")).status == 404
    assert (await c.get("/%2e%2e/%2e%2e/etc/passwd")).status == 404
    assert (await c.get("/api/state", headers=TOK)).headers["Cache-Control"] == "no-store"


def test_frontend_never_injects_data_via_innerhtml():
    js = (Path(__file__).parent.parent / "aicompany/webui/app.js").read_text()
    uses = [l for l in js.splitlines() if "innerHTML" in l and not l.lstrip().startswith("//")]
    assert len(uses) == 1 and "const svg" in uses[0]  # faqat o'zgarmas ikonkalar uchun yordamchi
    assert "eval(" not in js and "document.write" not in js
    html = (Path(__file__).parent.parent / "aicompany/webui/index.html").read_text()
    assert not re.search(r"<script(?![^>]*src=)", html) and " style=" not in html and " onclick=" not in html


async def test_bad_json_is_400(web):
    c, _ = web
    r = await c.post("/api/chat", headers=TOK, data="not json")
    assert r.status == 400 and "error" in await r.json()
    assert (await c.post("/api/chat", headers=TOK, data="[1]")).status == 400
    assert (await post(c, "/api/chat", {"text": ""}))[0] == 400
    assert (await post(c, "/api/chat", {"text": "x" * 5000}))[0] == 400


# ---------- ma'lumot ----------
async def test_state_team_and_hire_fire(web):
    c, app = web
    _, st = await get(c, "/api/state")
    assert st["paused"] is False and st["pending"] == 0 and st["budgets"][0]["provider"] == "anthropic"
    _, team = await get(c, "/api/team")
    assert {a["name"] for a in team} >= {"ceo", "developer"} and next(a for a in team if a["name"] == "ceo")["core"]
    assert (await post(c, "/api/team/fire", {"name": "ceo"}))[0] == 409  # asosiy xodim
    code, r = await post(c, "/api/team/hire", {"name": "seo_expert", "why": "SEO audits for websites"})
    assert code == 200 and r["name"] == "seo_expert"
    assert (await post(c, "/api/team/hire", {"name": "x", "why": "y"}))[0] == 400
    assert (await post(c, "/api/team/fire", {"name": "seo_expert"}))[0] == 200
    assert (await post(c, "/api/team/fire", {"name": "ghost"}))[0] == 409


async def test_busy_agents_visible(web):
    c, app = web
    app.team.busy["marketer"] = {"count": 1, "task_id": 7}
    _, st = await get(c, "/api/state")
    _, team = await get(c, "/api/team")
    assert st["working"] == [{"agent": "marketer", "task_id": 7}]
    assert next(a for a in team if a["name"] == "marketer")["busy"] is True


async def test_pause_resume_and_memory(web):
    c, app = web
    assert (await post(c, "/api/pause"))[1] == {"paused": True}
    assert (await get(c, "/api/state"))[1]["paused"] is True
    await post(c, "/api/resume")
    await app.store.add_memory("Owner sells coffee")
    assert (await get(c, "/api/memory"))[1][0]["text"] == "Owner sells coffee"


# ---------- vazifa va fayllar ----------
async def test_task_detail_and_safe_file_download(web):
    c, app = web
    res = await app.orch.run_task("x", 1)
    ws = app.settings.workspace_dir / f"task_{res['task_id']}"
    (ws / "site").mkdir(parents=True, exist_ok=True)
    (ws / "site" / "index.html").write_text("<script>alert(1)</script>")
    (app.settings.workspace_dir / "secret.txt").write_text("TOP SECRET")
    _, tasks = await get(c, "/api/tasks")
    assert tasks[0]["status"] == "done" and tasks[0]["cost"] > 0
    _, t = await get(c, f"/api/tasks/{res['task_id']}")
    assert t["result"] == "FINAL DELIVERABLE" and "site/index.html" in t["files"] and t["messages"]
    r = await c.get(f"/api/tasks/{res['task_id']}/files/site/index.html", headers=TOK)
    assert r.status == 200 and r.headers["Content-Type"] == "application/octet-stream"
    assert "attachment" in r.headers["Content-Disposition"]
    for evil in ("../secret.txt", "..%2Fsecret.txt", "%2e%2e/secret.txt", "site/../../secret.txt"):
        r = await c.get(f"/api/tasks/{res['task_id']}/files/{evil}", headers=TOK)
        assert r.status in (404, 400) and b"TOP SECRET" not in await r.read(), evil
    assert (await get(c, "/api/tasks/9999"))[0] == 404


# ---------- ruxsatlar ----------
async def test_approval_flow_through_web(web):
    c, app = web
    t = asyncio.create_task(app.center.ask(5, 1, "developer", "rm -rf build"))
    await asyncio.sleep(0.05)
    _, items = await get(c, "/api/approvals")
    assert items[0]["description"] == "rm -rf build" and items[0]["agent"] == "developer"
    assert (await get(c, "/api/state"))[1]["pending"] == 1
    assert (await post(c, "/api/approvals/5", {"approve": True}))[0] == 200
    assert await t is True
    assert (await post(c, "/api/approvals/5", {"approve": True}))[0] == 409  # allaqachon hal qilingan


async def test_approval_denied_via_web(web):
    c, app = web
    t = asyncio.create_task(app.center.ask(6, 1, "dev", "x"))
    await asyncio.sleep(0.05)
    await post(c, "/api/approvals/6", {"approve": False})
    assert await t is False


# ---------- suhbat ----------
async def wait_rows(c, n, after=0, tries=60):
    for _ in range(tries):
        await asyncio.sleep(0.05)
        _, rows = await get(c, f"/api/chat?after={after}")
        if len(rows) >= n:
            return rows
    return rows


async def test_chat_greeting_no_task_no_duplicate_rows(web):
    c, app = web
    assert (await post(c, "/api/chat", {"text": "salom"}))[0] == 200
    rows = await wait_rows(c, 2)
    await asyncio.sleep(0.2)
    _, rows = await get(c, "/api/chat")
    assert [(r["role"], r["text"]) for r in rows] == [("owner", "salom"), ("ceo", "Salom!")]
    assert await app.store.list_tasks() == []


async def test_chat_task_produces_result_row_and_after_cursor(web):
    c, app = web
    await post(c, "/api/chat", {"text": "sayt yasab ber"})
    for _ in range(100):
        await asyncio.sleep(0.05)
        _, rows = await get(c, "/api/chat")
        if any(r["role"] == "result" for r in rows):
            break
    roles = [r["role"] for r in rows]
    assert roles[0] == "owner" and "result" in roles and "sys" in roles
    payload = json.loads(next(r["text"] for r in rows if r["role"] == "result"))
    assert payload["status"] == "done" and payload["text"] == "FINAL DELIVERABLE" and payload["task_id"] == 1
    last = rows[-1]["id"]
    assert (await get(c, f"/api/chat?after={last}"))[1] == []  # kursor: yangi xabar yo'q
