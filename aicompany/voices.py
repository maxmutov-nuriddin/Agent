"""Qo'ng'iroqdagi ovozlar (Gemini TTS). Uslub ko'rsatmasi matn oldidan beriladi, u ovoz chiqarib o'qilmaydi.
pitch < 1: ovoz serverda pasaytiriladi (tezlik o'zgarmaydi) — yosh eshitiladigan ovozni yetuk, chuqur qiladi."""
from __future__ import annotations

import asyncio

MATURE = ("a mature man in his forties with a deep, resonant baritone voice; calm, confident, polite and slightly formal, "
          "unhurried measured pace, like the refined AI butler Jarvis")
VOICES = {
    # kalit: (Gemini ovozi, uslub, pitch, panelda nomi)
    "jarvis": ("Alnilam", f"Say in the voice of {MATURE}", 0.88, "🤵 Jarvis (chuqur bariton)"),
    "jarvis2": ("Charon", f"Say in the voice of {MATURE}", 0.86, "🤵 Jarvis 2 (yumshoqroq)"),
    "sadaltager": ("Sadaltager", "Say in a knowledgeable, deep, calm adult male voice", 0.9, "Sadaltager (bilimdon, past)"),
    "algenib": ("Algenib", "Say in a deep, gravelly, mature male voice, calm pace", 0.92, "Algenib (xirillagan, chuqur)"),
    "orus": ("Orus", "Say in a firm, clear, confident adult male voice", 0.93, "Orus (qat'iy)"),
    "kore": ("Kore", "", 1.0, "Kore (ayol)"),
}
DEFAULT = "jarvis"


def resolve(key: str | None) -> tuple[str, str, float]:
    name, style, pitch, _ = VOICES.get(key or DEFAULT, VOICES[DEFAULT])
    return name, style, pitch


async def shift_pitch(pcm: bytes, factor: float, rate: int = 24000) -> bytes:
    """Ovoz balandligini o'zgartiradi, tezlikni saqlab (ffmpeg). Xato bo'lsa asl ovoz qaytadi."""
    if abs(factor - 1.0) < 0.01 or not pcm:
        return pcm
    flt = f"asetrate={int(rate * factor)},aresample={rate},atempo={1 / factor:.4f}"
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0",
            "-af", flt, "-f", "s16le", "-ar", str(rate), "-ac", "1", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await proc.communicate(pcm)
        return out if proc.returncode == 0 and out else pcm
    except (OSError, FileNotFoundError):
        return pcm
