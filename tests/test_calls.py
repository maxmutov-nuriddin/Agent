import array
import wave
import io

from aicompany.calls import Segmenter, pcm_to_wav, spoken_text, rms

from .conftest import scripted_company


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
    calls._last_auto = 0
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


async def test_speak_requires_gemini(make_app):
    import pytest
    from aicompany.providers import VoiceUnavailable
    app, _ = await make_app(scripted_company(), OWNER_TELEGRAM_ID="1")
    with pytest.raises(VoiceUnavailable):
        await app.router.speak("salom")
