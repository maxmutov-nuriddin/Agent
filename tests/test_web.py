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
        if "front desk" in system or "in a CHAT" in system:
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
    assert st["done_today"] == 0
    await app.orch.run_task("x", 1)
    await post(c, "/api/resume")                              # yozuv so'rovi qisqa keshni tozalaydi
    assert (await get(c, "/api/state"))[1]["done_today"] == 1
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
async def wait_rows(c, n, after=0, tries=200):
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


async def test_chat_never_starts_tasks_but_proposes_them(web):
    c, app = web
    await post(c, "/api/chat", {"text": "sayt yasab ber"})
    rows = await wait_rows(c, 3)
    await asyncio.sleep(0.2)
    _, rows = await get(c, "/api/chat")
    assert [r["role"] for r in rows] == ["owner", "ceo", "proposal"]
    assert json.loads(rows[2]["text"]) == {"task": "sayt yasab ber", "based_on": None}
    assert await app.store.list_tasks() == []  # suhbat vazifa ochmadi


async def test_submit_task_runs_and_logs_result(web):
    c, app = web
    assert (await post(c, "/api/tasks", {"text": "sayt yasab ber"}))[0] == 200
    for _ in range(300):
        await asyncio.sleep(0.05)
        _, rows = await get(c, "/api/chat")
        if any(r["role"] == "result" for r in rows):
            break
    assert rows[0]["role"] == "owner" and rows[0]["text"] == "📌 sayt yasab ber"
    payload = json.loads(next(r["text"] for r in rows if r["role"] == "result"))
    assert payload["status"] == "done" and payload["text"] == "FINAL DELIVERABLE" and payload["task_id"] == 1
    assert not any(r["text"].startswith("[Vazifa #") for r in rows)  # ichki xulosa ko'rinmaydi
    assert any(m["role"] == "ceo" and m["text"].startswith("[Vazifa #") for m in await app.store.recent_chat(1, 20))  # lekin xotirada bor
    last = rows[-1]["id"]
    assert (await get(c, f"/api/chat?after={last}"))[1] == []  # kursor
    assert (await post(c, "/api/tasks", {"text": ""}))[0] == 400


# ---------- to'xtatish / arxiv / o'chirish ----------
async def blocked_company(make_app, gate):
    base = scripted_company()
    app, provs = await make_app(front(base), OWNER_TELEGRAM_ID="1")
    orig = app.router.call

    async def slow(*a, **k):
        if k.get("agent") == "researcher":
            await gate.wait()
        return await orig(*a, **k)
    app.router.call = slow
    app.settings = dataclasses.replace(app.settings, web_token="web-secret", widget_token="widget-secret")
    return app


async def test_stop_running_task_via_web(make_app):
    gate = asyncio.Event()
    app = await blocked_company(make_app, gate)
    client = TestClient(TestServer(make_web_app(app)))
    await client.start_server()
    try:
        asyncio.create_task(app.orch.run_task("x", 1))
        for _ in range(100):
            await asyncio.sleep(0.02)
            if "researcher" in app.team.busy:
                break
        assert (await post(client, "/api/tasks/1/archive"))[0] == 409  # ishlayotganni arxivlab bo'lmaydi
        assert (await post(client, "/api/tasks/1/stop"))[0] == 200
        for _ in range(100):
            await asyncio.sleep(0.02)
            if (await app.store.get_task(1))["status"] != "running":
                break
        t = await app.store.get_task(1)
        assert t["status"] == "cancelled" and t["note"] and app.team.busy == {} and app.orch.running == {}
        assert (await post(client, "/api/tasks/1/stop"))[0] == 409  # endi ishlamayapti
    finally:
        await client.close()


async def test_stop_marks_orphaned_running_task_interrupted(web):
    c, app = web
    tid = await app.store.create_task(1, "x")  # DB'da running, lekin jarayon yo'q
    assert (await post(c, f"/api/tasks/{tid}/stop"))[0] == 200
    assert (await app.store.get_task(tid))["status"] == "interrupted"


async def test_archive_restore_and_permanent_delete(web):
    c, app = web
    res = await app.orch.run_task("x", 1)
    tid = res["task_id"]
    ws = app.settings.workspace_dir / f"task_{tid}"
    (ws / "a.txt").write_text("x")
    assert ws.exists() and len((await get(c, "/api/tasks"))[1]) == 1

    r = await c.delete(f"/api/tasks/{tid}", headers=TOK)
    assert r.status == 409 and ws.exists()  # arxivlanmaguncha o'chirib bo'lmaydi

    assert (await post(c, f"/api/tasks/{tid}/archive"))[0] == 200
    assert (await get(c, "/api/tasks"))[1] == []                       # faol ro'yxatdan ketdi
    arch = (await get(c, "/api/tasks?archived=1"))[1]
    assert [t["id"] for t in arch] == [tid] and arch[0]["archived"] is True
    assert (await get(c, f"/api/tasks/{tid}"))[1]["archived"] is True  # ochib ko'rsa bo'ladi

    assert (await post(c, f"/api/tasks/{tid}/restore"))[0] == 200      # arxivdan qaytarish
    assert len((await get(c, "/api/tasks"))[1]) == 1 and (await get(c, "/api/tasks?archived=1"))[1] == []

    await post(c, f"/api/tasks/{tid}/archive")
    spent_before = await app.store.spent("anthropic")
    r = await c.delete(f"/api/tasks/{tid}", headers=TOK)
    assert r.status == 200
    assert not ws.exists() and await app.store.get_task(tid) is None and await app.store.task_messages(tid) == []
    assert await app.store.spent("anthropic") == spent_before          # sarf hisobi saqlanadi
    assert (await get(c, f"/api/tasks/{tid}"))[0] == 404
    assert (await post(c, "/api/tasks/999/archive"))[0] == 404


async def test_archived_tasks_hidden_from_widget_and_telegram_lists(web):
    c, app = web
    res = await app.orch.run_task("x", 1)
    await app.store.set_archived(res["task_id"], True)
    r = await c.get("/api/widget?token=widget-secret")
    assert (await r.json())["last_task"] is None
    assert await app.store.list_tasks() == []


# ---------- asosiy AI va ovoz ----------
@pytest.fixture
async def web2(make_app):
    app, provs = await make_app(front(), names=("anthropic", "gemini"), audio="Kofexona uchun reja tuz", OWNER_TELEGRAM_ID="1")
    app.settings = dataclasses.replace(app.settings, web_token="web-secret", widget_token="widget-secret")
    client = TestClient(TestServer(make_web_app(app)))
    await client.start_server()
    yield client, app, provs
    await client.close()


async def test_provider_switch_via_web(web2):
    c, app, provs = web2
    _, st = await get(c, "/api/state")
    assert st["providers"] == ["anthropic", "gemini"] and st["primary"] == "auto"
    assert (await post(c, "/api/provider", {"primary": "gemini"}))[0] == 200
    assert (await get(c, "/api/state"))[1]["primary"] == "gemini"
    assert (await app.router.call("cheap", "You are 'x'", [{"role": "user", "content": "hi"}])).provider == "gemini"
    assert (await post(c, "/api/provider", {"primary": "openai"}))[0] == 400   # kalit yo'q
    assert (await post(c, "/api/provider", {"primary": "auto"}))[0] == 200
    assert (await c.post("/api/provider", data="{}")).status == 401


async def test_voice_endpoint(web2):
    c, app, provs = web2
    audio = b"OggS" + b"\x00" * 500
    r = await c.post("/api/voice", headers={**TOK, "Content-Type": "audio/webm;codecs=opus"}, data=audio)
    assert r.status == 200 and (await r.json()) == {"text": "Kofexona uchun reja tuz"}
    assert provs["gemini"].audio_calls[0][1] == "audio/webm"                 # codec qismi olib tashlandi
    r = await c.post("/api/voice", headers={**TOK, "Content-Type": "text/plain"}, data=audio)
    assert r.status == 400
    r = await c.post("/api/voice", headers={**TOK, "Content-Type": "audio/ogg"}, data=b"x")
    assert r.status == 400
    assert (await c.post("/api/voice", headers={"Content-Type": "audio/ogg"}, data=audio)).status == 401
    provs["gemini"].audio_text = "[no speech]"
    r = await c.post("/api/voice", headers={**TOK, "Content-Type": "audio/ogg"}, data=audio)
    assert r.status == 422 and "topilmadi" in (await r.json())["error"]


async def test_voice_endpoint_without_gemini_key(web):
    c, app = web  # faqat Claude
    r = await c.post("/api/voice", headers={**TOK, "Content-Type": "audio/ogg"}, data=b"x" * 600)
    assert r.status == 503 and "GEMINI_API_KEY" in (await r.json())["error"]
    _, st = await get(c, "/api/state")
    assert st["providers"] == ["anthropic"]


def test_frontend_has_voice_and_provider_controls():
    root = Path(__file__).parent.parent / "aicompany/webui"
    js, html = (root / "app.js").read_text(), (root / "index.html").read_text()
    assert "chat-mic" in html and "/api/voice" in js and "/provider" in js and "MediaRecorder" in js
    assert '"Asosiy AI"' in js and "kaliti yo'q" in js  # tanlov bitta kalit bilan ham ko'rinadi


async def test_state_reports_running_version(web):
    c, _ = web
    st = (await get(c, "/api/state"))[1]
    assert isinstance(st["version"], str) and st["version"]
    assert "Versiya" in (Path(__file__).parent.parent / "aicompany/webui/app.js").read_text()


async def test_result_card_payload_stays_valid_json_even_for_huge_results(web):
    c, app = web
    app.orch.run_task_orig = app.orch.run_task
    big = "kod " * 5000

    async def fake_run_task(text, chat_id, notify, attachments=None, based_on=None):
        return {"task_id": 1, "status": "done", "result": big, "files": ["index.html", "css/style.css"], "workspace": ""}
    app.orch.run_task = fake_run_task
    await post(c, "/api/tasks", {"text": "sayt"})
    for _ in range(100):
        await asyncio.sleep(0.05)
        rows = (await get(c, "/api/chat"))[1]
        if any(r["role"] == "result" for r in rows):
            break
    payload = json.loads(next(r["text"] for r in rows if r["role"] == "result"))   # kesilmagan, yaroqli JSON
    assert payload["task_id"] == 1 and len(payload["text"]) == 500 and payload["files"] == ["index.html", "css/style.css"]


async def test_task_detail_lists_approval_decisions(web):
    c, app = web
    tid = await app.store.create_task(1, "x")
    a1 = await app.store.create_approval(tid, "developer", "rm -rf /")
    a2 = await app.store.create_approval(tid, "developer", "npm install")
    await app.store.decide_approval(a1, "denied")
    await app.store.decide_approval(a2, "expired")
    ap = (await get(c, f"/api/tasks/{tid}"))[1]["approvals"]
    assert [(a["command"], a["status"]) for a in ap] == [("rm -rf /", "denied"), ("npm install", "expired")]
    assert "Siz rad etdingiz" in (Path(__file__).parent.parent / "aicompany/webui/app.js").read_text()


async def test_location_endpoint_validates_and_stores(web):
    c, app = web
    assert (await post(c, "/api/location", {"lat": 41.31, "lon": 69.28, "live": True}))[0] == 200
    loc = json.loads(await app.store.get_kv("loc:last"))
    assert (loc["lat"], loc["lon"], loc["live"]) == (41.31, 69.28, True)
    for bad in ({}, {"lat": "x", "lon": 1}, {"lat": 95, "lon": 0}, {"lat": 0, "lon": 200}):
        assert (await post(c, "/api/location", bad))[0] == 400
    assert (await c.post("/api/location", data="{}")).status == 401


async def test_telegram_approval_card_has_its_own_kind(web):
    c, app = web
    t = asyncio.create_task(app.center.ask(9, 1, "assistant", "Kimga: Ali\n\nSalom", "telegram"))
    await asyncio.sleep(0.05)
    items = (await get(c, "/api/approvals"))[1]
    assert items[0]["kind"] == "telegram" and "Kimga: Ali" in items[0]["description"]
    await post(c, "/api/approvals/9", {"approve": True})
    assert await t is True
    js = (Path(__file__).parent.parent / "aicompany/webui/app.js").read_text()
    assert "Telegramda shu xabarni yuborishga ruxsat" in js


# ---------- panel: avval faqat Telegram/terminalda bo'lgan imkoniyatlar ----------
async def test_integrations_overview(web2):
    c, app, provs = web2
    d = (await get(c, "/api/integrations"))[1]
    assert d["telegram_account"].pop("listen")["active"] is False
    assert d["telegram_account"].pop("calls")["in_call"] is False
    assert d["telegram_account"] == {"configured": False, "keys": False, "pending": False, "proxy": False, "login": {}, "me": "", "mode": "read", "allowed": [],
                                     "private_providers": []}
    assert d["voice"] is True and d["maps"] == "osm" and d["search"] == "duckduckgo" and d["primary"] == "auto"
    assert [(p["name"], p["enabled"]) for p in d["providers"]] == [("anthropic", True), ("openai", False), ("gemini", True)]
    app.settings = dataclasses.replace(app.settings, google_maps_key="k", brave_key="b", tg_mode="write", tg_allowed=("ali",))
    c2 = TestClient(TestServer(make_web_app(app)))          # sozlamalar server yaratilganda o'qiladi
    await c2.start_server()
    try:
        d = (await get(c2, "/api/integrations"))[1]
        assert d["maps"] == "google" and d["search"] == "brave" and d["telegram_account"]["allowed"] == ["ali"]
        assert d["telegram_account"]["mode"] == "ask"
    finally:
        await c2.close()


async def test_location_and_home_from_the_panel(web):
    c, app = web
    assert (await get(c, "/api/location"))[1] == {"last": None, "places": {}}
    await post(c, "/api/location", {"lat": 41.3, "lon": 69.2})
    assert (await post(c, "/api/place", {"name": "home", "address": "me"}))[0] == 200            # hozirgi joylashuv = uy
    d = (await get(c, "/api/location"))[1]
    assert d["last"]["lat"] == 41.3 and d["last"]["age"] and d["places"]["home"]["lat"] == 41.3
    assert (await post(c, "/api/place", {"name": "work", "address": "41.1,69.0"}))[1]["ok"] is True
    assert (await post(c, "/api/place", {"name": "<x>", "address": "me"}))[0] == 400
    assert (await c.delete("/api/place/work", headers=TOK)).status == 200
    assert set((await get(c, "/api/location"))[1]["places"]) == {"home"}
    await app.store.delete_kv("loc:last")
    assert (await post(c, "/api/place", {"name": "home", "address": "me"}))[0] == 400             # joylashuv yo'q: tushunarli xato


async def test_hr_review_report_and_audit_from_the_panel(web):
    c, app = web
    r = (await post(c, "/api/team/review"))[1]
    assert r == {"fired": [], "tasks": 0}
    await app.orch.run_task("x", 1)
    text = (await get(c, "/api/report"))[1]["text"]
    assert "Hisobot" in text and "done: 1" in text and "anthropic" in text
    await app.store.audit("assistant", "tg_send", "APPROVED: Kimga: Ali")
    rows = (await get(c, "/api/audit"))[1]
    assert rows[0]["action"] == "tg_send" and rows[0]["actor"] == "assistant" and "Ali" in rows[0]["detail"]


async def test_memory_add_and_delete_from_the_panel(web):
    c, app = web
    assert (await post(c, "/api/memory", {"text": "Uy: Chilonzor 9"}))[0] == 200
    mem = (await get(c, "/api/memory"))[1]
    assert mem[0]["text"] == "Uy: Chilonzor 9" and mem[0]["source"] == "owner"
    assert (await post(c, "/api/memory", {"text": ""}))[0] == 400
    assert (await c.delete(f"/api/memory/{mem[0]['id']}", headers=TOK)).status == 200
    assert (await get(c, "/api/memory"))[1] == []
    assert (await c.delete("/api/memory/999", headers=TOK)).status == 404


async def test_clear_chat_from_the_panel(web):
    c, app = web
    await post(c, "/api/chat", {"text": "salom"})
    await wait_rows(c, 2)
    assert (await post(c, "/api/chat/clear"))[0] == 200
    assert (await get(c, "/api/chat"))[1] == []


async def test_upload_then_task_with_attachment(web):
    c, app = web
    r = await c.post("/api/upload?name=../../brif.txt", headers={**TOK, "Content-Type": "application/octet-stream"}, data=b"salom")
    d = await r.json()
    assert r.status == 200 and d["name"] == "brif.txt" and d["size"] == 5               # yo'l qismi olib tashlandi
    assert (await post(c, "/api/tasks", {"text": "briefni o'qi", "files": [d["id"]]}))[0] == 200
    for _ in range(200):
        await asyncio.sleep(0.05)
        t = await app.store.get_task(1)
        if t and t["status"] == "done":
            break
    assert (app.settings.workspace_dir / "task_1" / "brif.txt").read_bytes() == b"salom"
    assert "brif.txt" in (await get(c, "/api/tasks/1"))[1]["files"]


async def test_upload_validation(web):
    c, _ = web
    assert (await c.post("/api/upload?name=a.txt", headers=TOK, data=b"")).status == 400
    assert (await c.post("/api/upload?name=a.txt", headers=TOK, data=b"x" * 10_000_001)).status == 400
    assert (await c.post("/api/upload?name=a.txt", data=b"x")).status == 401
    for bad in ("../../etc", "zzzzzzzz", "12345678"):                                   # noto'g'ri yoki mavjud emas
        assert (await post(c, "/api/tasks", {"text": "x", "files": [bad]}))[0] == 400


def test_panel_exposes_the_new_features():
    js = (Path(__file__).parent.parent / "aicompany/webui/app.js").read_text()
    html = (Path(__file__).parent.parent / "aicompany/webui/index.html").read_text()
    for needle in ("/integrations", "/location", "/place", "/team/review", "/report", "/audit", "/memory", "/chat/clear", "/upload",
                   "navigator.geolocation", "Ulanishlar", "HR tahlili", "Jurnal", "Xotiraga qo'shish", "Fayl biriktirish"):
        assert needle in js, needle
    assert "chat-clear" in html


async def test_models_check_endpoint_mirrors_the_cli_check(web2):
    c, app, provs = web2
    provs["gemini"].models_list = ["gemini-3-flash-preview", "gemini-3.1-flash-lite", "gemini-3.5-flash", "gemini-embedding-001"]
    provs["anthropic"].models_list = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]
    data = {p["provider"]: p for p in (await get(c, "/api/models"))[1]}
    assert data["openai"] == {"provider": "openai", "enabled": False, "tiers": []}
    assert all(t["found"] for t in data["anthropic"]["tiers"]) and data["anthropic"]["count"] == 3
    mid = next(t for t in data["gemini"]["tiers"] if t["tier"] == "mid")
    assert mid["id"] == "gemini-3-flash" and mid["found"] is False and mid["suggestion"] == "gemini-3-flash-preview"
    await app.store.set_kv("model:gemini:gemini-3-flash", "gemini-3-flash-preview")                      # router avto-tuzatgan
    mid = next(t for t in (await get(c, "/api/models"))[1][2]["tiers"] if t["tier"] == "mid")
    assert mid["fixed"] is True and mid["using"] == "gemini-3-flash-preview"
    assert (await c.get("/api/models")).status == 401


async def test_models_check_reports_a_broken_key(web2):
    from aicompany.providers import ProviderError
    c, app, provs = web2

    async def boom():
        raise ProviderError("gemini: 400 API key not valid")
    provs["gemini"].list_models = boom
    d = {p["provider"]: p for p in (await get(c, "/api/models"))[1]}
    assert "API key not valid" in d["gemini"]["error"] and d["gemini"]["tiers"] == []


async def test_widget_link_and_limits_shown_in_the_panel(web):
    c, app = web
    w = (await get(c, "/api/widget-link"))[1]
    assert w["configured"] is True and w["token"] == "widget-secret" and w["base_url"].startswith("http") and "ai-jamoa.js" in w["script"]
    lim = (await get(c, "/api/integrations"))[1]["limits"]
    assert lim["task_usd"] == 1.0 and lim["agents"] == 12 and lim["tool_turns"] == 8 and "Asia/Tashkent" in lim["report"]
    assert (await c.get("/api/widget-link")).status == 401
    js = (Path(__file__).parent.parent / "aicompany/webui/app.js").read_text()
    for needle in ("/models", "/widget-link", "Modellarni tekshirish", "iPhone vidjeti", "Limitlar"):
        assert needle in js, needle



async def test_continue_previous_task_via_panel(web):
    c, app = web
    first = await app.orch.run_task("landing page", 1)
    ws1 = app.settings.workspace_dir / f"task_{first['task_id']}"
    (ws1 / "index.html").write_text("<h1>v1</h1>")
    assert (await post(c, "/api/tasks", {"text": "sarlavhani o'zgartir", "based_on": 999}))[0] == 400
    assert (await post(c, "/api/tasks", {"text": "sarlavhani o'zgartir", "based_on": "1"}))[0] == 400
    assert (await post(c, "/api/tasks", {"text": "sarlavhani o'zgartir", "based_on": first["task_id"]}))[0] == 200
    for _ in range(200):
        await asyncio.sleep(0.05)
        t2 = await app.store.get_task(2)
        if t2 and t2["status"] == "done":
            break
    assert t2["based_on"] == first["task_id"]
    assert (app.settings.workspace_dir / "task_2" / "index.html").read_text() == "<h1>v1</h1>"     # fayllar ko'chirildi
    assert t2["request"] == "sarlavhani o'zgartir"
    lst = (await get(c, "/api/tasks"))[1]
    assert next(t for t in lst if t["id"] == 2)["based_on"] == 1
    chat = (await get(c, "/api/chat"))[1]
    assert any(r["text"].startswith("📌 (#1 ustida)") for r in chat)


async def test_reminders_in_the_panel(web):
    c, app = web
    assert (await get(c, "/api/reminders"))[1] == []
    code, r = await post(c, "/api/reminders", {"text": "Aliga qo'ng'iroq", "when": "30 daq"})
    assert code == 200 and r["local"] and r["id"] == 1
    assert (await post(c, "/api/reminders", {"text": "x", "when": "kecha"}))[0] == 400
    assert (await post(c, "/api/reminders", {"text": "", "when": "30 daq"}))[0] == 400
    assert [x["text"] for x in (await get(c, "/api/reminders"))[1]] == ["Aliga qo'ng'iroq"]
    assert (await c.delete("/api/reminders/1", headers=TOK)).status == 200
    assert (await c.delete("/api/reminders/1", headers=TOK)).status == 404
    assert "Eslatmalar" in (Path(__file__).parent.parent / "aicompany/webui/app.js").read_text()


# ---------- Telegram akkauntni panel orqali ulash ----------
class LoginClient:
    class PasswordNeeded(Exception):
        pass

    def __init__(self, need_password=False, bad_code=False):
        self.need_password, self.bad_code = need_password, bad_code
        self.log, self.connected, self.authed = [], False, False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    def is_connected(self):
        return self.connected

    async def is_user_authorized(self):
        return self.authed

    async def send_code_request(self, phone):
        self.log.append(("code", phone))
        n = sum(1 for x in self.log if x[0] == "code")
        kind = "SentCodeTypeApp" if n == 1 else "SentCodeTypeSms"
        return type("Sent", (), {"phone_code_hash": f"H{n}", "type": type(kind, (), {})(),
                                 "next_type": type("CodeTypeSms", (), {})(), "timeout": 60})()

    async def sign_in(self, phone=None, code=None, phone_code_hash=None, password=None):
        self.log.append(("sign_in", phone, code, phone_code_hash, password))
        if self.bad_code and code:
            raise type("PhoneCodeInvalidError", (Exception,), {})()
        if self.need_password and password is None:
            raise type("SessionPasswordNeededError", (Exception,), {})()
        self.authed = True

    async def get_me(self):
        return type("Me", (), {"first_name": "Agent", "last_name": None, "username": "agent_x", "id": 9})()

    async def log_out(self):
        self.log.append(("logout",))
        self.authed = False


async def test_tg_login_flow_via_panel(web):
    from aicompany.tguser import TgUser
    c, app = web
    client = LoginClient()
    app.tg = TgUser(app.settings, client_factory=lambda: client)
    app.tg.api_id = app.tg.api_hash = None                                    # kalitlar hali yo'q
    assert (await get(c, "/api/integrations"))[1]["telegram_account"]["keys"] is False
    assert (await post(c, "/api/tg/keys", {"api_id": "abc", "api_hash": "x"}))[0] == 400
    assert (await post(c, "/api/tg/keys", {"api_id": "12345", "api_hash": "a" * 32}))[0] == 200
    assert await app.store.get_kv("tg_api_id") == "12345"
    assert (await post(c, "/api/tg/code", {"phone": "salom"}))[0] == 400
    assert (await post(c, "/api/tg/verify", {"code": "1"}))[0] == 400          # avval kod so'ralmagan
    assert (await post(c, "/api/tg/code", {"phone": "+998 90 123-45-67"}))[0] == 200
    assert ("code", "+998901234567") in client.log
    assert (await get(c, "/api/integrations"))[1]["telegram_account"]["pending"] is True
    st, d = await post(c, "/api/tg/verify", {"code": "1 2 3-45"})
    assert (st, d["status"]) == (200, "ok") and d["me"] == "Agent (@agent_x)"
    assert ("sign_in", "+998901234567", "12345", "H1", None) in client.log
    ta = (await get(c, "/api/integrations"))[1]["telegram_account"]
    assert ta["configured"] and ta["me"] == "Agent (@agent_x)" and not ta["pending"]
    assert await app.store.get_kv("tg_me") == "Agent (@agent_x)"
    assert "12345" not in json.dumps([dict(r) for r in await app.store.recent_audit(20)])   # kod jurnalga tushmaydi
    assert (await post(c, "/api/tg/logout"))[0] == 200
    assert ("logout",) in client.log and await app.store.get_kv("tg_me") is None


async def test_tg_login_two_factor_and_bad_code(web):
    from aicompany.tguser import TgUser
    c, app = web
    client = LoginClient(need_password=True)
    app.tg = TgUser(app.settings, client_factory=lambda: client)
    app.tg.api_id, app.tg.api_hash = 1, "h"
    await post(c, "/api/tg/code", {"phone": "+998901234567"})
    st, d = await post(c, "/api/tg/verify", {"code": "11111"})
    assert (st, d["status"]) == (200, "password")
    st, d = await post(c, "/api/tg/verify", {"code": "11111", "password": "sir"})
    assert (st, d["status"]) == (200, "ok")
    bad = LoginClient(bad_code=True)
    app.tg = TgUser(app.settings, client_factory=lambda: bad)
    app.tg.api_id, app.tg.api_hash = 1, "h"
    await post(c, "/api/tg/code", {"phone": "+998901234567"})
    st, d = await post(c, "/api/tg/verify", {"code": "000"})
    assert st == 400 and "Kod noto'g'ri" in d["error"]


async def test_tg_keys_from_panel_survive_restart(make_app):
    app, _ = await make_app(front())
    await app.store.set_kv("tg_api_id", "777")
    await app.store.set_kv("tg_api_hash", "b" * 32)
    from aicompany.app import build_app
    again = await build_app(app.settings, providers={})
    assert (again.tg.api_id, again.tg.api_hash) == (777, "b" * 32) and not again.tg.configured()


async def test_tg_me_shows_connected_account(web):
    from aicompany.tguser import TgUser
    c, app = web
    assert (await get(c, "/api/tg/me"))[1] == {"me": "", "pending": False}     # ulanmagan
    client = LoginClient()
    client.authed = True
    app.tg = TgUser(app.settings, client_factory=lambda: client)
    app.tg.api_id, app.tg.api_hash = 1, "h"
    assert (await get(c, "/api/tg/me"))[1] == {"me": "Agent (@agent_x)"}


async def test_tg_stale_session_is_cleaned_and_login_offered(web, tmp_path):
    from aicompany.tguser import TgUser, session_file
    c, app = web
    stale = LoginClient()                                                      # fayl bor, lekin kirilmagan
    app.tg = TgUser(dataclasses.replace(app.settings, tg_session=str(tmp_path / "tg")))
    app.tg.api_id, app.tg.api_hash = 1, "h"
    app.tg._new_client = lambda fresh=False: stale
    session_file(app.tg.s).write_text("x")
    assert app.tg.configured()
    assert (await get(c, "/api/tg/me"))[1] == {"me": "", "stale": True}
    assert not session_file(app.tg.s).exists() and not app.tg.configured()     # endi kirish qadamlari chiqadi


def test_parse_proxy_variants():
    from aicompany.tguser import parse_proxy
    assert parse_proxy("") is None
    assert parse_proxy("tg://proxy?server=1.2.3.4&port=443&secret=dd00ff") == ("mtproxy", ("1.2.3.4", 443, "dd00ff"))
    assert parse_proxy("https://t.me/proxy?server=p.example&port=8443&secret=abcd")[1] == ("p.example", 8443, "abcd")
    kind, px = parse_proxy("socks5://u:p@127.0.0.1:1080")
    assert kind == "socks" and (px["addr"], px["port"], px["username"], px["password"]) == ("127.0.0.1", 1080, "u", "p")
    for bad in ("salom", "tg://proxy?server=x", "tg://proxy?server=x&port=1&secret=ee11", "socks5://host"):
        with pytest.raises(ValueError):
            parse_proxy(bad)


async def test_tg_code_does_not_hang_when_telegram_unreachable(web, monkeypatch):
    import aicompany.tguser as tgu
    c, app = web
    monkeypatch.setattr(tgu, "CONNECT_TIMEOUT", 0.2)

    class Hanging(LoginClient):
        async def connect(self):
            await asyncio.sleep(30)                                            # tarmoq bloklangan: javob yo'q
    app.tg = tgu.TgUser(app.settings, client_factory=lambda: Hanging())
    app.tg.api_id, app.tg.api_hash = 1, "h"
    st, d = await post(c, "/api/tg/code", {"phone": "+998901234567"})
    assert st == 400 and "ulanib bo'lmadi" in d["error"] and "proksi" in d["error"].lower()
    assert not app.tg.login_pending()


async def test_tg_proxy_saved_from_panel_and_validated(web):
    from aicompany.tguser import TgUser
    c, app = web
    app.tg = TgUser(app.settings, client_factory=lambda: LoginClient())
    app.tg.api_id, app.tg.api_hash = 1, "h"
    st, d = await post(c, "/api/tg/code", {"phone": "+998901234567", "proxy": "nimadir"})
    assert st == 400 and "Proksi" in d["error"]
    link = "tg://proxy?server=1.2.3.4&port=443&secret=dd00"
    assert (await post(c, "/api/tg/code", {"phone": "+998901234567", "proxy": link}))[0] == 200
    assert await app.store.get_kv("tg_proxy") == link and app.tg.proxy == link
    assert (await get(c, "/api/integrations"))[1]["telegram_account"]["proxy"] is True
    assert (await post(c, "/api/tg/code", {"phone": "+998901234567", "proxy": ""}))[0] == 200
    assert await app.store.get_kv("tg_proxy") is None and not app.tg.proxy


async def test_pending_login_is_not_mistaken_for_connected(web, tmp_path):
    """Telethon kod so'ralganda sessiya faylini yaratadi: panel buni 'ulangan' deb olib, kirishni buzmasligi kerak."""
    from aicompany.tguser import TgUser, session_file
    c, app = web
    tg = TgUser(dataclasses.replace(app.settings, tg_session=str(tmp_path / "tg")))
    tg.api_id, tg.api_hash = 1, "h"
    client = LoginClient()

    async def connect():
        client.connected = True
        session_file(tg.s).write_text("x")                                     # haqiqiy Telethon kabi
    client.connect = connect
    tg._new_client = lambda fresh=False: client
    app.tg = tg
    assert (await post(c, "/api/tg/code", {"phone": "+998901234567"}))[0] == 200
    ta = (await get(c, "/api/integrations"))[1]["telegram_account"]
    assert ta["pending"] is True and ta["configured"] is False
    assert (await get(c, "/api/tg/me"))[1] == {"me": "", "pending": True}      # kirish bekor qilinmadi
    assert tg.login_pending() and session_file(tg.s).exists()
    st, d = await post(c, "/api/tg/verify", {"code": "12345"})
    assert (st, d["status"]) == (200, "ok")
    assert (await get(c, "/api/integrations"))[1]["telegram_account"]["configured"] is True


async def test_tg_shows_where_code_went_and_resends(web):
    from aicompany.tguser import TgUser
    c, app = web
    client = LoginClient()
    app.tg = TgUser(app.settings, client_factory=lambda: client)
    app.tg.api_id, app.tg.api_hash = 1, "h"
    st, d = await post(c, "/api/tg/code", {"phone": "+998901234567"})
    assert st == 200 and "Telegram ilovasiga" in d["via"] and d["next"] == "SMS bilan"
    assert "Telegram ilovasiga" in (await get(c, "/api/integrations"))[1]["telegram_account"]["login"]["via"]
    st, d = await post(c, "/api/tg/resend")
    assert st == 200 and d["via"] == "SMS bilan"
    await post(c, "/api/tg/verify", {"code": "55555"})
    assert ("sign_in", "+998901234567", "55555", "H2", None) in client.log    # qayta yuborilgan kod xeshi
    assert (await post(c, "/api/tg/resend"))[0] == 400                        # kirish tugagan


class FakeQR:
    def __init__(self, outcome):
        self.outcome, self.n = outcome, 0

    @property
    def url(self):
        return f"tg://login?token=T{self.n}"

    async def recreate(self):
        self.n += 1

    async def wait(self, timeout=None):
        if self.outcome == "timeout":
            await asyncio.sleep(timeout)
            raise asyncio.TimeoutError
        await asyncio.sleep(0.05)
        if self.outcome == "password":
            raise type("SessionPasswordNeededError", (Exception,), {})()


async def test_tg_qr_login_success_and_2fa(web):
    from aicompany.tguser import TgUser
    c, app = web
    for outcome in ("ok", "password"):
        client = LoginClient()
        qr = FakeQR(outcome)

        async def qr_login(qr=qr):
            return qr
        client.qr_login = qr_login
        app.tg = TgUser(app.settings, client_factory=lambda client=client: client)
        app.tg.api_id, app.tg.api_hash = 1, "h"
        st, d = await post(c, "/api/tg/qr")
        assert st == 200 and d["status"] == "waiting" and d["svg"].startswith("data:image/svg+xml;base64,")
        assert (await get(c, "/api/integrations"))[1]["telegram_account"]["login"]["qr"] is True
        await asyncio.sleep(0.2)
        d = (await get(c, "/api/tg/qr"))[1]
        if outcome == "ok":
            assert d["status"] == "ok" and d["me"] == "Agent (@agent_x)" and app.tg.configured()
            assert await app.store.get_kv("tg_me") == "Agent (@agent_x)"
        else:
            assert d["status"] == "password" and "svg" not in d
            st, d = await post(c, "/api/tg/verify", {"password": "sir"})
            assert (st, d["status"]) == (200, "ok") and ("sign_in", None, None, None, "sir") in client.log


async def test_tg_qr_refreshes_token_and_restart_cancels_old(make_app):
    from aicompany.tguser import TgUser
    app, _ = await make_app(front())
    client = LoginClient()
    qr = FakeQR("timeout")

    async def qr_login():
        return qr
    client.qr_login = qr_login
    tg = TgUser(app.settings, client_factory=lambda: client)
    tg.api_id, tg.api_hash = 1, "h"
    await tg.qr_start()
    task = tg._login["task"]
    task.cancel()                                                              # haqiqiy kutishni qisqa variant bilan almashtiramiz
    login = tg._login
    await tg._qr_wait(login, total=0.35, step=0.1)
    assert qr.n >= 2 and login["status"] == "expired"                        # token yangilanib turdi, keyin muddat tugadi
    await tg._drop_login()
    assert not tg.login_pending() and not client.connected


async def test_tg_access_mode_from_panel(web):
    c, app = web
    assert (await get(c, "/api/integrations"))[1]["telegram_account"]["mode"] == "read"
    assert (await post(c, "/api/tg/access", {"mode": "kuch"}))[0] == 400
    assert (await post(c, "/api/tg/access", {"mode": "full"}))[0] == 200
    assert (await get(c, "/api/integrations"))[1]["telegram_account"]["mode"] == "full"
    assert (await post(c, "/api/tg/access", {"mode": "ask"}))[0] == 200
    assert (await get(c, "/api/integrations"))[1]["telegram_account"]["mode"] == "ask"


async def test_health_is_public_and_minimal(web):
    c, app = web
    r = await c.get("/health")                                                 # tokensiz
    d = await r.json()
    assert r.status == 200 and d["ok"] is True and "uptime_s" in d and d["telegram_account"] is False
    assert "token" not in json.dumps(d).lower()
    assert (await c.head("/health")).status == 200                             # UptimeRobot HEAD so'rovi
    assert (await c.get("/healthz")).status == 200


def test_render_port_host_and_public_url():
    from .conftest import settings
    s = settings(PORT="10000", RENDER_EXTERNAL_URL="https://ai-jamoa.onrender.com")
    assert (s.web_host, s.web_port, s.web_public_url) == ("0.0.0.0", 10000, "https://ai-jamoa.onrender.com")
    s = settings()
    assert (s.web_host, s.web_port, s.web_public_url) == ("127.0.0.1", 8080, None)   # lokal o'zgarmaydi
    s = settings(PORT="10000", WEB_HOST="127.0.0.1", WEB_PORT="9000")
    assert (s.web_host, s.web_port) == ("127.0.0.1", 9000)                     # aniq sozlama ustun


def test_missing_token_on_render_fails_clearly(tmp_path, monkeypatch):
    from aicompany.config import ensure_secret
    monkeypatch.delenv("WEB_TOKEN_X", raising=False)
    monkeypatch.setenv("RENDER", "true")
    with pytest.raises(SystemExit, match="Render"):
        ensure_secret("WEB_TOKEN_X", tmp_path / ".env")
    assert not (tmp_path / ".env").exists()


def test_render_ignores_local_host_and_port_copied_from_env():
    from .conftest import settings
    s = settings(RENDER="true", PORT="10000", WEB_HOST="127.0.0.1", WEB_PORT="8080")
    assert (s.web_host, s.web_port) == ("0.0.0.0", 10000)


async def test_overview_has_everything_for_desktop_dashboard(web):
    c, app = web
    await post(c, "/api/tasks", {"text": "sayt yasab ber"})
    for _ in range(300):
        await asyncio.sleep(0.05)
        rows = (await get(c, "/api/tasks"))[1]
        if rows and rows[0]["status"] != "running":
            break
    st, d = await get(c, "/api/overview")
    assert st == 200 and {"state", "team", "daily", "tasks", "counts", "approvals", "reminders"} <= set(d)
    assert len(d["daily"]) == 14 and d["daily"][-1]["done"] >= 1 and d["daily"][-1]["cost"] > 0
    assert d["tasks"][0]["cost"] > 0 and d["counts"]["done"] >= 1
    assert any(a["name"] == "ceo" for a in d["team"])


async def test_static_files_are_compressed_and_cached_with_etag(web):
    c, _ = web
    r = await c.get("/app.js", headers={"Accept-Encoding": "gzip"}, skip_auto_headers=("Accept-Encoding",))
    raw = await r.read()
    assert r.status == 200 and r.headers.get("Content-Encoding") == "gzip" and r.headers.get("ETag")
    etag = r.headers["ETag"]
    r2 = await c.get("/app.js", headers={"If-None-Match": etag})
    assert r2.status == 304                                              # o'zgarmagan: qayta yuklanmaydi
    big = await c.get("/api/overview", headers={**TOK, "Accept-Encoding": "gzip"}, skip_auto_headers=("Accept-Encoding",))
    assert big.status == 200 and len(await big.read()) > 0


async def test_api_bot_push(web):
    c, app = web
    r = await c.post("/api/bot_push", json={"mode": "result"}, headers=TOK)
    assert r.status == 200 and await app.store.get_kv("bot_push") == "result"
    assert (await c.post("/api/bot_push", json={"mode": "zzz"}, headers=TOK)).status == 400


async def test_push_subscribe_prefs_and_delivery(web, monkeypatch):
    c, app = web
    key = (await get(c, "/api/push/key"))[1]["key"]
    assert len(key) > 60 and (await get(c, "/api/push/key"))[1]["key"] == key   # kalit barqaror
    assert (await post(c, "/api/push/subscribe", {"subscription": {"endpoint": "http://x", "keys": {}}}))[0] == 400
    sub = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "P", "auth": "A"}}
    assert (await post(c, "/api/push/subscribe", {"subscription": sub}))[0] == 200
    assert (await get(c, "/api/state"))[1]["push"]["devices"] == 1

    sent = []

    def fake_send(self, s, payload, vapid):
        sent.append(json.loads(payload))
    monkeypatch.setattr(type(app.push), "_send", fake_send)
    await app.push.task_done({"kind": "task", "task_id": 5, "status": "done", "result": "# Natija\nTayyor"})
    assert sent[-1]["title"].startswith("✅") and sent[-1]["url"] == "/?task=5"
    await post(c, "/api/push/prefs", {"done": False})
    n = len(sent)
    await app.push.task_done({"kind": "task", "task_id": 6, "status": "done", "result": "x"})
    assert len(sent) == n                                                     # o'chirilgan tur yuborilmaydi
    await app.push.task_done({"kind": "task", "task_id": 7, "status": "failed", "error": "limit"})
    assert sent[-1]["tag"] == "failed"
    await app.push.approval(1, 5, "dev", "rm -rf x")
    assert sent[-1]["tag"] == "approval"

    class Gone(Exception):
        response = type("R", (), {"status_code": 410})()

    def dead(self, s, payload, vapid):
        raise Gone()
    monkeypatch.setattr(type(app.push), "_send", dead)
    await app.push.notify("approval", "t")
    assert await app.push.subs() == []                                        # o'lik obuna tozalandi


async def test_push_assets_and_ui(web):
    js = (Path(__file__).parent.parent / "aicompany/webui/app.js").read_text()
    sw = (Path(__file__).parent.parent / "aicompany/webui/sw.js").read_text()
    assert "pushManager.subscribe" in js and "/push/prefs" in js and 'addEventListener("push"' in sw and "notificationclick" in sw
