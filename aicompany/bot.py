from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message

from .app import App

log = logging.getLogger("bot")
HELP = (
    "Men AI kompaniya rahbariman. Vazifani oddiy matn bilan yozing, jamoa bajaradi.\n\n"
    "/team — jamoa\n/tasks — oxirgi vazifalar\n/task <id> — vazifa natijasi\n"
    "/budget — sarf\n/hire <nom> <nima uchun> — xodim olish\n/fire <nom> — ishdan bo'shatish\n"
    "/pause — hammasini to'xtatish\n/resume — davom ettirish"
)


async def send_long(bot: Bot, chat_id: int, text: str, filename="natija.md"):
    if len(text) <= 3500:
        await bot.send_message(chat_id, text)
    else:
        await bot.send_message(chat_id, text[:600] + "\n\n… (to'liq natija faylda)")
        await bot.send_document(chat_id, BufferedInputFile(text.encode(), filename))


def make_dispatcher(app: App, bot: Bot) -> Dispatcher:
    dp = Dispatcher()
    owner = app.settings.owner_id
    sem = asyncio.Semaphore(app.settings.max_parallel)
    running: set[asyncio.Task] = set()

    async def warn(text: str):
        if owner:
            await bot.send_message(owner, text)

    app.router.on_warning = warn
    # Faqat egasi: boshqa hech kimga javob berilmaydi
    dp.message.filter(F.from_user.id == owner)

    @dp.message(Command("start", "help"))
    async def _help(m: Message):
        await m.answer(HELP)

    @dp.message(Command("team"))
    async def _team(m: Message):
        rows = await app.store.list_agents()
        await m.answer("👥 Jamoa:\n" + "\n".join(f"• {a['name']} [{a['tier']}] — {a['role'][:90]}" for a in rows))

    @dp.message(Command("tasks"))
    async def _tasks(m: Message):
        rows = await app.store.list_tasks(10)
        await m.answer("\n".join(f"#{t['id']} [{t['status']}] {t['request'][:60]}" for t in rows) or "Vazifa yo'q")

    @dp.message(Command("task"))
    async def _task(m: Message, command: CommandObject):
        try:
            t = await app.store.get_task(int(command.args or ""))
        except ValueError:
            return await m.answer("Ishlatish: /task <id>")
        if not t:
            return await m.answer("Topilmadi")
        await send_long(bot, m.chat.id, f"#{t['id']} [{t['status']}]\n\n{t['result'] or '(natija yo`q)'}", f"task_{t['id']}.md")

    @dp.message(Command("budget"))
    async def _budget(m: Message):
        st = await app.router.status()
        lines = [f"{'🟢' if v['enabled'] else '⚪️ (kalit yo`q)'} {n}: {v['spent']:.3f}$ / {v['budget']:.2f}$" for n, v in st.items()]
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        lines.append(f"\nBugun: {await app.store.spent_since(today):.3f}$")
        by_agent = await app.store.spent_by_agent()
        lines += [f"  {r['agent']}: {r['cost']:.3f}$" for r in by_agent[:8]]
        await m.answer("\n".join(lines))

    @dp.message(Command("pause"))
    async def _pause(m: Message):
        await app.store.set_kv("paused", "1")
        await m.answer("⏸ To'xtatildi. Davom ettirish: /resume")

    @dp.message(Command("resume"))
    async def _resume(m: Message):
        await app.store.set_kv("paused", "0")
        await m.answer("▶️ Davom etamiz.")

    @dp.message(Command("hire"))
    async def _hire(m: Message, command: CommandObject):
        parts = (command.args or "").split(maxsplit=1)
        if len(parts) < 2:
            return await m.answer("Ishlatish: /hire <nom> <nima uchun kerak>")
        name = await app.team.hire(parts[0], parts[1], created_by="owner")
        await m.answer(f"✅ Yollandi: {name}" if name else "❌ Jamoa to'lgan (MAX_AGENTS)")

    @dp.message(Command("fire"))
    async def _fire(m: Message, command: CommandObject):
        ok = await app.team.fire((command.args or "").strip())
        await m.answer("✅ Ishdan bo'shatildi" if ok else "❌ Topilmadi yoki asosiy xodim (ceo/hr/qa/generalist)")

    async def run(chat_id: int, text: str):
        async def notify(s: str):
            await bot.send_message(chat_id, s)
        async with sem:
            res = await app.orch.run_task(text, chat_id, notify)
        head = f"🏁 Vazifa #{res['task_id']} — {res['status']}"
        if res.get("error"):
            head += f"\n{res['error']}"
        await bot.send_message(chat_id, head)
        if res.get("result"):
            await send_long(bot, chat_id, res["result"], f"task_{res['task_id']}.md")

    @dp.message(F.text & ~F.text.startswith("/"))
    async def _text(m: Message):
        t = asyncio.create_task(run(m.chat.id, m.text))
        running.add(t)
        t.add_done_callback(running.discard)

    return dp


async def run_bot(app: App):
    s = app.settings
    if not s.telegram_token or not s.owner_id:
        raise SystemExit("TELEGRAM_BOT_TOKEN va OWNER_TELEGRAM_ID .env da bo'lishi kerak")
    bot = Bot(s.telegram_token)
    dp = make_dispatcher(app, bot)
    await dp.start_polling(bot)
