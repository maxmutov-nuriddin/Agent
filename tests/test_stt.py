import io
import wave

from aicompany import stt

from .conftest import scripted_company


def wav(seconds=2.0, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * int(rate * seconds))
    return buf.getvalue()


def setup(monkeypatch, results, ready=("google", "azure", "whisper")):
    calls = []

    def svc(name):
        async def f(w):
            calls.append(name)
            r = results[name]
            if isinstance(r, Exception):
                raise r
            return r
        return f
    monkeypatch.setattr(stt, "SERVICES", {n: svc(n) for n in ("google", "azure", "whisper")})
    monkeypatch.setattr(stt, "configured", lambda n: n in ready or n == "gemini")
    return calls


async def test_auto_chain_moves_on_low_confidence_and_counts_minutes(make_app, monkeypatch):
    app, _ = await make_app(scripted_company(), names=("gemini",), audio="gemini matni")
    calls = setup(monkeypatch, {"google": ("salom dunyo", 0.4), "azure": ("Salom, dunyo!", 0.92), "whisper": ("x", 0.9)})
    text = await app.router.transcribe(wav(), "audio/wav")
    assert text == "Salom, dunyo!" and calls == ["google", "azure"]
    chain = stt.SpeechChain(app.router)
    assert round(await chain.used_min("google") * 60) == 2 and round(await chain.used_min("azure") * 60) == 2


async def test_free_limit_skips_provider_one_minute_before_end(make_app, monkeypatch):
    app, _ = await make_app(scripted_company(), names=("gemini",), audio="g")
    calls = setup(monkeypatch, {"google": ("a", 0.9), "azure": ("Azure matni", 0.9), "whisper": ("w", 0.9)})
    chain = stt.SpeechChain(app.router)
    await app.store.set_kv(f"stt_used:google:{chain._month()}", str(59 * 60 + 10))   # 60 daqiqadan 50 soniya qolgan
    assert await app.router.transcribe(wav(), "audio/wav") == "Azure matni" and calls == ["azure"]


async def test_all_unsure_falls_back_to_gemini_meaning(make_app, monkeypatch):
    app, provs = await make_app(scripted_company(), names=("gemini",), audio="Gemini tushundi")
    calls = setup(monkeypatch, {"google": ("bla", 0.3), "azure": RuntimeError("401"), "whisper": ("blo", 0.5)})
    assert await app.router.transcribe(wav(), "audio/wav") == "Gemini tushundi"
    assert calls == ["google", "azure", "whisper"] and provs["gemini"].audio_calls


async def test_manual_mode_and_best_effort_without_gemini(make_app, monkeypatch):
    app, _ = await make_app(scripted_company())            # Gemini yo'q
    calls = setup(monkeypatch, {"google": ("google", 0.95), "azure": ("azure", 0.5), "whisper": ("whisper", 0.55)})
    await app.store.set_kv("stt_mode", "whisper")
    assert await app.router.transcribe(wav(), "audio/wav") == "google" and calls == ["whisper", "google"]
    calls.clear()
    setup(monkeypatch, {"google": ("g", 0.2), "azure": ("az", 0.5), "whisper": ("wh", 0.4)})
    assert await app.router.transcribe(wav(), "audio/wav") == "az"             # ishonchlisi yo'q: eng yaxshisi


async def test_ffmpeg_conversion_and_chunking():
    out = await stt.to_wav16k(wav(1.0, rate=8000), "audio/wav")
    with wave.open(io.BytesIO(out)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
    parts = stt.chunks(wav(130), seconds=55)
    assert len(parts) == 3 and abs(stt.duration_s(parts[0]) - 55) < 0.01
