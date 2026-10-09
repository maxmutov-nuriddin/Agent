"""Qo'ng'iroqdagi ovozlar (Gemini TTS). Uslub ko'rsatmasi matn oldidan beriladi, u ovoz chiqarib o'qilmaydi.
pitch < 1: ovoz serverda pasaytiriladi (tezlik o'zgarmaydi) — yosh eshitiladigan ovozni yetuk, chuqur qiladi."""
from __future__ import annotations

import asyncio

NATURAL = ("Speak naturally and casually like a real person on a phone call: relaxed, warm and light, with lively natural "
           "intonation and small natural pauses, never robotic or announcer-like. Voice: ")
VOICES = {
    # kalit: (Gemini ovozi, uslub, pitch, panelda nomi)
    "jarvis": ("Alnilam", NATURAL + "a calm, confident, friendly man in his thirties, like the AI assistant Jarvis", 0.95, "🤵 Jarvis (tabiiy, yengil)"),
    "jarvis2": ("Charon", NATURAL + "a calm, confident, friendly man in his thirties, like the AI assistant Jarvis", 0.94, "🤵 Jarvis 2 (yumshoq)"),
    "sadaltager": ("Sadaltager", NATURAL + "a knowledgeable, easy-going adult man", 0.96, "Sadaltager (bilimdon)"),
    "algenib": ("Algenib", NATURAL + "a slightly gravelly, relaxed adult man", 0.97, "Algenib (xirillagan)"),
    "orus": ("Orus", NATURAL + "a clear, confident adult man", 0.98, "Orus (aniq)"),
    "kore": ("Kore", NATURAL + "a friendly, confident woman", 1.0, "Kore (ayol)"),
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
