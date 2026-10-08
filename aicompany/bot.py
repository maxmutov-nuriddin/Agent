from __future__ import annotations

import asyncio
import itertools
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (BufferedInputFile, CallbackQuery, FSInputFile, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

from .app import App
from .approvals import Approver
from .report import build_report, daily_report_loop

log = logging.getLogger("bot")
APPROVAL_TIMEOUT = 600
MAX_UPLOAD = 10_000_000
HELP = (
    "Men AI kompaniya rahbariman. Vazifani oddiy matn bilan yozing (fayl ham yuborishingiz mumkin), jamoa bajaradi.\n\n"
    "/team — jamoa\n/tasks — oxirgi vazifalar\n/task <id> — vazifa natijasi\n"
    "/budget — sarf\n/report — hisobot\n/memory — xotira\n"
    "/hire <nom> <nima uchun> — xodim olish\n/fire <nom> — ishdan bo'shatish\n/review — HR tahlili\n"
    "/pause — hammasini to'xtatish\n/resume — davom ettirish"
)


async def send_long(bot: Bot, chat_id: int, text: str, filename="natija.md"):
    if len(text) <= 3500:
        await bot.send_message(chat_id, text)
    else:
        await bot.send_message(chat_id, text[:600] + "\n\n… (to'liq natija faylda)")
        await bot.send_document(chat_id, BufferedInputFile(text.encode(), filename))


class TelegramApprover(Approver):
    def __init__(self, bot: Bot, owner_id: int):
        self.bot, self.owner_id = bot, owner_id
        self.pending: dict[int, asyncio.Future] = {}
        self._ids = itertools.count(1)

    async def ask(self, task_id, agent, description) -> bool:
        aid = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self.pending[aid] = fut
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Ruxsat", callback_data=f"ap:{aid}:y"),
            InlineKeyboardButton(text="❌ Rad", callback_data=f"ap:{aid}:n")]])
        await self.bot.send_message(
            self.owner_id, f"🔐 Ruxsat so'ralmoqda (vazifa #{task_id}, {agent})\n\n{description[:3000]}", reply_markup=kb)
        try:
            return await asyncio.wait_for(fut, APPROVAL_TIMEOUT)
        except asyncio.TimeoutError:
            return False
        finally:
            self.pending.pop(aid, None)

    def resolve(self, aid: int, ok: bool) -> bool:
        fut = self.pending.get(aid)
        if fut and not fut.done():
            fut.set_result(ok)
            return True
        return False


def make_dispatcher(app: App, bot: Bot, approver: TelegramApprover | None = None) -> Dispatcher:
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
    dp.callback_query.filter(F.from_user.id == owner)

    @dp.callback_query(F.data.startswith("ap:"))
    async def _approval(cb: CallbackQuery):
        _, aid, ans = cb.data.split(":")
        ok = approver.resolve(int(aid), ans == "y") if approver else False
        await cb.answer("Ruxsat berildi" if ok and ans == "y" else "Rad etildi" if ok else "Muddati o'tgan")
        if cb.message:
            await cb.message.edit_reply_markup(reply_markup=None)

    @dp.message(Command("start", "help"))
    async def _help(m: Message):
        await m.answer(HELP)

    @dp.message(Command("team"))
    async def _team(m: Message):
        rows = await app.store.list_agents()
        await m.answer("👥 Jamoa:\n" + "\n".join(
            f"• {a['name']} [{a['tier']}] {('🔧' + a['tools']) if a['tools'] else ''} — {a['role'][:80]}" for a in rows))

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
        lines += [f"  {r['agent']}: {r['cost']:.3f}$" for r in (await app.store.spent_by_agent())[:8]]
        await m.answer("\n".join(lines))

    @dp.message(Command("report"))
    async def _report(m: Message):
        await m.answer(await build_report(app, datetime.now(timezone.utc) - timedelta(days=1), "Hisobot (24 soat)"))

    @dp.message(Command("memory"))
    async def _memory(m: Message):
        rows = await app.store.recent_memories(15)
        await m.answer("🧠 Xotira:\n" + "\n".join(f"• {r['text']}" for r in rows) if rows else "Xotira hozircha bo'sh")

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

    @dp.message(Command("review"))
    async def _review(m: Message):
        fired = await app.team.review(10)
        await m.answer("🧑‍💼 Bo'shatildi: " + ", ".join(fired) if fired else "Hamma xodim kerak yoki yetarli ma'lumot yo'q (10 vazifa kerak).")

    async def run(chat_id: int, text: str, attachments=None):
        async def notify(s: str):
            await bot.send_message(chat_id, s)
        async with sem:
            res = await app.orch.run_task(text, chat_id, notify, attachments)
        head = f"🏁 Vazifa #{res['task_id']} — {res['status']}"
        if res.get("error"):
            head += f"\n{res['error']}"
        await bot.send_message(chat_id, head)
        if res.get("result"):
            await send_long(bot, chat_id, res["result"], f"task_{res['task_id']}.md")
        ws = app.settings.workspace_dir / f"task_{res['task_id']}"
        for rel in res.get("files", [])[:8]:
            f = ws / rel
            if f.stat().st_size <= MAX_UPLOAD:
                await bot.send_document(chat_id, FSInputFile(f, filename=f.name), caption=f"📎 {rel}")

    def spawn(coro):
        t = asyncio.create_task(coro)
        running.add(t)
        t.add_done_callback(running.discard)

    @dp.message(F.document)
    async def _doc(m: Message):
        d = m.document
        if d.file_size and d.file_size > MAX_UPLOAD:
            return await m.answer("Fayl juda katta (10MB limit)")
        inbox = app.settings.workspace_dir / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        dest = inbox / f"{m.message_id}_{(d.file_name or 'file').replace('/', '_')}"
        await bot.download(d, destination=dest)
        spawn(run(m.chat.id, m.caption or "Ilova qilingan fayl bilan ishlang.", [dest]))

    @dp.message(F.text & ~F.text.startswith("/"))
    async def _text(m: Message):
        spawn(run(m.chat.id, m.text))

    return dp


async def run_bot(app: App):
    s = app.settings
    if not s.telegram_token or not s.owner_id:
        raise SystemExit("TELEGRAM_BOT_TOKEN va OWNER_TELEGRAM_ID .env da bo'lishi kerak")
    bot = Bot(s.telegram_token)
    approver = TelegramApprover(bot, s.owner_id)
    app.orch.approver = approver
    dp = make_dispatcher(app, bot, approver)

    async def send_owner(text):
        await bot.send_message(s.owner_id, text)
    report = asyncio.create_task(daily_report_loop(app, send_owner))
    try:
        await dp.start_polling(bot)
    finally:
        report.cancel()
