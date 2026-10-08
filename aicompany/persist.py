"""Disksiz server (Render bepul tarifi) uchun: vazifa fayllari va Telegram sessiyasi tashqi bazada saqlanadi.

Faqat tashqi baza (Supabase/Postgres) ulanganda ishlaydi; lokal SQLite'da hamma narsa odatdagidek diskda.
"""
from __future__ import annotations

import base64
import hashlib
import logging
from pathlib import Path

log = logging.getLogger("aicompany.persist")
MAX_FILE = 5_000_000    # bitta fayl
MAX_TASK = 20_000_000   # bitta vazifaning barcha fayllari


def collect(ws: Path) -> dict[str, bytes]:
    out, total = {}, 0
    for f in sorted(ws.rglob("*")):
        if not f.is_file():
            continue
        size = f.stat().st_size
        if size > MAX_FILE or total + size > MAX_TASK:
            log.warning("bazaga saqlanmadi (juda katta): %s", f)
            continue
        out[str(f.relative_to(ws))] = f.read_bytes()
        total += size
    return out


async def save_workspace(store, ws: Path, task_id: int):
    if ws.is_dir():
        await store.save_task_files(task_id, collect(ws))


async def restore_workspaces(store, workspace_dir: Path) -> int:
    """Ishga tushganda: bazadagi fayllardan diskda yo'qlarini qayta yozadi."""
    n = 0
    root = workspace_dir.resolve()
    for r in await store.load_task_files():
        base = (root / f"task_{int(r['task_id'])}").resolve()
        dest = (base / r["path"]).resolve()
        if not dest.is_relative_to(base) or dest.exists():
            continue  # yo'l tashqariga chiqmasin; mavjud faylni bosib yozmaymiz
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r["data"])
        n += 1
    return n


def _fernet(secret: str):
    from cryptography.fernet import Fernet
    key = base64.urlsafe_b64encode(hashlib.sha256(("aicompany-tg-session:" + secret).encode()).digest())
    return Fernet(key)


def seal(text: str, secret: str | None) -> str:
    """Telegram sessiyasi akkauntga to'liq kirish beradi: bazaga shifrlab yoziladi (kalit: SECRET_KEY yoki WEB_TOKEN)."""
    if not secret:
        raise ValueError("sessiyani shifrlash uchun SECRET_KEY yoki WEB_TOKEN kerak")
    return "v1:" + _fernet(secret).encrypt(text.encode()).decode()


def unseal(token: str | None, secret: str | None) -> str | None:
    if not token or not secret or not token.startswith("v1:"):
        return None
    try:
        return _fernet(secret).decrypt(token[3:].encode()).decode()
    except Exception:  # noqa: BLE001 — kalit o'zgargan: qayta ulash kerak bo'ladi
        log.warning("Telegram sessiyasini ochib bo'lmadi (SECRET_KEY/WEB_TOKEN o'zgarganmi?)")
        return None
