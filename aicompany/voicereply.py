"""Ovozli javob: Rahbar javobini ovozli xabar qilib yuboradi (Telegram bot, agent akkaunti, panel chati).

Qachon: Sozlamalardagi rejimga ko'ra — "mirror" (standart): ovozli xabarga ovoz bilan; "always": har doim; "off": hech qachon.
"Ovozli javob ber" / "golosovoy" deb so'ralsa, rejim "off" bo'lmasa, baribir ovoz bilan.
Ovoz qo'ng'iroqdagi bilan bir xil (tanlangan ovoz va manba: Gemini yoki Edge). Matn ham doim yuboriladi.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re

log = logging.getLogger("aicompany.voicereply")
MODES = ("mirror", "always", "off")
_ASK = re.compile(r"ovozli|ovoz\s+bilan|ovozda|golos\w*|голос\w*|voice|audio\s*xabar|eshittir", re.I)
_VERB = re.compile(r"javob|ayt|yubor|qaytar|gapir|jo'?nat|ber|answer|reply|ответ|скажи|отправ|запиш", re.I)
MAX_CHARS = 700
CACHE_KEEP = 200   # panelda qayta tinglash uchun saqlanadigan oxirgi javoblar (eskilari o'chiriladi: disk to'lmasin)


def asked(text: str) -> bool:
    """"Ovozli javob ber", "golosovoy qilib yubor", "ответь голосом" ..."""
    t = text or ""
    return bool(_ASK.search(t) and _VERB.search(t))


async def mode(store) -> str:
    m = await store.get_kv("voice_reply")
    return m if m in MODES else "mirror"


async def wanted(store, text: str, came_as_voice: bool) -> bool:
    m = await mode(store)
    if m == "off":
        return False
    return m == "always" or came_as_voice or asked(text)


async def _encode(pcm: bytes, fmt: str, rate: int = 24000) -> bytes:
    args = (["-c:a", "libopus", "-b:a", "32k", "-application", "voip", "-f", "ogg"] if fmt == "ogg"
            else ["-c:a", "libmp3lame", "-b:a", "64k", "-f", "mp3"])
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0",
        *args, "pipe:1", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate(pcm)
    if proc.returncode != 0 or not out:
        raise RuntimeError(f"ovozni o'girib bo'lmadi: {err.decode(errors='ignore')[:120]}")
    return out


async def synth(app, text: str, fmt: str = "ogg") -> bytes | None:
    """Javob matni -> ovozli xabar (ogg/opus Telegram uchun, mp3 brauzer uchun). Xato bo'lsa None (matn baribir bor).
    Bir xil matn qayta so'ralsa (panelda qayta tinglash), diskdagi keshdan beriladi."""
    from .calls import spoken_text
    text = spoken_text(text or "", MAX_CHARS)
    if not text:
        return None
    voice = (await app.store.get_kv("tts_voice")) or ""
    key = hashlib.sha1(f"{voice}|{await app.router.tts_mode()}|{fmt}|{text}".encode()).hexdigest()[:20]
    d = app.settings.workspace_dir / ".voice_cache" / "replies"
    f = d / f"{key}.{fmt}"
    cache = fmt == "mp3"   # faqat panel (qayta tinglanadi); Telegramga bir marta yuboriladi, saqlash shart emas
    if cache and f.exists():
        return f.read_bytes()
    try:
        audio = await _encode(await app.router.speak(text), fmt)
    except Exception as e:  # noqa: BLE001 — ovoz bo'lmasa ham matn javob yetib boradi
        log.warning("ovozli javob yaratilmadi: %s %s", type(e).__name__, str(e)[:150])
        return None
    if cache:
        try:
            d.mkdir(parents=True, exist_ok=True)
            f.write_bytes(audio)
            old = sorted(d.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
            for p in old[:-CACHE_KEEP]:
                p.unlink(missing_ok=True)
        except OSError:
            pass
    return audio


def reply_text(res: dict) -> str:
    """Ovozda aytiladigan qism: suhbatda javobning o'zi, vazifada qisqa natija."""
    if res.get("kind") == "chat":
        return res.get("reply") or ""
    if res.get("status") == "done":
        return "Vazifa tayyor. " + (res.get("result") or "")
    return ("Vazifa bajarilmadi. " + (res.get("error") or "")) if res.get("status") else ""
