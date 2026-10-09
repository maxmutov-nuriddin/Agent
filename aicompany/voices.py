"""Qo'ng'iroqdagi ovozlar (Gemini TTS). Uslub ko'rsatmasi matn oldidan beriladi, u ovoz chiqarib o'qilmaydi."""
from __future__ import annotations

JARVIS = ("Say in the voice of a refined AI assistant like Jarvis: a calm, confident, polite, low and smooth male voice, "
          "measured pace, slightly formal")
VOICES = {
    "jarvis": ("Charon", JARVIS, "🤵 Jarvis (vazmin, xotirjam erkak)"),
    "orus": ("Orus", "Say in a firm, clear, confident male voice", "Orus (qat'iy erkak)"),
    "iapetus": ("Iapetus", "Say in a clear, friendly male voice", "Iapetus (aniq erkak)"),
    "algenib": ("Algenib", "Say in a deep, gravelly male voice, calm pace", "Algenib (xirillagan erkak)"),
    "kore": ("Kore", "", "Kore (ayol)"),
}
DEFAULT = "jarvis"


def resolve(key: str | None) -> tuple[str, str]:
    name, style, _ = VOICES.get(key or DEFAULT, VOICES[DEFAULT])
    return name, style
