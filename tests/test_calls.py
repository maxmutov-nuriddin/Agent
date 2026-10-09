import array
import wave
import io

from aicompany.calls import Segmenter, pcm_to_wav, spoken_text, rms

from .conftest import scripted_company
from .test_web import web  # noqa: F401  (fixture)


def tone(ms, amp, rate=16000):
    n = rate * ms // 1000
    return array.array("h", [amp if i % 2 else -amp for i in range(n)]).tobytes()


def test_segmenter_splits_utterances():
    seg = Segmenter()
    out = []
    for _ in range(10):
        out += seg.feed(tone(100, 3000))      # 1 s gap
    for _ in range(12):
        out += seg.feed(tone(100, 0))         # 1.2 s jimlik -> bo'lak tayyor
    assert len(out) == 1 and len(out[0]) >= 16000 * 2
    for _ in range(12):
        out += seg.feed(tone(100, 0))
    assert len(out) == 1                      # jimlikning o'zi bo'lak bo'lmaydi
    out += seg.feed(tone(100, 3000))          # juda qisqa shovqin
    for _ in range(12):
        out += seg.feed(tone(100, 0))
    assert len(out) == 1


def test_wav_and_rms():
    w = pcm_to_wav(tone(200, 1000))
    with wave.open(io.BytesIO(w)) as r:
        assert r.getframerate() == 16000 and r.getnchannels() == 1
    assert rms(tone(100, 1000)) == 1000 and rms(b"") == 0


def test_spoken_text_strips_markdown_and_shortens():
    t = spoken_text("# Natija\n**Muhim**: [havola](http://x.y) va https://a.b ```code``` " + "Gap. " * 300, 200)
    assert "#" not in t and "http" not in t and "code" not in t and len(t) < 260 and t.endswith("chatda.")


async def test_task_done_only_calls_when_enabled(make_app):
    app, _ = await make_app(scripted_company(), OWNER_TELEGRAM_ID="1")
    calls = app.calls
    asked = []

    async def fake_call(text, **kw):
        asked.append(text)
        return True
    calls.call_owner = fake_call
    calls._tgc = object()
    res = {"kind": "task", "task_id": 7, "status": "done", "result": "Tayyor natija"}
    await calls.task_done(res)
    assert asked == []                         # yoqilmagan
    await app.store.set_kv("tg_call_notify", "1")
    await calls.task_done(res)
    assert len(asked) == 1 and "Tayyor natija" in asked[0] and "7" in asked[0]
    await calls.task_done(res)
    assert len(asked) == 1                     # cooldown
    calls._last_auto = float("-inf")
    await calls.task_done({"kind": "chat", "reply": "salom"})
    assert len(asked) == 1


async def test_incoming_call_from_stranger_is_ignored(make_app):
    app, _ = await make_app(scripted_company(), OWNER_TELEGRAM_ID="1")
    opened = []

    async def fake_open(chat_id, cfg):
        opened.append(chat_id)
    app.calls._open = fake_open
    await app.calls._on_incoming(999)
    assert opened == []


async def test_speak_requires_gemini(make_app, monkeypatch):
    import pytest
    from aicompany.providers import VoiceUnavailable
    app, _ = await make_app(scripted_company(), OWNER_TELEGRAM_ID="1")
    import aicompany.tts_edge as te
    monkeypatch.setattr(te, "available", lambda: False)      # Edge ham yo'q: tushunarli xato
    with pytest.raises(VoiceUnavailable):
        await app.router.speak("salom")


async def test_tts_fallback_and_cooldown(make_app, monkeypatch):
    from aicompany import tts_edge
    from aicompany.providers import VoiceError
    app, _ = await make_app(scripted_company(), OWNER_TELEGRAM_ID="1")
    r = app.router
    calls = {"edge": 0}

    async def fake_edge(text, voice=None):
        calls["edge"] += 1
        return b"\x01\x00" * 100
    monkeypatch.setattr(tts_edge, "available", lambda: True)
    monkeypatch.setattr(tts_edge, "synth", fake_edge)
    # Gemini kaliti yo'q: auto rejimda Edge aytadi
    pcm, eng = await r.speak_ex("salom")
    assert eng == "edge" and pcm and r.tts_last == "edge"
    # faqat Gemini rejimi: Edge ishlatilmaydi
    await app.store.set_kv("tts_mode", "gemini")
    import pytest
    with pytest.raises(VoiceError):
        await r.speak_ex("salom")
    # Edge-birinchi rejim; Edge xato bersa, ikkinchi gapda uni o'tkazib yuboradi (cooldown)
    await app.store.set_kv("tts_mode", "edge")

    async def boom(text, voice=None):
        calls["edge"] += 1
        raise tts_edge.EdgeError("403")
    monkeypatch.setattr(tts_edge, "synth", boom)
    n = calls["edge"]
    with pytest.raises(VoiceError):
        await r.speak_ex("salom")
    assert calls["edge"] == n + 1 and (await r.tts_status())["engines"]["edge"]["cooldown_s"] > 0
    assert tts_edge.voice_for("kore") == tts_edge.FEMALE and tts_edge.voice_for("jarvis") == tts_edge.MALE


async def test_voice_choice_and_jarvis_style(make_app):
    import base64
    from aicompany.providers import GeminiProvider
    from aicompany.voices import resolve
    assert resolve(None)[0] == "Alnilam" and "Jarvis" in resolve(None)[1] and 0.9 < resolve(None)[2] < 1   # tabiiy, biroz past
    assert resolve("kore")[0] == "Kore" and "naturally" in resolve("kore")[1] and resolve("kore")[2] == 1.0
    prov = GeminiProvider("k")
    sent = {}

    async def fake_generate(method, url, cfg, json=None, **kw):
        sent.update(json)
        return {"candidates": [{"content": {"parts": [{"inlineData": {"data": base64.b64encode(b"\0\0" * 2400).decode()}}]}}]}
    prov._generate = fake_generate
    pcm, cost = await prov.speak(None, "Salom", *resolve("jarvis")[:2])
    voice = sent["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"]
    assert voice == "Alnilam" and sent["contents"][0]["parts"][0]["text"].endswith(": Salom") and len(pcm) == 4800


async def test_voice_api(web):
    from .test_web import post
    c, app = web
    assert (await post(c, "/api/tts/voice", {"voice": "nope"}))[0] == 400
    assert (await post(c, "/api/tts/voice", {"voice": "algenib"}))[1] == {"voice": "algenib"}
    assert await app.store.get_kv("tts_voice") == "algenib"
    assert (await post(c, "/api/tts/mode", {"mode": "x"}))[0] == 400
    assert (await post(c, "/api/tts/mode", {"mode": "edge"}))[1]["order"] == ["edge", "gemini"]
    assert (await post(c, "/api/tts/mode", {"mode": "gemini"}))[0] == 200
    assert (await post(c, "/api/tts/preview", {"voice": "jarvis"}))[0] == 400     # Gemini kaliti yo'q: tushunarli xato


async def test_pitch_shift_keeps_length_and_changes_sound():
    import array
    import math
    from aicompany.voices import shift_pitch
    tone = array.array("h", [int(8000 * math.sin(2 * math.pi * 220 * i / 24000)) for i in range(24000)]).tobytes()
    out = await shift_pitch(tone, 0.88)
    assert out != tone and abs(len(out) - len(tone)) < len(tone) * 0.05      # tezlik (uzunlik) deyarli o'zgarmadi
    assert await shift_pitch(tone, 1.0) == tone
