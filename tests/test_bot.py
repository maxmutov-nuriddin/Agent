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
