from __future__ import annotations

import argparse
import asyncio
import logging

from .app import build_app
from .approvals import CliApprover
from .config import ensure_secret
from .providers import ProviderError, suggest_model


async def cmd_check(app):
    s = app.settings
    for name, pc in s.providers.items():
        budget = f"limit {pc.budget_usd:.2f}$"
        prov = app.router.providers.get(name)
        if not prov:
            print(f"⚪️ {name}: kalit yo'q ({budget})")
            continue
        try:
            available = set(await prov.list_models())
        except ProviderError as e:
            print(f"❌ {name}: kalit ishlamadi — {str(e)[:150]}")
            continue
        print(f"🟢 {name}: kalit ishlaydi ({budget}), {len(available)} model")
        bad = []
        for tier, m in pc.models.items():
            ok = m.id in available
            print(f"    {tier:6} {m.id:28} {'✓' if ok else '✗ TOPILMADI'}")
            if not ok:
                bad.append((tier, m.id, suggest_model(m.id, sorted(available))))
        for tier, wanted, found in bad:
            print(f"    → {tier}: " + (f"taklif: {found}  (models.yaml da `id: {wanted}` ni `id: {found}` ga almashtiring)"
                                      if found else "mos nom topilmadi, quyidagi ro'yxatdan tanlang"))
        if bad:
            chat = [m for m in sorted(available) if not any(k in m for k in ("embed", "tts", "image", "live", "audio"))]
            print("    mavjud modellar:", ", ".join(chat)[:700])
        if bad and any(f for _, _, f in bad):
            print("    (Bot ishlayotganda yaqin nomni o'zi topib ishlatadi, lekin models.yaml ni tuzatgan ma'qul.)")


async def cmd_tglogin(settings):
    """Shaxsiy Telegram akkauntga bir marta kirish (telefon raqami + kod). Parol saqlanmaydi, faqat sessiya fayli."""
    import getpass
    from .tguser import APP_VERSION, DEVICE_MODEL, SYSTEM_VERSION, lock_down, session_file
    if not (settings.tg_api_id and settings.tg_api_hash):
        raise SystemExit("Avval .env ga TG_API_ID va TG_API_HASH ni yozing (https://my.telegram.org → API development tools)")
    try:
        from telethon import TelegramClient
    except ImportError:
        raise SystemExit("Avval: pip install telethon")
    client = TelegramClient(settings.tg_session, settings.tg_api_id, settings.tg_api_hash, device_model=DEVICE_MODEL,
                            system_version=SYSTEM_VERSION, app_version=APP_VERSION)
    await client.start(phone=lambda: input("Telegram telefon raqamingiz (+998...): ").strip(),
                       code_callback=lambda: input("Telegramga kelgan kod: ").strip(),
                       password=lambda: getpass.getpass("Ikki bosqichli parol (bo'lsa): "))
    me = await client.get_me()
    await client.disconnect()
    lock_down(session_file(settings))
    print(f"✅ Ulandi: {me.first_name} (@{me.username}). Sessiya: {session_file(settings)} (faqat sizga o'qiladigan qilindi).")
    print("⚠️ Sessiya fayli akkauntingizga to'liq kirish beradi: hech kimga bermang, git'ga qo'shmang.")
    print("   Chiqarib yuborish: Telegram → Sozlamalar → Qurilmalar → shu sessiyani tugatish.")
    print(f"   Hozirgi rejim: {settings.tg_mode} (yuborishni yoqish uchun .env da TG_MODE=write).")


async def cmd_ask(app, text):
    async def notify(s):
        print(s)
    res = await app.orch.run_task(text, 0, notify)
    print(f"\n=== {res['status']} (vazifa #{res['task_id']}) ===")
    print(res.get("error", ""))
    print(res.get("result", ""))


def print_web_info(s):
    from .web import web_url
    url, reachable = web_url(s)
    print("\n🌐 Veb-panel:", url)
    print("   (havola maxfiy kalitni o'z ichiga oladi: hech kimga yubormang; Telegramda /web ham yuboradi)")
    if not reachable:
        print("   Telefondan ochish uchun .env ga WEB_HOST=0.0.0.0 qo'ying (WiFi) yoki WEB_PUBLIC_URL (Tailscale/Tunnel).")
    if s.widget_token:
        print("   Vidjet kaliti: .env dagi WIDGET_TOKEN")


def panel_sender(app):
    """Eslatma panel chatida ham ko'rinsin."""
    async def send(text):
        await app.store.add_chat(app.settings.owner_id or 0, "sys", text)
    return send


async def amain():
    ap = argparse.ArgumentParser(prog="aicompany")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="Telegram bot + veb-panel")
    sub.add_parser("web", help="faqat veb-panel (Telegramsiz)")
    sub.add_parser("link", help="veb-panel havolasini chiqarish")
    sub.add_parser("tglogin", help="shaxsiy Telegram akkauntga bir marta kirish")
    sub.add_parser("check", help="kalitlar va model ID'larni tekshirish")
    ask = sub.add_parser("ask", help="vazifani terminaldan berish (Telegramsiz)")
    ask.add_argument("text")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    for noisy in ("httpx", "httpx2", "httpcore", "aiohttp.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)  # har so'rovni terminalga chiqarmaymiz
    if args.cmd == "tglogin":
        from .config import load_settings
        await cmd_tglogin(load_settings())
        return
    if args.cmd == "link":
        ensure_secret("WEB_TOKEN")
        from .config import load_settings
        print_web_info(load_settings())
        return
    if args.cmd in ("run", "web"):
        ensure_secret("WEB_TOKEN")
        ensure_secret("WIDGET_TOKEN")
    app = await build_app(approver=CliApprover() if args.cmd == "ask" else None)
    try:
        if args.cmd == "check":
            await cmd_check(app)
        elif args.cmd == "ask":
            await cmd_ask(app, args.text)
        elif args.cmd == "web":
            from .reminders import reminder_loop
            from .web import start_web
            await start_web(app)
            print_web_info(app.settings)
            listen = asyncio.create_task(app.listener.supervise())
            resume = asyncio.create_task(app.orch.resume_interrupted(panel_sender(app)))
            from .briefing import morning_loop
            morning = asyncio.create_task(morning_loop(app, [panel_sender(app)]))
            watching = asyncio.create_task(app.watch.loop([panel_sender(app)]))
            autonomous = asyncio.create_task(app.auto.loop([panel_sender(app)]))
            try:
                await reminder_loop(app, [panel_sender(app), app.push.reminder_sender()])
            finally:
                listen.cancel()
                resume.cancel()
                morning.cancel()
                watching.cancel()
                autonomous.cancel()
        else:
            from .bot import run_bot
            from .web import start_web
            runner = await start_web(app)
            print_web_info(app.settings)
            listen = asyncio.create_task(app.listener.supervise())
            try:
                await run_bot(app, extra_senders=[panel_sender(app)])
            finally:
                listen.cancel()
                await runner.cleanup()
    finally:
        await app.store.close()


if __name__ == "__main__":
    asyncio.run(amain())
