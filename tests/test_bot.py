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
