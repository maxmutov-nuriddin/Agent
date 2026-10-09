"""Edge TTS: Microsoft Edge ovozlari (kalitsiz, bepul, tez). Norasmiy xizmat, shuning uchun router uni zaxira qilib ishlatadi.
O'zbekcha ovozlar: Sardor (erkak), Madina (ayol). Natija: PCM 24 kHz mono (Gemini bilan bir xil format)."""
from __future__ import annotations

import asyncio

RATE = 24000
TIMEOUT = 20
FEMALE = "uz-UZ-MadinaNeural"
MALE = "uz-UZ-SardorNeural"
FEMALE_KEYS = {"kore"}


class EdgeError(Exception):
    pass


def available() -> bool:
    try:
        import edge_tts  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def voice_for(key: str | None) -> str:
    return FEMALE if key in FEMALE_KEYS else MALE


async def _stream(text: str, voice: str) -> bytes:
    import edge_tts
    mp3 = bytearray()
    async for chunk in edge_tts.Communicate(text, voice, rate="+4%").stream():
        if chunk["type"] == "audio":
            mp3 += chunk["data"]
    return bytes(mp3)


async def _to_pcm(mp3: bytes) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-f", "s16le", "-ar", str(RATE), "-ac", "1", "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate(mp3)
    if proc.returncode != 0 or not out:
        raise EdgeError(f"ovozni o'girib bo'lmadi: {err.decode(errors='ignore')[:120]}")
    return out


async def synth(text: str, voice_key: str | None = None) -> bytes:
    """Matn -> PCM. Bir marta qayta urinadi (tarmoq uzilishi uchun)."""
    if not available():
        raise EdgeError("edge-tts kutubxonasi o'rnatilmagan")
    voice, last = voice_for(voice_key), None
    for _ in range(2):
        try:
            mp3 = await asyncio.wait_for(_stream(text, voice), TIMEOUT)
            if not mp3:
                raise EdgeError("bo'sh javob")
            return await _to_pcm(mp3)
        except EdgeError as e:
            last = e
        except Exception as e:  # noqa: BLE001 — tarmoq, 403, vaqt tugashi
            last = EdgeError(f"{type(e).__name__} {e}"[:150])
    raise last
