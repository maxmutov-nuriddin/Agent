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


async def test_voice_api(web, monkeypatch):
    from aicompany import tts_edge
    monkeypatch.setattr(tts_edge, "available", lambda: True)
    from .test_web import post
    c, app = web
    assert (await post(c, "/api/tts/voice", {"voice": "nope"}))[0] == 400
    assert (await post(c, "/api/tts/voice", {"voice": "algenib"}))[1] == {"voice": "algenib"}
    assert await app.store.get_kv("tts_voice") == "algenib"
    assert (await post(c, "/api/tts/mode", {"mode": "x"}))[0] == 400
    assert (await post(c, "/api/tts/mode", {"mode": "edge"}))[1]["order"] == ["edge", "gemini"]
    assert (await post(c, "/api/tts/mode", {"mode": "gemini"}))[0] == 400     # Gemini kaliti yo'q: tanlab bo'lmaydi
    monkeypatch.setattr(tts_edge, "available", lambda: False)
    assert (await post(c, "/api/tts/mode", {"mode": "edge"}))[0] == 400       # Edge o'rnatilmagan: tanlab bo'lmaydi
    assert (await post(c, "/api/tts/preview", {"voice": "jarvis"}))[0] == 400     # Gemini kaliti yo'q: tushunarli xato


async def test_pitch_shift_keeps_length_and_changes_sound():
    import array
    import math
    from aicompany.voices import shift_pitch
    tone = array.array("h", [int(8000 * math.sin(2 * math.pi * 220 * i / 24000)) for i in range(24000)]).tobytes()
    out = await shift_pitch(tone, 0.88)
    assert out != tone and abs(len(out) - len(tone)) < len(tone) * 0.05      # tezlik (uzunlik) deyarli o'zgarmadi
    assert await shift_pitch(tone, 1.0) == tone


async def test_say_survives_tts_failure(make_app):
    from aicompany.calls import Call
    from aicompany.providers import VoiceError
    app, _ = await make_app(scripted_company(), OWNER_TELEGRAM_ID="1")

    async def boom(text, **kw):
        raise VoiceError("hammasi ishlamadi")
    app.router.speak = boom
    call = Call(1)
    await app.calls.say(call, "salom")
    await app.calls.say(call, "salom")
    assert not call.closed and call.say_fails == 2     # qo'ng'iroq uzilmaydi
    await app.calls.say(call, "salom")
    assert call.closed                                  # 3 marta ketma-ket: jim qo'ng'iroq tugatiladi


async def test_free_ai_gating(make_app):
    """Bepul AI (Groq, OpenRouter): faqat yoqilgan + free_ok + cheap + asbobsiz. Xato bersa dam oladi va keyingisiga o'tadi."""
    from aicompany.providers import ProviderError
    fail = set()

    def handler(system, user, model):
        if model.startswith("groq"):
            return ProviderError("429 limit") if "groq" in fail else "groq javob"
        if model.endswith(":free"):
            return ProviderError("429 limit") if "or" in fail else "openrouter javob"
        return "pullik javob"
    app, provs = await make_app(handler, names=("anthropic", "openrouter", "groq"), OWNER_TELEGRAM_ID="1")
    r = app.router
    msgs = [{"role": "user", "content": "x"}]

    async def run(tier="cheap", **kw):
        return (await r.call(tier, "s", msgs, **kw)).provider
    assert await run(free_ok=True) == "anthropic"          # o'chiq: ishlatilmaydi
    await app.store.set_kv("openrouter_on", "1")
    assert await run() == "anthropic"                       # free_ok berilmagan (suhbat/agent): ishlatilmaydi
    assert await run(free_ok=True, tools=[{"name": "t", "description": "", "input_schema": {}}]) == "anthropic"
    assert await run(free_ok=True) == "openrouter"          # faqat OpenRouter yoqilgan
    assert await run("mid", free_ok=True) == "anthropic"    # faqat cheap daraja
    await app.store.set_kv("groq_on", "1")
    assert await run(free_ok=True) == "groq"                # ikkalasi yoqiq: avval Groq
    fail.add("groq")
    assert await run(free_ok=True) == "openrouter"          # Groq xato: OpenRouter
    assert (await r.free_status())["groq"]["cooldown_s"] > 0
    fail.add("or")
    assert await run(free_ok=True) == "anthropic"           # ikkalasi xato: pullik
    fail.clear()
    n = len(provs["groq"].calls)
    assert await run(free_ok=True) == "anthropic" and len(provs["groq"].calls) == n   # dam olayotganda urinmaydi
    r._free_down.clear()
    assert await run(free_ok=True) == "groq"


async def test_free_ai_api(web):
    from .test_web import post
    c, app = web
    assert (await post(c, "/api/free_ai", {"name": "groq", "enabled": True}))[0] == 400       # kalit yo'q
    assert (await post(c, "/api/free_ai", {"name": "x", "enabled": False}))[0] == 400
    assert (await post(c, "/api/free_ai", {"name": "groq", "enabled": False}))[0] == 200


async def test_groq_stt(make_app, monkeypatch):
    from aicompany import stt
    app, _ = await make_app(scripted_company(), OWNER_TELEGRAM_ID="1")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("WHISPER_MODEL", "off")
    chain = stt.SpeechChain(app.router)
    got = []

    async def fake_groq(wav):
        got.append(1)
        return "salom dunyo", 0.9
    monkeypatch.setitem(stt.SERVICES, "groq", fake_groq)
    wav = stt.frames_to_wav(b"\0\0" * 16000)

    async def gem(a, m):
        return "gemini"
    import pytest
    from aicompany.providers import VoiceUnavailable
    with pytest.raises(VoiceUnavailable):
        await chain.transcribe(wav, "audio/wav", gem)      # Groq o'chiq: o'tkazib yuboriladi (boshqa xizmat yo'q)
    assert not got
    await app.store.set_kv("groq_on", "1")
    assert await chain.transcribe(wav, "audio/wav", gem) == "salom dunyo" and got      # yoqildi: Gemini'dan oldin ishlaydi
    assert (await chain.status())["services"]["groq"]["ready"]


async def test_openrouter_provider_request():
    from aicompany.config import ModelCfg
    from aicompany.providers import OpenRouterProvider
    prov = OpenRouterProvider("k")
    sent = {}

    async def fake_gen(method, url, cfg, json=None, **kw):
        sent.update(url=url, body=json)
        return {"choices": [{"message": {"content": "salom"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 3}}
    prov._generate = fake_gen
    res = await prov.complete(ModelCfg("m:free", 0, 0, 0), "sys", [{"role": "user", "content": "x"}], 100)
    assert res.text == "salom" and res.cost_usd == 0 and sent["url"].startswith("https://openrouter.ai/api/v1") and sent["body"]["max_tokens"] == 100
    assert prov._headers()["Authorization"] == "Bearer k"


async def test_stt_mode_requires_ready_service(web):
    from .test_web import post
    c, app = web
    assert (await post(c, "/api/stt", {"mode": "azure"}))[0] == 400       # Azure kaliti yo'q
    assert (await post(c, "/api/stt", {"mode": "auto"}))[0] == 200


async def test_call_owner_resolves_unknown_peer(make_app):
    """Telethon egasini tanimasa: avval dialoglarni yuklaydi; baribir topilmasa tushunarli yo'l-yo'riq qaytaradi."""
    app, _ = await make_app(scripted_company(), OWNER_TELEGRAM_ID="1")
    calls = app.calls

    class Client:
        def __init__(self, known_after_dialogs):
            self.loaded, self.known_after = False, known_after_dialogs

        async def get_input_entity(self, cid):
            if not (self.loaded and self.known_after):
                raise ValueError("Could not find the input entity")
            return object()

        async def get_dialogs(self, limit=None):
            self.loaded = True
    calls._tgc = object()

    async def ids():
        return [42]
    calls._owner_ids = ids
    calls._client = Client(True)
    assert await calls._ensure_peer(42)                       # dialoglardan topildi
    calls._client = Client(False)
    assert not await calls.call_owner("salom")                # topilmadi: qo'ng'iroq qilinmaydi
    assert "bitta xabar yozing" in calls.last_error
    app.orch.call_ready = lambda: True
    app.orch.call_owner = calls.call_owner
    app.orch.call_error = lambda: calls.last_error
    res = await app.orch.handle("menga qo'ng'iroq qil hozir", 5)
    assert "bitta xabar yozing" in res["reply"]               # foydalanuvchi sababini ko'radi


async def test_greeting_is_short_manager_line_and_cached(make_app):
    from aicompany.calls import GREETING, Call
    assert "menejeringizman" in GREETING and len(GREETING) < 60
    app, _ = await make_app(scripted_company(), OWNER_TELEGRAM_ID="1")
    n = {"speak": 0, "played": 0}

    async def fake_speak(text, **kw):
        n["speak"] += 1
        return b"\0\0" * 100

    async def fake_play(call, pcm, pause=0.3):
        n["played"] += 1
    app.router.speak = fake_speak
    app.router.speak_ex = lambda text, **kw: _pair(fake_speak(text))
    app.calls._play = fake_play
    call = Call(1)
    await app.calls.say(call, GREETING)
    await app.calls.say(call, GREETING)
    assert n["played"] == 2 and n["speak"] == 1       # ikkinchi marta keshdan: AI sarflanmadi
    assert "menejer" in app.orch.TALK_STYLE


async def _pair(coro):
    return await coro, "gemini"


def test_split_speech_makes_short_first_chunk():
    from aicompany.calls import split_speech
    assert split_speech("Salom.") == ["Salom."]
    t = "Bugun ob-havo yaxshi, quyoshli. Kechqurun biroz yomg'ir yog'ishi mumkin. Soat beshda uchrashuvingiz bor. Unutmang!"
    ch = split_speech(t)
    assert 2 <= len(ch) <= 3 and " ".join(ch) == t and len(ch[0]) < len(t)


async def test_say_plays_chunks_in_order(make_app):
    from aicompany.calls import Call
    app, _ = await make_app(scripted_company(), OWNER_TELEGRAM_ID="1")
    played = []

    async def fake_speak(text, **kw):
        import asyncio
        await asyncio.sleep(0.05 if text.startswith("Birinchi") else 0)   # birinchisi sekinroq tayyor bo'lsa ham tartib saqlanadi
        return text.encode()

    async def fake_play(call, pcm, pause=0.3):
        played.append(pcm.decode())
    app.router.speak = fake_speak
    app.calls._play = fake_play
    await app.calls.say(Call(1), "Birinchi gap ancha uzun bo'lsin deb yozildi. Ikkinchi gap ham uzunroq yozildi mana. Uchinchi.")
    assert len(played) >= 2 and played[0].startswith("Birinchi") and "Ikkinchi" in " ".join(played)
    assert "tts" in app.calls.timing


async def test_voice_reply_rules(make_app):
    from aicompany import voicereply
    app, _ = await make_app(scripted_company(), OWNER_TELEGRAM_ID="1")
    st = app.store
    assert voicereply.asked("ovozli javob ber") and voicereply.asked("golosovoy qilib yubor") and voicereply.asked("ответь голосом")
    assert not voicereply.asked("bugun ob-havo qanday") and not voicereply.asked("ovozli qo'ng'iroq sozlamasi")
    assert await voicereply.wanted(st, "salom", True)          # ovozga ovoz (standart)
    assert not await voicereply.wanted(st, "salom", False)
    assert await voicereply.wanted(st, "ovozli javob ber, nima gap", False)
    await st.set_kv("voice_reply", "always")
    assert await voicereply.wanted(st, "salom", False)
    await st.set_kv("voice_reply", "off")
    assert not await voicereply.wanted(st, "ovozli javob ber", True)
    assert voicereply.reply_text({"kind": "chat", "reply": "Ha"}) == "Ha"
    assert voicereply.reply_text({"kind": "task", "status": "done", "result": "R"}).startswith("Vazifa tayyor")


async def test_voice_reply_web(web, monkeypatch):
    from aicompany import voicereply
    from .test_web import post
    c, app = web
    seen = []

    async def handle(text, chat_id, notify, **kw):
        seen.append(kw.get("voice"))
        return {"kind": "chat", "reply": "ok"}
    app.orch.handle = handle
    status, d = await post(c, "/api/chat", {"text": "salom", "voice": True})
    assert status == 200 and d["voice"] is True
    status, d = await post(c, "/api/chat", {"text": "salom"})
    assert d["voice"] is False
    import asyncio
    await asyncio.sleep(0.05)
    assert seen == [True, False]
    assert (await post(c, "/api/voice_reply", {"mode": "x"}))[0] == 400
    assert (await post(c, "/api/voice_reply", {"mode": "always"}))[1] == {"mode": "always"}

    async def fake_synth(app_, text, fmt="ogg"):
        return b"ID3mp3" if fmt == "mp3" else None
    monkeypatch.setattr(voicereply, "synth", fake_synth)
    r = await c.post("/api/tts/say", json={"text": "salom"}, headers={"Authorization": "Bearer " + app.settings.web_token})
    assert r.status == 200 and r.headers["Content-Type"].startswith("audio/mpeg") and await r.read() == b"ID3mp3"


async def test_voice_reply_cache_is_bounded(make_app, monkeypatch):
    from aicompany import voicereply

    app, _ = await make_app(scripted_company(), OWNER_TELEGRAM_ID="1")

    async def speak(text, **kw):
        return b"\0\0" * 10

    async def enc(pcm, fmt, rate=24000):
        return b"A" + fmt.encode()
    app.router.speak = speak
    monkeypatch.setattr(voicereply, "_encode", enc)
    monkeypatch.setattr(voicereply, "CACHE_KEEP", 3)
    for i in range(6):
        assert await voicereply.synth(app, f"javob {i}", "mp3") == b"Amp3"
    assert await voicereply.synth(app, "tg uchun", "ogg") == b"Aogg"
    d = app.settings.workspace_dir / ".voice_cache" / "replies"
    assert len(list(d.glob("*.mp3"))) == 3 and not list(d.glob("*.ogg"))     # eskilari o'chadi, Telegram ovozi saqlanmaydi
