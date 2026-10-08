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


async def amain():
    ap = argparse.ArgumentParser(prog="aicompany")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="Telegram bot + veb-panel")
    sub.add_parser("web", help="faqat veb-panel (Telegramsiz)")
    sub.add_parser("link", help="veb-panel havolasini chiqarish")
    sub.add_parser("check", help="kalitlar va model ID'larni tekshirish")
    ask = sub.add_parser("ask", help="vazifani terminaldan berish (Telegramsiz)")
    ask.add_argument("text")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
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
            from .web import start_web
            await start_web(app)
            print_web_info(app.settings)
            await asyncio.Event().wait()
        else:
            from .bot import run_bot
            from .web import start_web
            runner = await start_web(app)
            print_web_info(app.settings)
            try:
                await run_bot(app)
            finally:
                await runner.cleanup()
    finally:
        await app.store.close()


if __name__ == "__main__":
    asyncio.run(amain())
