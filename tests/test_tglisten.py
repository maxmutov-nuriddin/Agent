import asyncio
import json
from types import SimpleNamespace as NS

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aicompany.tglisten import TgListener, parse_ids
from aicompany.tguser import TgUser


class Client:
    """Agent akkauntining soxta Telethon mijozi."""

    def __init__(self):
        self.sent, self.files, self.handlers, self.connected = [], [], [], True

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    def is_connected(self):
        return self.connected

    async def is_user_authorized(self):
        return True

    async def get_me(self):
        return NS(first_name="Agent", last_name=None, username="agent_x", id=9, title=None)

    def add_event_handler(self, fn, ev):
        self.handlers.append((fn, ev))

    def remove_event_handler(self, fn):
        self.handlers = [h for h in self.handlers if h[0] is not fn]

    async def send_message(self, chat, text):
        self.sent.append((chat, text))

    async def send_file(self, chat, f, caption=None):
        self.files.append((chat, getattr(f, "name", f), caption))


def event(sender, text="", private=True, voice=None, document=None, data=b"", mid=1):
    msg = NS(message=text, voice=voice, audio=None, document=document, photo=None, id=mid,
             file=NS(size=len(data), mime_type="audio/ogg", name="hujjat.pdf", ext=".pdf"))

    async def download_media(file=None):
        return data
    return NS(is_private=private, sender_id=sender, chat_id=sender, message=msg, download_media=download_media)


@pytest.fixture
async def lapp(make_app):
    def handler(system, user, model):
        if "front desk" in system or "in a CHAT" in system:
            return json.dumps({"mode": "chat", "reply": "Salom, men agentman"})
        return "ok"
    app, provs = await make_app(handler, names=("anthropic", "gemini"), audio="ertaga uchrashuv", OWNER_TELEGRAM_ID="777")
    client = Client()
    app.tg = TgUser(app.settings, client_factory=lambda: client)
    app.tg.api_id, app.tg.api_hash = 1, "h"
    return app, client


def test_parse_ids():
    assert parse_ids("123, 456 -100") == [123, 456, -100] and parse_ids("") == []
    with pytest.raises(ValueError):
        parse_ids("123, ali")


async def test_only_owner_private_messages_get_answers(lapp):
    app, client = lapp
    lst = TgListener(app)
    await lst.handle_event(event(555, "salom"))                    # begona odam
    await lst.handle_event(event(777, "salom", private=False))     # egasi, lekin guruhda
    assert client.sent == []
    await lst.handle_event(event(777, "salom"))                    # egasi, shaxsiy chat
    assert client.sent == [(777, "Salom, men agentman")]


async def test_owner_list_from_panel_overrides_env(lapp):
    app, client = lapp
    lst = TgListener(app)
    assert await lst.owner_ids() == [777]
    await app.store.set_kv("tg_owner_ids", "111,222")
    assert await lst.owner_ids() == [111, 222]
    await lst.handle_event(event(777, "salom"))                    # endi 777 egasi emas
    await lst.handle_event(event(222, "salom"))
    assert client.sent == [(222, "Salom, men agentman")]


async def test_task_result_and_files_are_sent_back(lapp, tmp_path):
    app, client = lapp
    ws = app.settings.workspace_dir / "task_5"
    ws.mkdir(parents=True)
    (ws / "sayt.html").write_text("<h1>x</h1>")

    async def handle(text, chat_id, notify, attachments=None):
        await notify("Boshladim")
        return {"kind": "task", "task_id": 5, "status": "done", "result": "x" * 5000, "files": ["sayt.html"]}
    app.orch.handle = handle
    await TgListener(app).handle_event(event(777, "sayt qil"))
    texts = [t for _, t in client.sent]
    assert texts[0] == "Boshladim" and texts[1] == "🏁 Vazifa #5 — tayyor" and "to'liq natija faylda" in texts[2]
    assert [f[1] for f in client.files] == ["task_5.md", str(ws / "sayt.html")]


async def test_voice_and_document_from_owner(lapp):
    app, client = lapp
    seen = []

    async def handle(text, chat_id, notify, attachments=None):
        seen.append((text, [p.name for p in attachments or []]))
        return {"kind": "chat", "reply": "ok"}
    app.orch.handle = handle
    lst = TgListener(app)
    await lst.handle_event(event(777, voice=object(), data=b"ogg" * 100))
    assert client.sent[-1] == (777, "🎤 Eshitdim: ertaga uchrashuv") and seen[-1] == ("ertaga uchrashuv", [])
    await lst.handle_event(event(777, "shuni tahlil qil", document=object(), data=b"%PDF", mid=42))
    assert seen[-1] == ("shuni tahlil qil", ["tg42_hujjat.pdf"])
    assert (app.settings.workspace_dir / "inbox" / "tg42_hujjat.pdf").read_bytes() == b"%PDF"


async def test_errors_are_reported_not_swallowed(lapp):
    app, client = lapp

    async def boom(*a, **k):
        raise RuntimeError("model ishlamadi")
    app.orch.handle = boom
    await TgListener(app).handle_event(event(777, "salom"))
    assert client.sent == [(777, "❌ Xatolik: model ishlamadi")]


async def test_attach_detach_and_reattach_on_new_login(lapp):
    app, client = lapp
    lst = TgListener(app)
    assert await lst.attach() and lst.active() and len(client.handlers) == 1
    assert client.handlers[0][1].incoming is True                  # faqat kiruvchi xabarlar
    assert await lst.attach() and len(client.handlers) == 1         # takror ulanmaydi
    await app.store.set_kv("tg_listen", "0")
    assert not await lst.attach() and client.handlers == [] and not lst.active()
    await app.store.set_kv("tg_listen", "1")
    await lst.attach()
    new = Client()                                                  # panelda qayta kirildi: yangi mijoz
    await app.tg.close()
    app.tg._factory = lambda: new
    await lst.attach()
    assert len(new.handlers) == 1 and client.handlers == []


async def test_supervise_recovers_from_disconnect(lapp):
    app, client = lapp
    lst = TgListener(app)
    task = asyncio.create_task(lst.supervise(interval=0.05))
    await asyncio.sleep(0.1)
    assert lst.active()
    client.connected = False                                        # tarmoq uzildi
    await asyncio.sleep(0.2)
    assert lst.active() and client.connected                        # qayta ulandi
    task.cancel()


async def test_listen_settings_endpoint(make_app):
    import dataclasses

    from aicompany.web import make_web_app
    app, _ = await make_app(lambda *a: "ok", OWNER_TELEGRAM_ID="777")
    app.settings = dataclasses.replace(app.settings, web_token="t")
    c = TestClient(TestServer(make_web_app(app)))
    await c.start_server()
    try:
        hdr = {"Authorization": "Bearer t"}
        d = await (await c.get("/api/integrations", headers=hdr)).json()
        assert d["telegram_account"]["listen"] == {"enabled": True, "owners": [777], "active": False, "error": ""}
        r = await c.post("/api/tg/listen", headers=hdr, data=json.dumps({"owners": "12, x"}))
        assert r.status == 400
        r = await c.post("/api/tg/listen", headers=hdr, data=json.dumps({"owners": "12, 34", "enabled": False}))
        assert (await r.json())["owners"] == [12, 34] and not (await r.json())["enabled"]
        r = await c.post("/api/tg/listen", headers=hdr, data=json.dumps({"owners": ""}))
        assert (await r.json())["owners"] == [777]                  # bo'sh: .env dagi egasi
    finally:
        await c.close()
