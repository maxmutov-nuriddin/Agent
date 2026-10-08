from __future__ import annotations

import argparse
import asyncio
import logging

from .app import build_app
from .approvals import CliApprover
from .providers import ProviderError


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
        for tier, m in pc.models.items():
            ok = "✓" if m.id in available else "✗ TOPILMADI — models.yaml da tuzating"
            print(f"    {tier:6} {m.id:28} {ok}")
        if any(m.id not in available for m in pc.models.values()):
            print("    mavjud modellar:", ", ".join(sorted(available))[:600])


async def cmd_ask(app, text):
    async def notify(s):
        print(s)
    res = await app.orch.run_task(text, 0, notify)
    print(f"\n=== {res['status']} (vazifa #{res['task_id']}) ===")
    print(res.get("error", ""))
    print(res.get("result", ""))


async def amain():
    ap = argparse.ArgumentParser(prog="aicompany")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="Telegram botni ishga tushirish")
    sub.add_parser("check", help="kalitlar va model ID'larni tekshirish")
    ask = sub.add_parser("ask", help="vazifani terminaldan berish (Telegramsiz)")
    ask.add_argument("text")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    app = await build_app(approver=CliApprover() if args.cmd == "ask" else None)
    try:
        if args.cmd == "check":
            await cmd_check(app)
        elif args.cmd == "ask":
            await cmd_ask(app, args.text)
        else:
            from .bot import run_bot
            await run_bot(app)
    finally:
        await app.store.close()


if __name__ == "__main__":
    asyncio.run(amain())
