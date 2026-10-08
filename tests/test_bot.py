from datetime import datetime

from aiogram import Bot
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import Chat, Message, Update, User

from aicompany.bot import make_dispatcher

from .conftest import scripted_company

OWNER = 111


def update(uid, text):
    msg = Message(message_id=1, date=datetime.now(), chat=Chat(id=uid, type="private"),
                  from_user=User(id=uid, is_bot=False, first_name="x"), text=text)
    return Update(update_id=1, message=msg)


async def test_bot_only_obeys_owner(make_app, monkeypatch):
    sent = []

    async def fake_call(self, method, request_timeout=None):
        sent.append(method)
        return True
    monkeypatch.setattr(Bot, "__call__", fake_call)

    app, _ = await make_app(scripted_company(), OWNER_TELEGRAM_ID=str(OWNER))
    bot = Bot("123456:ABC")
    dp = make_dispatcher(app, bot)

    assert await dp.feed_update(bot, update(999, "/pause")) is UNHANDLED
    assert await app.store.get_kv("paused") is None and not sent

    await dp.feed_update(bot, update(OWNER, "/pause"))
    assert await app.store.get_kv("paused") == "1" and sent
    await bot.session.close()


async def test_bot_replies_to_greeting_without_creating_task(make_app, monkeypatch):
    import asyncio
    import json
    sent = []

    async def fake_call(self, method, request_timeout=None):
        sent.append(getattr(method, "text", None))
        return True
    monkeypatch.setattr(Bot, "__call__", fake_call)
    base = scripted_company()

    def handler(system, user, model):
        if "front desk" in system:
            return json.dumps({"mode": "chat", "reply": "Salom! Men tayyorman."})
        return base(system, user, model)
    app, _ = await make_app(handler, OWNER_TELEGRAM_ID=str(OWNER))
    bot = Bot("123456:ABC")
    dp = make_dispatcher(app, bot)
    await dp.feed_update(bot, update(OWNER, "salom"))
    await asyncio.sleep(0.2)
    assert sent == ["Salom! Men tayyorman."] and await app.store.list_tasks() == []
    await bot.session.close()


async def test_ai_command_shows_and_switches_provider(make_app, monkeypatch):
    sent = []

    async def fake_call(self, method, request_timeout=None):
        sent.append(getattr(method, "text", None))
        return True
    monkeypatch.setattr(Bot, "__call__", fake_call)
    app, _ = await make_app(scripted_company(), names=("anthropic", "gemini"), OWNER_TELEGRAM_ID=str(OWNER))
    bot = Bot("123456:ABC")
    dp = make_dispatcher(app, bot)
    await dp.feed_update(bot, update(OWNER, "/ai"))
    assert "Avto" in sent[0] and "Claude, Gemini" in sent[0]
    await dp.feed_update(bot, update(OWNER, "/ai gemini"))
    assert "Asosiy AI: Gemini" in sent[1] and await app.router.primary() == "gemini"
    await dp.feed_update(bot, update(OWNER, "/ai claude"))
    assert await app.router.primary() == "anthropic"
    await dp.feed_update(bot, update(OWNER, "/ai chatgpt"))
    assert "ulanmagan" in sent[3] and await app.router.primary() == "anthropic"
    assert await dp.feed_update(bot, update(999, "/ai gemini")) is UNHANDLED
    await bot.session.close()


def voice_update(uid, size=2000):
    from aiogram.types import Voice
    msg = Message(message_id=5, date=datetime.now(), chat=Chat(id=uid, type="private"),
                  from_user=User(id=uid, is_bot=False, first_name="x"),
                  voice=Voice(file_id="f", file_unique_id="u", duration=3, mime_type="audio/ogg", file_size=size))
    return Update(update_id=9, message=msg)


async def test_voice_message_is_transcribed_then_handled_like_text(make_app, monkeypatch):
    import asyncio
    import json
    sent = []

    async def fake_call(self, method, request_timeout=None):
        sent.append(getattr(method, "text", None))
        return True

    async def fake_download(self, file, destination=None, **kw):
        destination.write(b"OggS" + b"\x00" * 800)
    monkeypatch.setattr(Bot, "__call__", fake_call)
    monkeypatch.setattr(Bot, "download", fake_download)
    base = scripted_company()

    def handler(system, user, model):
        if "front desk" in system:
            return json.dumps({"mode": "chat", "reply": "Eshitdim, davom eting."})
        return base(system, user, model)
    app, provs = await make_app(handler, names=("anthropic", "gemini"), audio="salom rahbar", OWNER_TELEGRAM_ID=str(OWNER))
    bot = Bot("123456:ABC")
    dp = make_dispatcher(app, bot)
    assert await dp.feed_update(bot, voice_update(999)) is UNHANDLED and provs["gemini"].audio_calls == []
    await dp.feed_update(bot, voice_update(OWNER))
    await asyncio.sleep(0.3)
    assert "🎤 Eshitdim: salom rahbar" in sent and "Eshitdim, davom eting." in sent
    assert provs["gemini"].audio_calls[0][1] == "audio/ogg"
    await bot.session.close()


async def test_voice_message_without_gemini_key_explains(make_app, monkeypatch):
    sent = []

    async def fake_call(self, method, request_timeout=None):
        sent.append(getattr(method, "text", None))
        return True

    async def fake_download(self, file, destination=None, **kw):
        destination.write(b"x" * 800)
    monkeypatch.setattr(Bot, "__call__", fake_call)
    monkeypatch.setattr(Bot, "download", fake_download)
    app, _ = await make_app(scripted_company(), OWNER_TELEGRAM_ID=str(OWNER))  # faqat Claude
    bot = Bot("123456:ABC")
    dp = make_dispatcher(app, bot)
    await dp.feed_update(bot, voice_update(OWNER))
    assert any("GEMINI_API_KEY" in (t or "") for t in sent) and await app.store.list_tasks() == []
    await dp.feed_update(bot, voice_update(OWNER, size=20_000_000))
    assert any("katta" in (t or "") for t in sent)
    await bot.session.close()


def location_update(uid, lat=41.31, lon=69.28, edited=False, live=None):
    from aiogram.types import Location
    msg = Message(message_id=7, date=datetime.now(), chat=Chat(id=uid, type="private"),
                  from_user=User(id=uid, is_bot=False, first_name="x"),
                  location=Location(latitude=lat, longitude=lon, live_period=live))
    return Update(update_id=11, **({"edited_message": msg} if edited else {"message": msg}))


async def test_bot_stores_location_home_and_live_updates(make_app, monkeypatch):
    import json
    sent = []

    async def fake_call(self, method, request_timeout=None):
        sent.append(getattr(method, "text", None))
        return True
    monkeypatch.setattr(Bot, "__call__", fake_call)
    app, _ = await make_app(scripted_company(), OWNER_TELEGRAM_ID=str(OWNER))
    bot = Bot("123456:ABC")
    dp = make_dispatcher(app, bot)
    assert await dp.feed_update(bot, location_update(999)) is UNHANDLED and await app.store.get_kv("loc:last") is None
    await dp.feed_update(bot, location_update(OWNER, 41.31, 69.28))
    assert "Joylashuv saqlandi" in sent[-1] and json.loads(await app.store.get_kv("loc:last"))["lat"] == 41.31
    assert await app.store.get_kv("place:home") is None                  # oddiy joylashuv uy emas
    await dp.feed_update(bot, update(OWNER, "/home"))                    # keyingi joylashuv = uy
    await dp.feed_update(bot, location_update(OWNER, 41.2, 69.1))
    assert "Uy manzili saqlandi" in sent[-1] and json.loads(await app.store.get_kv("place:home"))["lat"] == 41.2
    await dp.feed_update(bot, location_update(OWNER, 41.5, 69.5, edited=True, live=900))   # jonli yangilanish: jimgina
    loc = json.loads(await app.store.get_kv("loc:last"))
    assert loc["lat"] == 41.5 and loc["live"] is True and sent[-1].startswith("🏠")
    await bot.session.close()


async def test_tg_status_command_without_account(make_app, monkeypatch):
    sent = []

    async def fake_call(self, method, request_timeout=None):
        sent.append(getattr(method, "text", None))
        return True
    monkeypatch.setattr(Bot, "__call__", fake_call)
    app, _ = await make_app(scripted_company(), OWNER_TELEGRAM_ID=str(OWNER))
    bot = Bot("123456:ABC")
    dp = make_dispatcher(app, bot)
    await dp.feed_update(bot, update(OWNER, "/tg"))
    assert "tglogin" in sent[0]
    await bot.session.close()


async def test_bot_reports_errors_splits_long_messages_and_uses_uzbek_status(make_app, monkeypatch):
    import asyncio
    import json
    sent = []

    async def fake_call(self, method, request_timeout=None):
        sent.append(getattr(method, "text", None))
        return True
    monkeypatch.setattr(Bot, "__call__", fake_call)
    long_reply = "a" * 9000

    def handler(system, user, model):
        if "front desk" in system:
            return json.dumps({"mode": "chat", "reply": long_reply})
        return scripted_company()(system, user, model)
    app, _ = await make_app(handler, OWNER_TELEGRAM_ID=str(OWNER))
    bot = Bot("123456:ABC")
    dp = make_dispatcher(app, bot)
    await dp.feed_update(bot, update(OWNER, "salom"))
    await asyncio.sleep(0.2)
    texts = [t for t in sent if t]
    assert len(texts) == 3 and "".join(texts) == long_reply and all(len(t) <= 4096 for t in texts)  # bo'laklandi

    async def broken(*a, **k):
        raise RuntimeError("baza qulf")
    app.orch.handle = broken
    sent.clear()
    await dp.feed_update(bot, update(OWNER, "salom"))
    await asyncio.sleep(0.2)
    assert sent and sent[0].startswith("❌ Xatolik") and "baza qulf" in sent[0]       # foydalanuvchi javobsiz qolmaydi
    await bot.session.close()


async def test_bot_task_summary_uses_uzbek_status(make_app, monkeypatch):
    import asyncio
    import json
    sent = []

    async def fake_call(self, method, request_timeout=None):
        sent.append(getattr(method, "text", None))
        return True
    monkeypatch.setattr(Bot, "__call__", fake_call)
    base = scripted_company()

    def handler(system, user, model):
        if "front desk" in system:
            return json.dumps({"mode": "task", "reply": "", "task": "x"})
        return base(system, user, model)
    app, _ = await make_app(handler, OWNER_TELEGRAM_ID=str(OWNER))
    bot = Bot("123456:ABC")
    dp = make_dispatcher(app, bot)
    await dp.feed_update(bot, update(OWNER, "sayt yarat"))
    for _ in range(50):
        await asyncio.sleep(0.05)
        if any(t and t.startswith("🏁") for t in sent):
            break
    assert any(t and t.startswith("🏁 Vazifa #1 — ✅ tayyor") for t in sent)
    await bot.session.close()


async def test_reminders_command_lists_and_cancels(make_app, monkeypatch):
    sent = []

    async def fake_call(self, method, request_timeout=None):
        sent.append(getattr(method, "text", None))
        return True
    monkeypatch.setattr(Bot, "__call__", fake_call)
    from aicompany import reminders
    app, _ = await make_app(scripted_company(), OWNER_TELEGRAM_ID=str(OWNER))
    bot = Bot("123456:ABC")
    dp = make_dispatcher(app, bot)
    await dp.feed_update(bot, update(OWNER, "/reminders"))
    assert "Eslatma yo'q" in sent[-1]
    await reminders.create(app.store, app.settings, OWNER, "Dori ich", "1 soat")
    await dp.feed_update(bot, update(OWNER, "/reminders"))
    assert "#1" in sent[-1] and "Dori ich" in sent[-1]
    await dp.feed_update(bot, update(OWNER, "/reminders cancel 1"))
    assert "Bekor" in sent[-1] and await app.store.list_reminders() == []
    await bot.session.close()


async def test_bot_push_modes(make_app, monkeypatch):
    from aicompany.orchestrator import Progress
    sent = []

    async def fake_call(self, method, request_timeout=None):
        sent.append(getattr(method, "text", None))
        return True
    monkeypatch.setattr(Bot, "__call__", fake_call)
    app, _ = await make_app(scripted_company(), OWNER_TELEGRAM_ID=str(OWNER))
    bot = Bot("123456:ABC")
    dp = make_dispatcher(app, bot)
    res = {"task_id": 1, "status": "done", "result": "Javob", "files": []}

    assert await dp["push_mode"]() == "all"
    await dp["deliver"](OWNER, res)
    assert any("Javob" in (t or "") for t in sent)

    await app.store.set_kv("bot_push", "off")
    sent.clear()
    await dp["deliver"](OWNER, res)
    assert sent == []
    await bot.session.close()
    assert isinstance(Progress("x"), str)

