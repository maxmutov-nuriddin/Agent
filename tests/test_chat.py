import json

import sqlalchemy as sa

from .conftest import scripted_company


def desk(decisions):
    """Front desk javoblari ketma-ket; qolgan chaqiruvlar oddiy kompaniya."""
    seq = list(decisions)
    base = scripted_company()
    seen, plans, systems = [], [], []

    def handler(system, user, model):
        if "Plan the work" in user:
            plans.append(user)
        if "front desk" in system or "in a CHAT" in system:
            seen.append((user, model))
            systems.append(system)
            d = seq.pop(0)
            return d if isinstance(d, str) else json.dumps(d)
        return base(system, user, model)
    handler.seen, handler.plans, handler.systems = seen, plans, systems
    return handler


async def n_tasks(app):
    return len(await app.store.list_tasks(100))


async def test_greeting_is_chat_not_task(make_app):
    h = desk([{"mode": "chat", "reply": "Salom! Qanday yordam beray?"}])
    app, provs = await make_app(h)
    notes = []

    async def notify(s):
        notes.append(s)
    res = await app.orch.handle("salom", 1, notify)
    assert res == {"kind": "chat", "reply": "Salom! Qanday yordam beray?", "proposed_task": None}
    assert notes == ["Salom! Qanday yordam beray?"] and await n_tasks(app) == 0
    assert provs["anthropic"].calls == ["claude-haiku-5-5"]  # bitta arzon chaqiruv, jamoa ishlamadi


async def test_clear_task_runs_after_acknowledgement(make_app):
    h = desk([{"mode": "task", "reply": "Boshladim", "task": "Write 5 Instagram ideas for a coffee shop"}])
    app, _ = await make_app(h)
    notes = []

    async def notify(s):
        notes.append(s)
    res = await app.orch.handle("kofexona uchun 5 ta post g'oyasi", 1, notify)
    assert res["kind"] == "task" and res["status"] == "done"
    assert notes[0] == "Boshladim"
    t = await app.store.get_task(res["task_id"])
    assert t["request"] == "Write 5 Instagram ideas for a coffee shop"  # kengaytirilgan, to'liq vazifa


async def test_clarifying_question_then_task_uses_history(make_app):
    h = desk([
        {"mode": "chat", "reply": "Qaysi shahar uchun?"},
        {"mode": "task", "reply": "Tushunarli", "task": "Make a plan for a coffee shop in Tashkent"}])
    app, _ = await make_app(h)
    r1 = await app.orch.handle("reja tuz", 1)
    assert r1["kind"] == "chat" and await n_tasks(app) == 0
    r2 = await app.orch.handle("Toshkent", 1)
    assert r2["kind"] == "task"
    second_prompt = h.seen[1][0]
    assert "Owner: reja tuz" in second_prompt and "CEO: Qaysi shahar uchun?" in second_prompt
    assert second_prompt.rstrip().endswith("Toshkent")


async def test_followup_can_see_previous_task_result(make_app):
    h = desk([{"mode": "task", "reply": "", "task": "t1"}, {"mode": "chat", "reply": "ok"}])
    app, _ = await make_app(h)
    await app.orch.handle("birinchi", 1)
    await app.orch.handle("buni qisqartir", 1)
    assert "FINAL DELIVERABLE" in h.seen[1][0] and "Vazifa #1 done" in h.seen[1][0]


async def test_unparseable_decision_falls_back_to_chat_cheaply(make_app):
    app, _ = await make_app(desk(["Salom, men shu yerdaman!"]))
    res = await app.orch.handle("salom", 1)
    assert res["kind"] == "chat" and "shu yerdaman" in res["reply"] and await n_tasks(app) == 0


async def test_task_mode_without_task_text_uses_original_message(make_app):
    app, _ = await make_app(desk([{"mode": "task", "reply": "ok", "task": ""}]))
    res = await app.orch.handle("sayt yasab ber", 1)
    assert res["kind"] == "task"
    assert (await app.store.get_task(res["task_id"]))["request"] == "sayt yasab ber"


async def test_attachment_forces_task_without_front_desk(make_app, tmp_path):
    h = desk([])  # front desk chaqirilsa pop xatosi beradi
    app, _ = await make_app(h)
    f = tmp_path / "brief.txt"
    f.write_text("x")
    res = await app.orch.handle("buni ko'rib chiq", 1, attachments=[f])
    assert res["kind"] == "task" and h.seen == []


async def test_history_is_per_chat_and_clearable(make_app):
    h = desk([{"mode": "chat", "reply": "a"}, {"mode": "chat", "reply": "b"}, {"mode": "chat", "reply": "c"}])
    app, _ = await make_app(h)
    await app.orch.handle("one", 1)
    await app.orch.handle("two", 2)
    assert "Owner: one" not in h.seen[1][0]  # boshqa chat tarixi aralashmaydi
    await app.store.clear_chat(1)
    await app.orch.handle("three", 1)
    assert "Owner: one" not in h.seen[2][0]


async def test_chat_cost_is_tracked_under_ceo_chat(make_app):
    app, _ = await make_app(desk([{"mode": "chat", "reply": "hi"}]))
    await app.orch.handle("salom", 1)
    rows = await app.store._all(sa.text("select agent, model from usage"))
    assert rows == [{"agent": "ceo-chat", "model": "claude-haiku-5-5"}]


async def test_parallel_task_limit(make_app):
    import asyncio
    active, peak = 0, 0
    base = scripted_company()

    def handler(system, user, model):
        return base(system, user, model)
    app, _ = await make_app(handler, MAX_PARALLEL_TASKS="1")
    orig = app.orch._run

    async def tracked(*a, **k):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        try:
            return await orig(*a, **k)
        finally:
            active -= 1
    app.orch._run = tracked
    await asyncio.gather(app.orch.run_task("a", 1), app.orch.run_task("b", 1))
    assert peak == 1



async def test_front_desk_can_continue_an_earlier_task(make_app):
    h = desk([{"mode": "task", "reply": "", "task": "landing"},
              {"mode": "task", "reply": "O'zgartiraman", "task": "Sarlavhani qizil qil", "based_on": 1},
              {"mode": "task", "reply": "", "task": "yangi ish", "based_on": 77}])      # mavjud bo'lmagan vazifa
    app, _ = await make_app(h)
    await app.orch.handle("landing yarat", 1)
    (app.settings.workspace_dir / "task_1" / "page.html").write_text("eski")
    r2 = await app.orch.handle("sarlavhani qizil qil", 1)
    t2 = await app.store.get_task(r2["task_id"])
    assert t2["based_on"] == 1 and (app.settings.workspace_dir / f"task_{r2['task_id']}" / "page.html").exists()
    assert t2["request"] == "Sarlavhani qizil qil"                                         # bazada sizning matningiz
    assert "continues task #1" in h.plans[-1] and "page.html" in h.plans[-1]               # rahbar kontekstni oladi
    r3 = await app.orch.handle("yangi narsa", 1)
    assert (await app.store.get_task(r3["task_id"]))["based_on"] is None                   # noto'g'ri raqam e'tiborsiz


async def test_chat_proposal_carries_based_on(make_app):
    h = desk([{"mode": "task", "reply": "", "task": "landing"},
              {"reply": "Shu saytni o'zgartiraymi?", "proposed_task": "Rangni o'zgartir", "based_on": "#1"}])
    app, _ = await make_app(h)
    await app.orch.handle("landing yarat", 1)
    r = await app.orch.handle("rangini o'zgartirsa bo'ladimi?", 1, allow_tasks=False)
    assert r["kind"] == "chat" and r["proposed_task"] == "Rangni o'zgartir"
    prop = [m for m in await app.store.recent_chat(1, 20) if m["role"] == "proposal"][-1]
    assert json.loads(prop["text"]) == {"task": "Rangni o'zgartir", "based_on": 1}


async def test_small_talk_hides_old_task_summaries_and_talk_only_mode(make_app):
    import json
    seen = []
    base = scripted_company()

    def handler(system, user, model):
        if "front desk" in system or "CEO of an AI company" in system:
            seen.append((system, user))
            if "CHAT" in system and "FRONT DESK" not in system and "front desk" not in system:
                return json.dumps({"reply": "Yaxshi, o'zingiz qalaysiz?", "proposed_task": ""})
            return json.dumps({"mode": "chat", "reply": "Yaxshi, o'zingiz qalaysiz?", "task": ""})
        return base(system, user, model)
    app, _ = await make_app(handler, OWNER_TELEGRAM_ID="1")
    await app.store.add_chat(5, "ceo", "[Vazifa #3 interrupted] Uzildi (dastur qayta yoqilgan)")
    await app.orch.handle("nma gap", 5)
    assert "Uzildi" not in seen[-1][1] and "CONVERSATION STYLE" in seen[-1][0]
    await app.orch.handle("vazifa nima bo'ldi", 5)
    assert "Uzildi" in seen[-1][1]                      # so'ralsa, eski vazifa ko'rinadi
    await app.store.set_kv("talk_only", "1")
    await app.orch.handle("salom", 5)
    assert "NEVER start or claim to do work" in seen[-1][0]


async def test_owner_preferences_are_remembered_and_applied(make_app):
    seen = []
    base = scripted_company()
    state = {"n": 0}

    def handler(system, user, model):
        if "front desk" in system:
            seen.append(user)
            state["n"] += 1
            if state["n"] == 1:
                return json.dumps({"mode": "chat", "reply": "Xo'p, so'ramaguningizcha aytmayman.",
                                   "task": "", "remember": "Eski muammolarni so'ramagunimcha eslatma"})
            return json.dumps({"mode": "chat", "reply": "ok", "task": ""})
        return base(system, user, model)
    app, _ = await make_app(handler, OWNER_TELEGRAM_ID="1")
    await app.orch.handle("eski muammolarni so'ramagunimcha aytma", 5)
    prefs = await app.store.owner_prefs()
    assert [p["text"] for p in prefs] == ["Eski muammolarni so'ramagunimcha eslatma"]
    await app.orch.handle("salom", 5)
    assert "standing preferences" in seen[-1] and "so'ramagunimcha" in seen[-1]
    await app.orch._save_pref("Eski muammolarni so'ramagunimcha eslatma")        # takror yozilmaydi
    assert len(await app.store.owner_prefs()) == 1


async def test_call_now_and_phone_reminder(make_app):
    base = scripted_company()
    calls = []

    def handler(system, user, model):
        if "front desk" in system:
            if "qo'ng'iroq qil" in user.split("# Latest owner message\n")[-1]:
                return json.dumps({"mode": "chat", "reply": "Qo'ng'iroq qilaman", "task": "", "call": "Vazifa tayyor"})
            return json.dumps({"mode": "chat", "reply": "", "task": "",
                               "reminder": {"when": "+30m", "text": "dori ich", "call": True}})
        return base(system, user, model)
    app, _ = await make_app(handler, OWNER_TELEGRAM_ID="1")

    async def fake_call(text, **kw):
        calls.append(text)
        return True
    app.orch.call_owner = fake_call
    app.orch.call_ready = lambda: True
    res = await app.orch.handle("menga qo'ng'iroq qil", 5)
    assert calls == ["Vazifa tayyor"] and res["kind"] == "chat"
    res = await app.orch.handle("30 daqiqadan keyin tel qilib eslat", 5)
    assert "qo'ng'iroq" in res["reply"]
    rid = (await app.store.list_reminders())[0]["id"]
    assert await app.store.get_kv(f"rcall:{rid}") == "1"
    import asyncio
    from aicompany.reminders import deliver_due
    async with app.store.engine.begin() as c:
        import sqlalchemy as sa
        from aicompany.db import reminders as rt
        await c.execute(sa.update(rt).values(due_at="2000-01-01T00:00:00+00:00"))
    sent = []

    async def send(t):
        sent.append(t)
    await deliver_due(app, [send])
    await asyncio.sleep(0.05)                                                   # qo'ng'iroq fonda
    assert sent and calls[-1] == "Eslatma. dori ich"


async def test_older_messages_are_recalled_and_long_chats_summarized(make_app):
    seen = []
    base = scripted_company()

    def handler(system, user, model):
        if "running summary" in system:
            seen.append(("sum", user))
            return "Egasi kafe ochmoqchi; byudjet 5 ming dollar."
        if "front desk" in system:
            seen.append(("fd", user))
            return json.dumps({"mode": "chat", "reply": "ok", "task": ""})
        return base(system, user, model)
    app, _ = await make_app(handler, OWNER_TELEGRAM_ID="1")
    st = app.store
    await st.add_chat(7, "owner", "Mening g'oyam: Toshkentda kofe yetkazib berish xizmati ochish")
    await st.add_chat(7, "ceo", "Yaxshi g'oya, bozorni o'rganamiz")
    for i in range(30):                                   # uzun suhbat: g'oya oynadan chiqib ketadi
        await st.add_chat(7, "owner" if i % 2 == 0 else "ceo", f"gap {i}")
    await app.orch.handle("o'tgan aytgan kofe yetkazib berish g'oyam nima edi", 7)
    import asyncio
    await asyncio.sleep(0.3)
    fd = [u for k, u in seen if k == "fd"][-1]
    assert "Possibly relevant older messages" in fd and "kofe yetkazib berish xizmati" in fd
    assert fd.count("gap ") <= app.orch.CHAT_WINDOW        # oyna 16 ta
    sums = [u for k, u in seen if k == "sum"]
    assert sums and "kofe" in sums[0]
    await app.orch.handle("salom", 7)
    assert "Summary of the earlier conversation" in [u for k, u in seen if k == "fd"][-1]
    await st.clear_chat(7)
    assert await app.orch._chat_summary(7) == ""


async def test_same_reminder_is_merged_not_duplicated_and_call_needs_module(make_app):
    base = scripted_company()
    n = {"i": 0}

    def handler(system, user, model):
        if "front desk" in system:
            n["i"] += 1
            return json.dumps({"mode": "chat", "reply": "", "task": "",
                               "reminder": {"when": "+120m", "text": "Uyg'onish vaqti: soat 9:00", "call": n["i"] == 2}})
        return base(system, user, model)
    app, _ = await make_app(handler, OWNER_TELEGRAM_ID="1")
    r1 = await app.orch.handle("ertaga 9 da uygot", 5)
    assert "qo'yildi" in r1["reply"]
    r2 = await app.orch.handle("telefon qilib uygotgin", 5)
    assert "yangilandi" in r2["reply"] and "tayyor emas" in r2["reply"]        # modul yo'q: halol aytadi, istak saqlanadi
    assert len(await app.store.list_reminders()) == 1                           # takror yo'q
    app.orch.call_ready = lambda: True
    n["i"] = 1
    await app.orch.handle("telefon qilib uygotgin", 5)
    assert len(await app.store.list_reminders()) == 1
    rid = (await app.store.list_reminders())[0]["id"]
    assert await app.store.get_kv(f"rcall:{rid}") == "1"


async def test_owner_plan_dictated_in_chat_goes_to_plans_page(make_app):
    base = scripted_company()

    def handler(system, user, model):
        if "front desk" in system:
            return json.dumps({"mode": "chat", "reply": "", "task": "",
                               "plan": {"period": "day", "title": "Bugungi reja", "items": ["Bozorga borish", "Aliga qo'ng'iroq"]}})
        return base(system, user, model)
    app, _ = await make_app(handler, OWNER_TELEGRAM_ID="1")
    res = await app.orch.handle("bugungi rejam: bozor, Aliga qo'ng'iroq", 5)
    assert "Reja saqlandi" in res["reply"]
    plans = await app.store.list_plans()
    assert plans[0]["title"] == "Bugungi reja" and "Bozorga borish" in plans[0]["items"]
    assert await app.store.list_tasks() == []                              # vazifa ochilmadi


async def test_timed_phone_request_never_calls_now_and_reports_failure(make_app):
    import asyncio
    base = scripted_company()

    def handler(system, user, model):
        if "front desk" in system:   # model ikkalasini qaytardi: hozir qo'ng'iroq + eslatma
            return json.dumps({"mode": "chat", "reply": "", "task": "", "call": "Uyg'oning!",
                               "reminder": {"when": "+60m", "text": "Uyg'onish"}})
        return base(system, user, model)
    app, _ = await make_app(handler, OWNER_TELEGRAM_ID="1")
    calls = []

    async def fake_call(text, **kw):
        calls.append(text)
        return False                                                          # ko'tarilmadi
    app.orch.call_owner = fake_call
    app.orch.call_ready = lambda: True
    res = await app.orch.handle("ertaga 7 da telefon qilib uyg'ot", 5)
    assert calls == [] and "qo'ng'iroq qilaman" in res["reply"]                 # hozir emas, vaqtida
    rid = (await app.store.list_reminders())[0]["id"]
    assert await app.store.get_kv(f"rcall:{rid}") == "1"
    from aicompany.reminders import phone_reminder
    app.calls.last_error = "qo'ng'iroq qilib bo'lmadi: javob yo'q"
    sent = []

    async def send(t):
        sent.append(t)
    ok = await phone_reminder(app, "Uyg'onish", [send], retry_after=0)
    assert not ok and len(calls) == 2 and "qila olmadim" in sent[0] and "javob yo'q" in sent[0]   # 2 urinish, keyin sabab


def test_call_intent_and_time_parsing():
    from datetime import datetime, timezone
    from aicompany.reminders import call_requested, call_when
    now = datetime(2026, 10, 9, 10, 0, tzinfo=timezone.utc)      # Toshkent 15:00
    tz = "Asia/Tashkent"
    yes = ("menga qo'ng'iroq qil", "manga qongiroq qil hozir", "tel qil", "мне позвони", "call me", "tel ql", "menga tel qlb ber",
           "telefon qil", "telefon qilib yuboring", "manga tel ur", "tel qiling", "meni telefon qilgin", "qongirok qil", "kongiroq qil",
           "qo\u2018ng\u2018iroq qil", "qo`ng`iroq qil", "zvonok qil", "zvon qil", "menga zvon ber", "qo'ng'iroq bering", "menga bog'lan",
           "men bilan aloqaga chiqing", "meni chaqir", "тел қил", "телефон қил", "қўнғироқ қил", "позвони мне", "набери меня",
           "10 daqiqadan keyin tel qil", "ertaga 7 da telefon qilib uyg'ot", "hozir telefon qil")
    for t in yes:
        assert call_requested(t), t
    no = ("qo'ng'iroq qila olasanmi?", "qo'ng'iroq qanday ishlaydi", "bugun ob-havo qanday", "vazifa tayyor bo'lsin",
          "telefon raqamim 901234567", "telefonimning batareyasi tugadi", "kecha onamga tel qildim", "telefon narxi qancha",
          "telefon sozlamasini qil", "onamga tel qilishni eslat", "tel qilgan edingmi",
          "marketologni chaqir", "HR ni chaqirib yangi xodim ol", "internet bog'lanishi yo'q, tekshir", "menga bog'landingmi")
    for t in no:
        assert not call_requested(t), t
    assert call_when("menga qo'ng'iroq qil", tz, now) == ("now", "")
    assert call_when("hozir qo'ng'iroq qil", tz, now) == ("now", "")
    assert call_when("5 daqiqadan keyin menga qo'ng'iroq qil", tz, now) == ("at", "300 s")
    assert call_when("o'n daqiqadan keyin tel qil", tz, now) == ("at", "600 s")
    assert call_when("1 soatdan keyin qo'ng'iroq qil", tz, now) == ("at", "60 daq")
    assert call_when("yarim soatdan keyin qo'ng'iroq qil", tz, now) == ("at", "30 daq")
    assert call_when("soat 18:30 da qo'ng'iroq qil", tz, now) == ("at", "18:30")
    assert call_when("soat 9 da qo'ng'iroq qil", tz, now) == ("at", "09:00")
    assert call_when("ertaga soat 7 da tel qil", tz, now) == ("at", "2026-10-10 07:00")
    assert call_when("ertaga kechqurun 8 da qo'ng'iroq qil", tz, now) == ("at", "2026-10-10 20:00")
    assert call_when("12-oktabr soat 14:00 da qo'ng'iroq qil", tz, now) == ("at", "2026-10-12 14:00")
    assert call_when("20-oktabr qo'ng'iroq qil", tz, now) == ("at", "2026-10-20 09:00")
    assert call_when("dushanba kuni qo'ng'iroq qil", tz, now)[0] == "ask"      # tushunarsiz vaqt: so'raydi, hozir qo'ng'iroq qilmaydi


async def test_call_me_works_even_if_model_misses_it(make_app):
    """Model oddiy javob qaytarsa ham: hozir -> darrov qo'ng'iroq, vaqt bilan -> eslatma (qo'ng'iroq bilan), tushunarsiz -> so'raydi."""
    calls = []
    base = scripted_company()

    def handler(system, user, model):
        if "front desk" in system or "in a CHAT" in system:
            return json.dumps({"mode": "chat", "reply": "Albatta!", "task": ""})
        return base(system, user, model)
    app, _ = await make_app(handler, OWNER_TELEGRAM_ID="1")

    async def fake_call(text, **kw):
        calls.append(text)
        return True
    app.orch.call_owner = fake_call
    app.orch.call_ready = lambda: True
    res = await app.orch.handle("manga qongiroq qil hozir", 5)
    assert len(calls) == 1 and res["kind"] == "chat"
    res = await app.orch.handle("10 daqiqadan keyin menga qo'ng'iroq qil", 5)
    assert len(calls) == 1 and "Eslatma qo'yildi" in res["reply"]       # hozir emas, vaqti kelganda
    rid = (await app.store.list_reminders())[0]["id"]
    assert await app.store.get_kv(f"rcall:{rid}") == "1"
    res = await app.orch.handle("dushanba kuni menga qo'ng'iroq qil", 5)
    assert len(calls) == 1 and "Qachon" in res["reply"]
    res = await app.orch.handle("qo'ng'iroq qila olasanmi?", 5)
    assert len(calls) == 1                                               # savol: qo'ng'iroq qilinmaydi


def test_call_intent_review_cases():
    """Mustaqil tekshiruvda topilgan holatlar: boshqaga qo'ng'iroq, shart, inkor, o'tgan zamon, «hozir emas», so'z bilan raqamlar."""
    from datetime import datetime, timezone
    from aicompany.reminders import call_requested, call_when
    for t in ("Onamga qo'ng'iroq qil", "Akamga tel qil", "mijozga qo'ng'iroq qilib narxni so'ra", "narx 500 ga tushsa qo'ng'iroq qil",
              "Telegram kanalni kuzat, yangi post chiqsa tel qil", "kecha qo'ng'iroq qilib ketdi", "agar tel qilsang xursand bo'laman",
              "menga qo'ng'iroq qilib bezovta qilma", "Ertaga mijozlarga qo'ng'iroq qilib chiqish kerak"):
        assert not call_requested(t), t
    now = datetime(2026, 10, 9, 5, 0, tzinfo=timezone.utc)   # Toshkent 10:00
    tz = "Asia/Tashkent"
    cases = {"hozir emas, ertaga qo'ng'iroq qil": ("ask", ""), "hozir bandman, keyinroq qo'ng'iroq qil": ("ask", ""),
             "2 soatda tel qil": ("at", "120 daq"), "o'n besh daqiqadan keyin": ("at", "900 s"),
             "yigirma besh daqiqadan keyin": ("at", "1500 s"), "bir yarim soatdan keyin": ("at", "90 daq"),
             "soat 3 da": ("at", "15:00"), "bugun soat 5 da": ("at", "2026-10-09 17:00"),
             "kechasi 12 da": ("at", "2026-10-10 00:00"), "ertaga 7 da tel qilib uyg'ot": ("at", "2026-10-10 07:00")}
    for t, want in cases.items():
        assert call_when(t, tz, now) == want, t


async def test_task_decision_not_replaced_by_call_without_self(make_app):
    import json as _json
    base = scripted_company()

    def handler(system, user, model):
        if "front desk" in system:
            return _json.dumps({"mode": "task", "reply": "Boshladim", "task": "mijozlarga qo'ng'iroq rejasini tuz"})
        return base(system, user, model)
    app, _ = await make_app(handler, OWNER_TELEGRAM_ID="1")
    d = app.orch._ensure_call("mijozlarga qo'ng'iroq qilib chiqish rejasini tuz", {"mode": "task", "task": "x", "reply": ""})
    assert d["mode"] == "task" and not d.get("call")


async def test_latest_message_is_tied_to_the_last_exchange_not_old_topics(make_app):
    h = desk([{"mode": "chat", "reply": "ok"}])
    app, _ = await make_app(h)
    cid = 7
    latest = "Aynan web platforma ideyasi kerak"
    for role, t in [("owner", "Moliyaviy startap g'oyalari ro'yxati kerak"), ("ceo", "Tayyor, fayllarda"),
                    ("owner", "Zapchast savdosini avtomatlashtirish kerak"), ("ceo", "Qaysi tizim ishlatiladi?"),
                    ("owner", "1C"), ("ceo", "Tushundim"), ("owner", latest)]:
        await app.store.add_chat(cid, role, t)
    await app.orch._front_desk(latest, cid)
    user, system = h.seen[-1][0], h.systems[-1]
    assert user.index("# Earlier in this chat") < user.index("# Immediately preceding messages") < user.index("# Latest owner message")
    head, near = user.split("# Immediately preceding messages")
    assert "Zapchast" in near and "Moliyaviy" not in near          # eski mavzu «oxirgi xabarlar»da emas
    assert "Moliyaviy" in head
    assert "TOPIC RULE" in system
