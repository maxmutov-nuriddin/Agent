from __future__ import annotations

import asyncio
import io
import json
import logging
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (BufferedInputFile, CallbackQuery, FSInputFile, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

from .app import App
from .providers import VoiceError, VoiceUnavailable
from .report import build_report, daily_report_loop

log = logging.getLogger("bot")
MAX_UPLOAD = 10_000_000
NAMES = {"anthropic": "Claude", "gemini": "Gemini", "openai": "ChatGPT", "auto": "Avto (eng arzoni)"}
HELP = (
    "Men AI kompaniya rahbariman. Menga oddiy yozing: gaplashishingiz, savol berishingiz yoki vazifa topshirishingiz mumkin. Qaysi biri ekanini o'zim tushunaman, kerak bo'lsa aniqlashtiraman. Fayl yoki ovozli xabar ham yuborishingiz mumkin.\n\n"
    "/team — jamoa\n/tasks — oxirgi vazifalar\n/task <id> — vazifa natijasi\n"
    "/budget — sarf\n/report — hisobot\n/memory — xotira\n"
    "/hire <nom> <nima uchun> — xodim olish\n/fire <nom> — ishdan bo'shatish\n/review — HR tahlili\n"
    "/clear — suhbat tarixini tozalash\n/pause — hammasini to'xtatish\n/resume — davom ettirish"
)


STATUS_UZ = {"done": "✅ tayyor", "cancelled": "⏹ siz to'xtatdingiz", "limit": "💸 limit tugadi", "failed": "❌ xato",
             "paused": "⏸ pauza", "interrupted": "⚠️ uzildi", "running": "⏳ ishlayapti"}


async def send_long(bot: Bot, chat_id: int, text: str, filename="natija.md"):
    if len(text) <= 3500:
        await bot.send_message(chat_id, text)
    else:
        await bot.send_message(chat_id, text[:600] + "\n\n… (to'liq natija faylda)")
        await bot.send_document(chat_id, BufferedInputFile(text.encode(), filename))


async def send_text(bot: Bot, chat_id: int, text: str):
    """Telegram 4096 belgidan uzun xabarni qabul qilmaydi: bo'laklab yuboradi."""
    text = text or "…"
    for i in range(0, len(text), 3900):
        await bot.send_message(chat_id, text[i:i + 3900])


def make_dispatcher(app: App, bot: Bot) -> Dispatcher:
    dp = Dispatcher()
    owner = app.settings.owner_id
    running: set[asyncio.Task] = set()

    async def warn(text: str):
        if owner:
            await bot.send_message(owner, text)

    app.router.on_warning = warn

    async def announce(aid, task_id, agent, description, kind="command"):
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Ruxsat" if kind == "command" else "✅ Yuborilsin", callback_data=f"ap:{aid}:y"),
            InlineKeyboardButton(text="❌ Rad", callback_data=f"ap:{aid}:n")]])
        title = "🔐 Buyruqqa ruxsat" if kind == "command" else "📨 Telegramda xabar yuborish"
        await bot.send_message(owner, f"{title} (vazifa #{task_id}, {agent})\n\n{description[:3000]}", reply_markup=kb)
    if app.center:
        app.center.announcers.append(announce)
    # Faqat egasi: boshqa hech kimga javob berilmaydi
    dp.message.filter(F.from_user.id == owner)
    dp.callback_query.filter(F.from_user.id == owner)

    @dp.callback_query(F.data.startswith("ap:"))
    async def _approval(cb: CallbackQuery):
        _, aid, ans = cb.data.split(":")
        ok = app.center.resolve(int(aid), ans == "y") if app.center else False
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

    @dp.message(Command("ai"))
    async def _ai(m: Message, command: CommandObject):
        enabled = sorted(app.router.providers)
        arg = (command.args or "").strip().lower()
        alias = {"claude": "anthropic", "chatgpt": "openai", "gpt": "openai"}
        arg = alias.get(arg, arg)
        if arg:
            if arg != "auto" and arg not in enabled:
                return await m.answer(f"❌ '{arg}' ulanmagan. Ulangan: {', '.join(NAMES[e] for e in enabled) or 'yo`q'}")
            await app.store.set_kv("primary_provider", arg)
        cur = await app.router.primary()
        options = ", ".join(["auto"] + enabled)
        await m.answer(f"🤖 Asosiy AI: {NAMES.get(cur, cur)}\nUlangan: {', '.join(NAMES[e] for e in enabled) or 'yo`q'}\n"
                       f"O'zgartirish: /ai <{options}>")

    @dp.message(F.location)
    async def _location(m: Message):
        from .tools_ext import save_location
        await save_location(app.store, m.location.latitude, m.location.longitude, bool(m.location.live_period))
        text = "📍 Joylashuv saqlandi."
        if await app.store.get_kv("await_home") == "1":
            await app.store.set_kv("await_home", "0")
            await app.store.set_kv("place:home", json.dumps({"lat": m.location.latitude, "lon": m.location.longitude, "label": "uy"}))
            text = "🏠 Uy manzili saqlandi."
        await m.answer(text)

    @dp.edited_message(F.location)
    async def _live_location(m: Message):
        from .tools_ext import save_location  # jonli joylashuv yangilanishlari: jimgina saqlanadi
        await save_location(app.store, m.location.latitude, m.location.longitude, True)

    @dp.message(Command("home"))
    async def _home(m: Message, command: CommandObject):
        from .tools_ext import geocode_text
        arg = (command.args or "").strip()
        if not arg:
            await app.store.set_kv("await_home", "1")
            return await m.answer("🏠 Uy joylashuvingizni yuboring (📎 → Joylashuv), yoki /home <manzil> deb yozing.")
        try:
            from .tools import ToolEnv
            env = ToolEnv(workspace=app.settings.workspace_dir, store=app.store, settings=app.settings)
            lat, lon, label = await geocode_text(env, arg)
        except Exception as e:  # noqa: BLE001
            return await m.answer(f"❌ Manzil topilmadi: {e}")
        await app.store.set_kv("place:home", json.dumps({"lat": lat, "lon": lon, "label": label}, ensure_ascii=False))
        await m.answer(f"🏠 Uy saqlandi: {label}")

    @dp.message(Command("tg"))
    async def _tg(m: Message):
        s = app.settings
        if not app.tg or not app.tg.configured():
            return await m.answer("Telegram akkaunt ulanmagan. Kompyuterda: python -m aicompany tglogin")
        await m.answer(f"📨 Telegram akkaunt ulangan.\nRejim: {'o`qish va yuborish (tasdiq bilan)' if s.tg_mode == 'write' else 'faqat o`qish'}\n"
                       f"Ruxsat etilgan kontaktlar: {', '.join(s.tg_allowed) or 'cheklanmagan (har xabar tasdiqlanadi)'}\n"
                       f"Maxfiy yozishmalar faqat: {', '.join(s.private_providers) or 'barcha AI provayderlarga yuborilishi mumkin'}")

    @dp.message(Command("reminders", "eslatma"))
    async def _reminders(m: Message, command: CommandObject):
        from .reminders import local_text
        args = (command.args or "").split()
        if len(args) == 2 and args[0] in ("cancel", "bekor") and args[1].isdigit():
            ok = await app.store.cancel_reminder(int(args[1]))
            return await m.answer("✅ Bekor qilindi" if ok else "Bunday kutilayotgan eslatma yo'q")
        rows = await app.store.list_reminders()
        text = "\n".join(f"#{r['id']} {local_text(r['due_at'], app.settings.report_tz)} — {r['text']}" for r in rows)
        await m.answer(("⏰ Eslatmalar:\n" + text + "\n\nBekor qilish: /reminders cancel <id>") if rows
                       else "Eslatma yo'q. Qo'yish uchun oddiy yozing: «ertaga 9:00 da ... eslat»")

    @dp.message(Command("stop"))
    async def _stop(m: Message, command: CommandObject):
        try:
            tid = int(command.args or "")
        except ValueError:
            return await m.answer("Ishlatish: /stop <vazifa raqami>")
        await m.answer("⏹ To'xtatilmoqda…" if app.orch.stop_task(tid) else "Bu vazifa hozir ishlamayapti.")

    @dp.message(Command("web", "link"))
    async def _web(m: Message):
        from .web import web_url
        if not app.settings.web_token:
            return await m.answer("Veb-panel yoqilmagan (WEB_TOKEN yo'q). `python -m aicompany run` ni qayta ishga tushiring.")
        url, reachable = web_url(app.settings)
        text = f"🌐 Veb-panel:\n{url}\n\nHavolada maxfiy kalit bor: hech kimga yubormang."
        if not reachable:
            text += ("\n\n⚠️ Bu manzil faqat kompyuterning o'zida ishlaydi. Telefondan ochish uchun .env ga "
                     "WEB_HOST=0.0.0.0 (WiFi) yoki WEB_PUBLIC_URL (Tailscale/Tunnel) qo'ying.")
        await m.answer(text, disable_web_page_preview=True)

    @dp.message(Command("clear"))
    async def _clear(m: Message):
        await app.store.clear_chat(m.chat.id)
        await m.answer("🧹 Suhbat tarixi tozalandi.")

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
            await send_text(bot, chat_id, s)
        try:
            res = await app.orch.handle(text, chat_id, notify, attachments)
            if res["kind"] == "chat":
                return  # javob allaqachon yuborilgan
            head = f"🏁 Vazifa #{res['task_id']} — {STATUS_UZ.get(res['status'], res['status'])}"
            if res.get("error"):
                head += f"\n{res['error']}"
            await send_text(bot, chat_id, head)
            if res.get("result"):
                await send_long(bot, chat_id, res["result"], f"task_{res['task_id']}.md")
            ws = app.settings.workspace_dir / f"task_{res['task_id']}"
            for rel in res.get("files", [])[:8]:
                f = ws / rel
                try:
                    if f.stat().st_size <= MAX_UPLOAD:
                        await bot.send_document(chat_id, FSInputFile(f, filename=f.name), caption=f"📎 {rel}")
                except Exception:  # noqa: BLE001 — bitta fayl yuborilmasa qolganlari yuborilsin
                    log.exception("fayl yuborilmadi: %s", rel)
            if len(res.get("files", [])) > 8:
                await bot.send_message(chat_id, f"… yana {len(res['files']) - 8} ta fayl panelda (/web)")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — foydalanuvchi javobsiz qolmasligi kerak
            log.exception("xabarni qayta ishlashda xato")
            try:
                await bot.send_message(chat_id, f"❌ Xatolik: {str(e)[:300]}")
            except Exception:  # noqa: BLE001
                pass

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
        safe = re.sub(r"[^\w.\- ]", "_", Path(d.file_name or "file").name)[:100] or "file"
        dest = inbox / f"{m.message_id}_{safe}"
        await bot.download(d, destination=dest)
        spawn(run(m.chat.id, m.caption or "Ilova qilingan fayl bilan ishlang.", [dest]))

    @dp.message(F.voice | F.audio)
    async def _voice(m: Message):
        media = m.voice or m.audio
        if media.file_size and media.file_size > 10_000_000:
            return await m.answer("Ovoz fayli juda katta (10MB limit)")
        buf = io.BytesIO()
        await bot.download(media, destination=buf)
        try:
            text = await app.router.transcribe(buf.getvalue(), media.mime_type or "audio/ogg")
        except VoiceUnavailable as e:
            return await m.answer(f"🎤 {e}. Matn yozing yoki .env ga Gemini kalitini qo'shing.")
        except VoiceError as e:
            return await m.answer(f"🎤 {e}")
        await m.answer(f"🎤 Eshitdim: {text}")
        spawn(run(m.chat.id, text))

    @dp.message(F.text & ~F.text.startswith("/"))
    async def _text(m: Message):
        spawn(run(m.chat.id, m.text))

    return dp


async def run_bot(app: App, extra_senders=()):
    s = app.settings
    if not s.telegram_token or not s.owner_id:
        raise SystemExit("TELEGRAM_BOT_TOKEN va OWNER_TELEGRAM_ID .env da bo'lishi kerak")
    bot = Bot(s.telegram_token)
    dp = make_dispatcher(app, bot)

    async def send_owner(text):
        await bot.send_message(s.owner_id, text)
    from .reminders import reminder_loop
    report = asyncio.create_task(daily_report_loop(app, send_owner))
    remind = asyncio.create_task(reminder_loop(app, [send_owner, *extra_senders]))
    try:
        await dp.start_polling(bot)
    finally:
        report.cancel()
        remind.cancel()
