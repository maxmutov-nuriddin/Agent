"""Havolalarni haqiqatan tekshirish: AI "hammasi tekshirildi" deb yozishi o'rniga, server har bir URL'ni o'zi ochib ko'radi.

Natija QA'ga beriladi (o'lik havolaga e'tiroz bildirsin) va NATIJA.md oxiriga sana bilan jadval qo'shiladi.
Ichki/lokal manzillar tekshirilmaydi (tools._get himoyasi).
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime

URL_RE = re.compile(r"https?://[^\s<>\"'`)\]}|]+")
MAX_URLS = 25
TIMEOUT = 10


def extract_urls(*texts: str) -> list[str]:
    seen, out = set(), []
    for t in texts:
        for u in URL_RE.findall(t or ""):
            u = u.rstrip(".,;:!?*_")
            if u not in seen:
                seen.add(u)
                out.append(u)
    return out[:MAX_URLS]


async def _status(env, url: str) -> tuple[str, str]:
    from .tools import ToolError, _get
    try:
        r = await asyncio.wait_for(_get(env, url, timeout=TIMEOUT), TIMEOUT + 5)
    except ToolError as e:
        return url, f"❌ {str(e)[:60]}"
    except Exception as e:  # noqa: BLE001 — vaqt tugashi va h.k.
        return url, f"❌ {type(e).__name__}"
    if r.status_code < 400:
        return url, f"✅ {r.status_code}"
    if r.status_code in (401, 403, 429):
        return url, f"⚠️ {r.status_code} (sayt botlarni cheklaydi: qo'lda tekshiring)"
    return url, f"❌ {r.status_code}"


async def check(env, urls: list[str]) -> dict[str, str]:
    sem = asyncio.Semaphore(6)

    async def one(u):
        async with sem:
            return await _status(env, u)
    return dict(await asyncio.gather(*(one(u) for u in urls))) if urls else {}


def report(results: dict[str, str], tz: str = "Asia/Tashkent") -> str:
    if not results:
        return ""
    from zoneinfo import ZoneInfo
    day = datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M")
    rows = "\n".join(f"| {u} | {s} |" for u, s in results.items())
    return (f"\n\n## Havolalar tekshiruvi (server avtomatik ochib ko'rdi, {day})\n\n| Havola | Holat |\n|---|---|\n{rows}\n"
            "\n✅ ochildi · ⚠️ sayt cheklaydi, qo'lda tekshiring · ❌ ochilmadi (o'lik yoki noto'g'ri havola). "
            "Havola ochilishi uning mazmuni da'voni tasdiqlashini anglatmaydi.")
