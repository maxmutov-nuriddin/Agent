from __future__ import annotations

import asyncio
import re
import json
import shutil
from pathlib import Path
from typing import Awaitable, Callable

from .approvals import Approver, DenyApprover
from .config import TIERS
from .db import Store, now
from .reminders import call_requested
from .router import BudgetExhausted, TaskBudgetExceeded
from .team import Team
from .tools import ToolEnv
from .util import clip, extract_json

Notify = Callable[[str], Awaitable[None]]
MAX_STEPS = 8
REVIEW_EVERY = 10
RESULT_FILE, PROMPT_FILE = "NATIJA.md", "PROMPT.md"  # har tugagan vazifada: bitta tayyor natija + qayta bajarish prompti
MAIN_FILES = (RESULT_FILE, PROMPT_FILE)


class Paused(Exception):
    pass


class Stopped(Exception):
    """Egasi vazifani to'xtatdi."""


async def _safe_done(cb, res):
    try:
        await cb(res)
    except Exception:  # noqa: BLE001 — ixtiyoriy xabar yetkazish asosiy ishni buzmasin
        pass


PREFS_MAX = 15     # shundan ko'p doimiy qoida bo'lsa, takrorlari birlashtiriladi
PREFS_SHOWN = 20   # suhbat va rejaga hammasi sig'adi (birlashtirish 15 dan oshirmaydi)
STEP_CTX = 3000    # keyingi qadamga beriladigan oldingi natija hajmi; to'liqi .steps/ faylida
TASK_WORDS = re.compile(r"vazifa|natija|hisobot|task|#\d|задач|результат|отчет|отчёт|nima bo'?ldi|qani|tugadimi|xato|muammo|davom|buni|uni |shuni|o'zgartir|qisqartir|kengaytir|tuzat|qo'sh|yaxshila|qayta|yana|fayl|prompt|исправ|измени|сократи", re.I)


class Progress(str):
    """Jarayon xabari (reja, qadam tugadi...). Telegram «Faqat natija»/«O'chiq» rejimlarida ko'rsatilmaydi, panelda esa har doim."""


async def _noop(_: str):
    return None


def _safe(notify: Notify) -> Notify:
    """Xabar yetkazilmasa (Telegram/tarmoq xatosi) vazifa yiqilmasligi kerak."""
    async def wrapped(text: str):
        try:
            await notify(text)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass
    return wrapped


def normalize_plan(plan: dict) -> dict:
    """Model rejasi kutilmagan shaklda bo'lishi mumkin: qadamlarni tozalab, bir xil ko'rinishga keltiradi."""
    steps, seen = [], set()
    for i, s in enumerate(plan.get("steps") or []):
        if not isinstance(s, dict) or not s.get("agent") or not s.get("task"):
            continue
        sid = str(s.get("id") or f"s{i + 1}")
        while sid in seen:
            sid += "_"
        seen.add(sid)
        deps = s.get("depends_on") or []
        deps = [deps] if isinstance(deps, (str, int)) else [d for d in deps if isinstance(d, (str, int))]
        steps.append({"id": sid, "agent": str(s["agent"]).strip(), "task": str(s["task"]),
                      "tier": s.get("tier") if s.get("tier") in TIERS else None,
                      "depends_on": [str(d) for d in deps if str(d) != sid]})
    roles = [r for r in (plan.get("new_roles") or []) if isinstance(r, dict) and r.get("name")]
    acc = plan.get("acceptance") or []
    acc = [str(a).strip()[:200] for a in (acc if isinstance(acc, list) else [acc]) if str(a).strip()][:6]
    return {"summary": str(plan.get("summary") or ""), "steps": steps, "new_roles": roles,
            "complexity": "simple" if plan.get("complexity") == "simple" else "normal",
            "direct_answer": str(plan.get("direct_answer") or "").strip()[:8000], "acceptance": acc}


class Orchestrator:
    def __init__(self, store: Store, team: Team, settings, max_revisions: int = 1,
                 approver: Approver | None = None, tg=None):
        self.store, self.team, self.settings, self.max_revisions = store, team, settings, max_revisions
        self.approver = approver or DenyApprover()
        self.tg = tg
        self.sem = asyncio.Semaphore(getattr(settings, "max_parallel", 2))
        self.running: dict[int, asyncio.Task] = {}
        self._stopping: set[int] = set()
        self.on_done = None
        self._summarizing: set[int] = set()
        self._background: set[asyncio.Task] = set()
        self.call_ready = lambda: False   # app.py ulaydi: qo'ng'iroq moduli tayyormi
        self.call_error = lambda: ""   # app.py ulaydi: oxirgi qo'ng'iroq xatosi (sababi foydalanuvchiga ko'rsatiladi)
        self.call_owner = None   # app.py ulaydi: egasiga qo'ng'iroq qilish (ovozli modul bo'lsa)

    async def _check_pause(self):
        if await self.store.get_kv("paused") == "1":
            raise Paused()

    def _workspace(self, task_id: int) -> Path:
        ws = self.settings.workspace_dir / f"task_{task_id}"
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    @staticmethod
    def _files(ws: Path) -> list[str]:
        """Asosiy natija va tayyor prompt birinchi, qolganlari alifbo tartibida."""
        files = sorted(str(f.relative_to(ws)) for f in ws.rglob("*")
                       if f.is_file() and not any(part.startswith(".") for part in f.relative_to(ws).parts))
        return [f for f in MAIN_FILES if f in files] + [f for f in files if f not in MAIN_FILES]

    FRONT_DESK = (
        "You are the front desk of an AI company that works for its owner. Decide how to handle the owner's "
        "latest message. Return ONLY JSON: {\"mode\": \"chat\" or \"task\", \"reply\": \"...\", \"task\": \"...\"}.\n"
        "- chat: greetings, thanks, small talk, questions about the company, team, budget or earlier results, "
        "opinions and advice, or when ONE short clarifying question is needed because missing information would "
        "waste the work. Put your answer in \"reply\" (short, warm, in the owner's language).\n"
        "- task: the owner wants real work done (create, write, research, build, analyze, plan...) and the request "
        "is clear enough. Put a one-line acknowledgement in \"reply\" and a COMPLETE self-contained description of "
        "the work in \"task\", merging details from earlier messages (including answers to your questions).\n"
        "If a sensible default exists, do the task instead of asking. Never claim work is done in chat mode. "
        "If the owner wants to change, fix or continue the result of an earlier task (lines like '[Vazifa #N ...]'), "
        "add \"based_on\": N so the team starts from that task's files. "
        "If the owner asks to be reminded of something at a time, use mode \"chat\" and add "
        "\"reminder\": {\"when\": \"YYYY-MM-DD HH:MM\" (owner's local time, see '# Now') or a delay like \"30 daq\", \"text\": \"...\"}. "
        "Reply in the language the owner uses (Uzbek, Russian or English).")


    EXTRA_FIELDS = (
        "\n\nOPTIONAL FIELDS (add to the JSON only when they apply):\n"
        "- \"remember\": a short imperative sentence in the owner's language when the owner states a STANDING preference, rule or "
        "correction about how you or the team should behave or work (e.g. 'don't bring up old problems unless I ask', "
        "'this was done wrong, next time do X', 'always answer briefly'). It is saved and applied to all future chats and tasks. "
        "Briefly confirm it in the reply.\n"
        "- \"call\": text to SPEAK when the owner asks you to phone/call them right now (e.g. 'menga qo'ng'iroq qil, natijani ayt'). "
        "You can only call the owner, nobody else; if asked to call someone else, say you cannot.\n"
        "- inside \"reminder\": \"call\": true when the owner wants the reminder delivered as a phone call ('tel qilib eslat', "
        "'ertaga 7 da telefon qilib uyg'ot'). A phone call at a LATER time is always a reminder with call:true, never top-level call.\n"
        "- \"plan\": {\"period\": \"day\"|\"week\"|\"month\"|\"year\"|\"other\", \"title\": \"...\", \"items\": [\"...\", ...]} when the owner dictates "
        "THEIR OWN daily/weekly/monthly plan or to-do list ('bugungi rejam: ...', 'haftalik reja tuz'). It is saved to their Plans page. "
        "This is not for work you should do: that is a task.\n"
        "- \"watch\": {\"kind\": \"tg\"|\"price\", \"target\": \"@channel, t.me link or product URL\", \"keywords\": [\"...\"], "
        "\"description\": \"what exactly to look for\", \"target_price\": number or null} when the owner asks to MONITOR a Telegram "
        "channel for posts or to track a product's price. Confirm it briefly in the reply.")

    TALK_STYLE = (
        "\n\nIDENTITY: you are the owner's manager (menejer). When asked who you are or to introduce yourself, say it in ONE short "
        "sentence, e.g. 'Men sizning menejeringizman.', nothing more: do not say 'AI', 'assistant', 'language model', do not list "
        "your abilities or the team. Give details about yourself only if the owner asks for them. If the owner sincerely asks "
        "whether you are an AI or a robot, answer honestly and briefly."
        "\n\nCONVERSATION STYLE: the owner may just want to talk (feelings, ideas, life, plans). Then be a warm, "
        "attentive companion: answer what they actually said, in a natural human tone, a few sentences, no lists or "
        "reports. Do NOT bring up earlier tasks, failures, budgets, restarts or problems unless the owner asks about them "
        "or it is directly relevant to what they just said. Follow the owner's standing wishes found in the recent "
        "conversation (for example 'don't mention that now, tell me only when I ask') and honour them; when asked about "
        "it later, answer honestly. Never invent events.")

    CHAT_ONLY = (
        "You are the CEO of an AI company, talking with its owner in a CHAT. This chat is for conversation only: "
        "greetings, questions about the company, team, budget and earlier results, advice, opinions, brainstorming, "
        "clarifying questions. NEVER start or claim to do work from here. Return ONLY JSON: "
        "{\"reply\": \"...\", \"proposed_task\": \"...\"}. Put your conversational answer in \"reply\" (short, warm, "
        "in the owner's language). If, and only if, the owner clearly asks for real work to be done (create, write, "
        "research, build, analyze...), set \"proposed_task\" to a COMPLETE self-contained description (merging earlier "
        "messages) and make the reply a short offer such as 'Buni vazifa qilib topshiraymi?'; the app shows a "
        "'Submit as task' button. Otherwise proposed_task is an empty string. If the work changes or continues an earlier "
        "task's result (lines like '[Vazifa #N ...]'), also add \"based_on\": N. Reminders are allowed from chat: if the owner asks "
        "to be reminded at a time, add \"reminder\": {\"when\": \"YYYY-MM-DD HH:MM\" (local time, see '# Now') or a delay like "
        "\"30 daq\", \"text\": \"...\"}.")

    def _ensure_call(self, text: str, decision: dict) -> dict:
        from .calls import GREETING
        """"Menga qo'ng'iroq qil" (hozir / bir necha daqiqadan keyin / aniq vaqtda) model xato qilsa ham bajariladi."""
        from . import reminders
        if decision.get("reminder") or decision.get("call") or not reminders.call_requested(text):
            return decision
        if decision.get("mode") == "task" and not reminders.explicit_self(text):
            return decision   # model buni ish deb tushungan va "menga" deyilmagan: vazifani almashtirmaymiz
        kind, when = reminders.call_when(text, self.settings.report_tz)
        base = {"mode": "chat", "task": "", "based_on": None}
        if kind == "now":
            return {**base, "reply": decision.get("reply") or "📞 Hozir qo'ng'iroq qilyapman.", "call": GREETING}
        if kind == "at":
            return {**base, "reply": "", "reminder": {"when": when, "text": "Siz qo'ng'iroq qilishimni so'ragan edingiz.", "call": True}}
        return {**base, "reply": "Qachon qo'ng'iroq qilay? Vaqtni aniqroq ayting (masalan: 15 daqiqadan keyin yoki ertaga 9:00)."}

    async def handle(self, text: str, chat_id: int = 0, notify: Notify = _noop,
                     attachments: list[Path] | None = None, allow_tasks: bool = True, spoken: bool = False,
                     voice: bool = False) -> dict:
        """Oddiy xabarni qabul qiladi. allow_tasks=True: suhbat yoki vazifa ekanini o'zi aniqlaydi (Telegram).
        allow_tasks=False: faqat suhbat; ish so'ralsa vazifa taklif qiladi (veb-chat)."""
        await self.store.add_chat(chat_id, "owner", text)
        self._bg(self._maybe_summarize(chat_id))   # fonda: javobni kechiktirmaydi
        decision = {"mode": "task", "task": text, "reply": ""}
        if not attachments:  # fayl yuborilgan bo'lsa, bu aniq vazifa
            decision = await self._front_desk(text, chat_id, allow_tasks, spoken, voice)
            if not spoken:
                decision = self._ensure_call(text, decision)
        if decision.get("reminder"):
            from . import reminders
            try:
                wants_call = bool(decision["reminder"].get("call"))
                # qo'ng'iroq istagi doim saqlanadi: modul hozir tayyor bo'lmasa ham, eslatma vaqtida yana urinib ko'riladi
                r = await reminders.create(self.store, self.settings, chat_id, decision["reminder"]["text"], decision["reminder"]["when"],
                                           call=wants_call)
                decision["reply"] = (f"⏰ Eslatma yangilandi: {r['local']} — {r['text']}" if r.get("merged") else f"⏰ Eslatma qo'yildi: {r['local']} — {r['text']}")
                if r.get("call") and self.call_ready():
                    decision["reply"] += " (vaqti kelganda Telegram orqali sizga qo'ng'iroq qilaman)"
                elif r.get("call"):
                    decision["reply"] += (" (vaqti kelganda qo'ng'iroq qilishga urinaman, lekin qo'ng'iroq moduli hozir tayyor emas: "
                                          "Hisob → Telegram akkaunt → Ovozli qo'ng'iroq. Baribir matn ham keladi)")
            except ValueError as e:
                decision["reply"] = f"Eslatmani qo'ya olmadim: {e}. Vaqtni aniqroq yozing (masalan: ertaga 9:00)."
        if decision.get("call"):
            ok = self.call_ready() and await self.call_owner(decision["call"])
            why = self.call_error() if self.call_ready() else ""
            decision["reply"] = (decision["reply"] or "Qo'ng'iroq qilyapman.") if ok else \
                ("Qo'ng'iroq qila olmadim: " + (why.removeprefix("qo'ng'iroq qilib bo'lmadi: ") if why else
                 "qo'ng'iroq moduli yoqilmagan yoki akkaunt ulanmagan (Hisob → Telegram akkaunt → Ovozli qo'ng'iroq)."))
        if decision["mode"] == "chat":
            await self.store.add_chat(chat_id, "ceo", decision["reply"])
            await notify(decision["reply"])
            if decision.get("task"):
                await self.store.add_chat(chat_id, "proposal", json.dumps(
                    {"task": decision["task"], "based_on": decision.get("based_on")}, ensure_ascii=False))
            return {"kind": "chat", "reply": decision["reply"], "proposed_task": decision.get("task") or None}
        if decision["reply"]:
            await self.store.add_chat(chat_id, "ceo", decision["reply"])
            await notify(decision["reply"])
        return await self._run_and_log(decision["task"], chat_id, notify, attachments, decision.get("based_on"))

    async def submit_task(self, text: str, chat_id: int = 0, notify: Notify = _noop,
                          attachments: list[Path] | None = None, based_on: int | None = None) -> dict:
        """Aniq vazifa (suhbatsiz): «Vazifa berish» tugmasi. based_on: shu vazifa natijasi ustida davom etish."""
        await self.store.add_chat(chat_id, "owner", "📌 " + (f"(#{based_on} ustida) " if based_on else "") + text)
        return await self._run_and_log(text, chat_id, notify, attachments, based_on)

    async def _run_and_log(self, task_text, chat_id, notify, attachments, based_on=None) -> dict:
        res = await self.run_task(task_text, chat_id, notify, attachments, based_on)
        summary = f"[Vazifa #{res['task_id']} {res['status']}] " + (res.get("result") or res.get("error") or "")
        await self.store.add_chat(chat_id, "ceo", summary[:1500])
        res["kind"] = "task"
        if getattr(self, "on_done", None):
            self._bg(_safe_done(self.on_done, res))  # masalan: tugaganda qo'ng'iroq qilish
        return res

    async def resume_stopped(self, notify: Notify, statuses=("interrupted",), *, max_age_h: float = 6, limit: int = 3, delay: float = 15,
                             on_result=None) -> int:
        """Uzilgan (server qayta ishga tushgan) yoki pauzada qolgan vazifalarni bir martadan qayta boshlaydi:
        avvalgi ish fayllari va jamoa natijalari yangi vazifaga beriladi, bajarilgan qism qaytadan qilinmaydi."""
        from datetime import datetime, timedelta, timezone
        if delay:
            await asyncio.sleep(delay)
        since = (datetime.now(timezone.utc) - timedelta(hours=max_age_h)).isoformat()
        started = 0
        for t in await self.store.stopped_since(statuses, since):
            if started >= limit:
                break
            if await self.store.get_kv(f"resumed:{t['id']}"):
                continue
            if t["based_on"] and await self.store.get_kv(f"resumed:{t['based_on']}"):
                continue  # bu allaqachon avtomatik qayta boshlangan vazifaning nusxasi: cheksiz takrorlanmasin
            await self.store.set_kv(f"resumed:{t['id']}", "1")
            await self.store.update_task(t["id"], note="Avtomatik davom ettirildi (yangi vazifa sifatida).")
            started += 1
            try:
                await _safe(notify)(Progress(f"♻️ #{t['id']} vazifa avtomatik davom ettirilmoqda."))
                res = await self.submit_task(t["request"], t["chat_id"] or 0, notify, None, based_on=t["id"])
                if on_result:
                    await on_result(res)  # natija (matn va fayllar) Telegramga ham yetkaziladi
                else:
                    await _safe(notify)(f"🏁 #{res.get('task_id')} (#{t['id']} davomi) — {res.get('status')}")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                await self.store.audit("orchestrator", "resume_error", f"#{t['id']}: {e!r}"[:300])
        return started

    async def resume_interrupted(self, notify: Notify, **kw) -> int:
        return await self.resume_stopped(notify, ("interrupted",), **kw)

    def stop_task(self, task_id: int) -> bool:
        t = self.running.get(task_id)
        if not t or t.done():
            return False
        self._stopping.add(task_id)
        t.cancel()
        return True

    VOICE_MSG = ("\n\nYOUR REPLY WILL ALSO BE SENT AS A VOICE MESSAGE: write it the way a person would say it out loud, natural "
                 "and conversational, 1-4 short sentences in the owner's language. No lists, markdown, emojis or links.")

    SPOKEN = ("\n\nYOUR REPLY WILL BE SPOKEN ALOUD IN A PHONE CALL: talk like a real person, not a robot. 1-3 short, natural "
              "sentences in everyday conversational language (the owner's language), warm and relaxed. No lists, markdown, "
              "emojis, headings or reading out links. Answer in the owner's usual language (Uzbek unless they speak a whole "
              "sentence in another language; a single word like 'allo' does not count).")

    async def _front_desk(self, text: str, chat_id: int, allow_tasks: bool = True, spoken: bool = False,
                          voice: bool = False) -> dict:
        if allow_tasks and await self.store.get_kv("talk_only") == "1":  # «Faqat suhbat» rejimi: ish boshlamaydi, /task bilan beriladi
            allow_tasks = False
        all_hist = [h for h in await self.store.recent_chat(chat_id, 60) if h["role"] in ("owner", "ceo")]
        history = all_hist[-(self.CHAT_WINDOW + 1):-1]
        if not TASK_WORDS.search(text):  # oddiy gapda eski vazifa xulosalari (xato, qayta ishga tushish) suhbatni bosib ketmasin
            history = [h for h in history if not (h["role"] == "ceo" and (h["text"] or "").startswith("[Vazifa #"))]
        # oxirgisi hozirgi xabarning o'zi
        hist = "\n".join(f"{'Owner' if h['role'] == 'owner' else 'CEO'}: {clip(h['text'], 600)}" for h in history)
        mems = await self._recall(text, 5, fast=spoken)   # qo'ng'iroqda embedding'siz (kalit so'z): tezroq
        memo = "\n".join(f"- {m['text']}" for m in mems)
        prefs = "\n".join(f"- {m['text']}" for m in await self.store.owner_prefs(PREFS_SHOWN))
        summary = await self._chat_summary(chat_id)
        window_start = history[0]["id"] if history else (all_hist[-1]["id"] if all_hist else 0)
        older = await self.store.search_chat(chat_id, text, window_start)
        recall = "\n".join(f"[{(h['created_at'] or '')[:10]}] {'Owner' if h['role'] == 'owner' else 'CEO'}: {clip(h['text'], 300)}" for h in older)
        roster = await self.team.roster()
        from .reminders import local_text
        pend = await self.store.list_reminders(limit=15)
        rems = "\n".join(f"- {local_text(r['due_at'], self.settings.report_tz)}: {clip(r['text'], 120)}" for r in pend)
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo
        now_local = _dt.now(ZoneInfo(self.settings.report_tz)).strftime("%Y-%m-%d %H:%M (%A)")
        prompt = (f"# Now\n{now_local}, {self.settings.report_tz}\n\n# Team\n{roster}\n\n" + (f"# Memory\n{memo}\n\n" if memo else "") +
                  (f"# Owner's standing preferences (ALWAYS follow)\n{prefs}\n\n" if prefs else "") +
                  (f"# Owner's pending reminders (local time, complete list)\n{rems}\n\n" if rems else "# Owner's pending reminders\n(none)\n\n") +
                  (f"# Summary of the earlier conversation (background)\n{summary}\n\n" if summary else "") +
                  (f"# Possibly relevant older messages\n{recall}\n\n" if recall else "") +
                  (f"# Recent conversation\n{hist}\n\n" if hist else "") + f"# Latest owner message\n{text}")
        res = await self.team.router.call("cheap", (self.FRONT_DESK if allow_tasks else self.CHAT_ONLY) + self.EXTRA_FIELDS + self.TALK_STYLE + (self.SPOKEN if spoken else self.VOICE_MSG if voice else ""),
                                          [{"role": "user", "content": prompt}], agent="ceo-chat")
        try:
            d = extract_json(res.text)
            reply = str(d.get("reply", "")).strip()
            based_on = await self._valid_task_id(d.get("based_on"))
            await self._save_pref(d.get("remember"))
            watch = await self._save_watch(d.get("watch"))
            if watch and not reply:
                reply = f"🔔 Kuzatuv qo'shildi: {watch}. Rejalar → Kuzatuv bo'limida ko'rasiz."
            plan = await self._save_plan(d.get("plan"))
            if plan and not reply:
                reply = f"📋 Reja saqlandi: {plan['title']} ({len(plan['items'])} band). Rejalar sahifasida ko'rasiz."
            say = str(d.get("call") or "").strip() if isinstance(d.get("call"), (str, bool)) and d.get("call") else ""
            rem = d.get("reminder")
            if isinstance(rem, dict) and rem.get("when") and rem.get("text"):
                # vaqtli so'rov: qo'ng'iroq HOZIR emas, eslatma vaqtida (model ikkalasini qaytarsa ham)
                wants_call = bool(rem.get("call")) or bool(say) or call_requested(text)
                return {"mode": "chat", "reply": reply, "task": "", "reminder": {"when": str(rem["when"]), "text": str(rem["text"]), "call": wants_call}}
            if say and say.lower() not in ("true", "1"):
                return {"mode": "chat", "reply": reply, "task": "", "call": say}
            if allow_tasks and d.get("mode") == "task":
                return {"mode": "task", "reply": reply, "task": str(d.get("task", "")).strip() or text, "based_on": based_on}
            if not allow_tasks:  # faqat suhbat: ish so'ralgan bo'lsa taklif sifatida qaytaramiz
                proposal = str(d.get("proposed_task") or (d.get("task") if d.get("mode") == "task" else "") or "").strip()
                return {"mode": "chat", "reply": reply or ("Buni vazifa qilib topshiraymi?" if proposal else "Tushundim."),
                        "task": proposal, "based_on": based_on if proposal else None}
            if reply:
                return {"mode": "chat", "reply": reply, "task": ""}
        except (ValueError, TypeError):
            pass
        return {"mode": "chat", "reply": res.text.strip() or "Tushunmadim, qaytadan yozing.", "task": ""}

    CHAT_WINDOW = 16          # oxirgi nechta xabar to'liq ko'rsatiladi
    SUMMARY_EVERY = 10        # shuncha eski xabar to'planganda xulosa yangilanadi

    async def _chat_summary(self, chat_id) -> str:
        try:
            return str(json.loads(await self.store.get_kv(f"chat_sum:{chat_id}") or "{}").get("text", ""))
        except ValueError:
            return ""

    async def _maybe_summarize(self, chat_id):
        """Oynadan chiqib ketgan eski xabarlar xulosaga qo'shiladi (arzon modelda, kamdan-kam)."""
        if chat_id in self._summarizing:
            return
        self._summarizing.add(chat_id)
        try:
            rows = [h for h in await self.store.recent_chat(chat_id, 400) if h["role"] in ("owner", "ceo")]
            if len(rows) <= self.CHAT_WINDOW + self.SUMMARY_EVERY:
                return
            edge = rows[-(self.CHAT_WINDOW + 1)]["id"]          # shundan eskilari oynadan chiqib ketgan
            try:
                state = json.loads(await self.store.get_kv(f"chat_sum:{chat_id}") or "{}")
            except ValueError:
                state = {}
            new = await self.store.chat_between(chat_id, int(state.get("upto", 0)), edge)
            if len(new) < self.SUMMARY_EVERY:
                return
            convo = "\n".join(f"{'Owner' if h['role'] == 'owner' else 'CEO'}: {clip(h['text'], 400)}" for h in new)
            res = await self.team.router.call("cheap", (
                "You maintain a running summary of a long chat between a business owner and the CEO of their AI company. "
                "Merge the new messages into the summary. Keep: decisions, facts about the owner, plans, preferences, open "
                "questions, names, numbers, dates. Drop greetings and small talk. Max 180 words, plain text, in the owner's language."),
                [{"role": "user", "content": f"# Current summary\n{state.get('text') or '(none)'}\n\n# New messages\n{convo}"}],
                agent="ceo-summary")
            text = res.text.strip()
            if text:
                await self.store.set_kv(f"chat_sum:{chat_id}", json.dumps({"upto": new[-1]["id"], "text": text[:1500]}, ensure_ascii=False))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — xulosa bo'lmasa ham suhbat ishlayveradi
            pass
        finally:
            self._summarizing.discard(chat_id)

    async def _recall(self, text: str, k: int = 5, fast: bool = False):
        """Xotiradan qidirish: ma'no bo'yicha (Gemini embedding) + kalit so'z. Vektori yo'q eski xotiralar shu yerda to'ldiriladi."""
        qvec = None
        router = self.team.router
        if not fast and any(hasattr(p, "embed") for p in router.providers.values()):
            try:
                missing = await self.store.memories_without_emb(32)
                if missing:
                    vecs = await router.embed([m["text"] for m in missing])
                    for m, v in zip(missing, vecs or []):
                        await self.store.set_memory_emb(m["id"], v)
                got = await router.embed([text[:2000]], query=True)
                qvec = got[0] if got else None
            except Exception:  # noqa: BLE001 — vektor bo'lmasa kalit so'z qidiruvi yetadi
                qvec = None
        return await self.store.search_memories(text, k, qvec)

    async def _save_watch(self, value):
        """Egasi so'ragan kuzatuv (Telegram kanal yoki narx) yaratiladi; kuzatuvning o'zi AI'siz ishlaydi."""
        if not isinstance(value, dict) or value.get("kind") not in ("tg", "price"):
            return None
        target = str(value.get("target") or "").strip()[:500]
        if not target or (value["kind"] == "price" and not target.startswith(("http://", "https://"))):
            return None
        kws = value.get("keywords") or []
        kws = ", ".join(str(k).strip() for k in (kws if isinstance(kws, list) else [kws]) if str(k).strip())[:500]
        try:
            tp = float(value["target_price"]) if value.get("target_price") not in (None, "") else None
        except (TypeError, ValueError):
            tp = None
        title = (target if value["kind"] == "tg" else "Narx: " + target.split("/")[2])[:60]
        await self.store.add_watch(kind=value["kind"], title=title, target=target, keywords=kws,
                                   description=" ".join(str(value.get("description") or "").split())[:500], target_price=tp)
        return title

    async def _save_plan(self, value):
        """Egasi aytgan o'z rejasi (kunlik/haftalik...) Rejalar sahifasiga yoziladi. AI vazifa rejalari bu yerga tushmaydi."""
        if not isinstance(value, dict):
            return None
        title = " ".join(str(value.get("title") or "").split())[:200]
        items = [{"text": " ".join(str(i).split())[:300], "done": False} for i in (value.get("items") or []) if str(i).strip()][:60]
        if not title:
            return None
        period = value.get("period") if value.get("period") in ("day", "week", "month", "year", "other") else "day"
        await self.store.add_plan(period, title, json.dumps(items, ensure_ascii=False))
        return {"title": title, "items": items}

    async def _save_pref(self, value):
        """Egasining doimiy qoidasi yoki tuzatishi: xotiraga yoziladi va keyingi barcha suhbat/vazifalarda hisobga olinadi."""
        text = " ".join(str(value or "").split())[:300]
        if len(text) < 6 or text.lower() in ("none", "null", "false"):
            return
        if any(m["text"].strip().lower() == text.lower() for m in await self.store.owner_prefs(100)):
            return
        await self.store.add_memory(text, source="owner-pref")
        if len(await self.store.owner_prefs(100)) > PREFS_MAX:
            self._bg(self._consolidate_prefs())

    def _bg(self, coro):
        """Fon vazifasi: havola saqlanadi (aks holda Python uni yarim yo'lda yo'qotishi mumkin)."""
        t = asyncio.create_task(coro)
        self._background.add(t)
        t.add_done_callback(self._background.discard)
        return t

    async def _consolidate_prefs(self):
        """Doimiy qoidalar ko'payib ketsa, takrorlari birlashtiriladi. Hech bir alohida qoida yo'qolmasligi kerak:
        eski ro'yxat zaxiraga yoziladi, natija shubhali bo'lsa (juda qisqargan) o'zgartirilmaydi."""
        prefs = await self.store.owner_prefs(100)          # yangisi birinchi
        if len(prefs) <= PREFS_MAX:
            return
        listing = "\n".join(f"{i + 1}. {p['text']}" for i, p in enumerate(prefs))
        try:
            res = await self.team.router.call("cheap", (
                "You maintain the owner's standing rules for an AI assistant. Merge duplicates and near-duplicates into one "
                "rule each. NEVER drop a distinct rule or detail. If two rules contradict, keep the NEWER one (item 1 is the "
                f"newest). Keep the owner's language. Return ONLY JSON: {{\"rules\": [\"...\"]}} with at most {PREFS_MAX} rules."),
                [{"role": "user", "content": listing}], agent="memory")
            rules = [" ".join(str(r).split())[:300] for r in extract_json(res.text).get("rules", []) if str(r).strip()]
        except Exception:  # noqa: BLE001 — birlashtirib bo'lmasa, qoidalar avvalgidek qoladi
            return
        if not rules or len(rules) < max(3, len(prefs) * 0.4) or len(rules) > len(prefs):
            await self.store.audit("memory", "prefs_merge_skipped", f"{len(prefs)} -> {len(rules)}")
            return
        await self.store.set_kv("prefs_backup", json.dumps({"at": now(), "rules": [p["text"] for p in prefs]}, ensure_ascii=False))
        await self.store.replace_owner_prefs(list(reversed(rules)))   # eng yangisi oxirida yoziladi: tartib saqlanadi
        await self.store.audit("memory", "prefs_merged", f"{len(prefs)} -> {len(rules)} (eski ro'yxat zaxirada: prefs_backup)")

    async def _valid_task_id(self, value) -> int | None:
        try:
            tid = int(str(value).lstrip("#"))
        except (TypeError, ValueError):
            return None
        return tid if await self.store.get_task(tid) else None

    async def run_task(self, request: str, chat_id: int = 0, notify: Notify = _noop,
                       attachments: list[Path] | None = None, based_on: int | None = None) -> dict:
        async with self.sem:
            return await self._run_task(request, chat_id, notify, attachments, based_on)

    async def _continue_from(self, based_on: int, ws: Path) -> str:
        """Avvalgi vazifa fayllarini yangi ish papkasiga ko'chiradi va jamoaga kontekst beradi."""
        prev = await self.store.get_task(based_on)
        if not prev:
            return ""
        src = self.settings.workspace_dir / f"task_{based_on}"
        if src.is_dir():
            shutil.copytree(src, ws, dirs_exist_ok=True)
        files = self._files(ws)
        if prev["status"] != "done":  # to'xtatilgan/uzilgan/pauzadagi vazifa: bajarilgan qismini saqlab, qolganini tugatamiz
            done = "\n\n".join(f"[{m['agent']}]\n{clip(m['content'] or '', 1500)}" for m in await self.store.task_messages(based_on)
                                if m["agent"] not in ("hr", "qa") and "===ANSWER===" not in (m["content"] or ""))  # qadoqlash xabari kerak emas
            return (f"\n\n[RESUME: task #{based_on} was stopped before it finished (status: {prev['status']}). Its files are already in the "
                    f"workspace: {', '.join(files) or '(none)'}. Work already done by the team is below: do NOT redo it; "
                    f"continue from where it stopped and deliver the complete final result.\nWork so far:\n{clip(done or prev['result'] or '(nothing saved)', 5000)}]")
        return (f"\n\n[This continues task #{based_on}: \"{clip(prev['request'], 300)}\". "
                f"Its files are already in the workspace: {', '.join(files) or '(none)'}. Modify or extend them as "
                f"requested instead of starting over.\nPrevious result:\n{clip(prev['result'] or '', 2500)}]")

    async def _run_task(self, request: str, chat_id: int, notify: Notify,
                        attachments: list[Path] | None, based_on: int | None = None) -> dict:
        raw_notify = _safe(notify)

        async def notify(text: str):
            await raw_notify(Progress(text))
        based_on = await self._valid_task_id(based_on) if based_on else None
        task_id = await self.store.create_task(chat_id, request, based_on)
        ws = self._workspace(task_id)
        resume = None
        if based_on:
            request += await self._continue_from(based_on, ws)
            prev = await self.store.get_task(based_on)
            if prev and prev["status"] != "done":  # aynan to'xtagan joydan: saqlangan reja va qadam natijalari
                try:
                    resume = json.loads(await self.store.get_kv(f"ckpt:{based_on}") or "null")
                except ValueError:
                    resume = None
        for src in attachments or []:
            shutil.copy(src, ws / Path(src).name)
        if attachments:
            request += "\n\n[Attached files in the workspace: " + ", ".join(Path(a).name for a in attachments) + "]"
        env = ToolEnv(workspace=ws, store=self.store, settings=self.settings, task_id=task_id,
                      approver=self.approver, notify=notify, tg=self.tg)
        await notify(f"📝 Vazifa #{task_id} qabul qilindi. Rahbar rejalashtiryapti...")
        out = {"task_id": task_id, "workspace": str(ws)}
        try:
            result = await self._run_guarded(task_id, request, notify, env, resume)
            await self.store.update_task(task_id, status="done", result=result, finished_at=now())
            await self.store.delete_kv(f"ckpt:{task_id}")   # tugadi: nazorat nuqtasi kerak emas
            out.update(status="done", result=result)
            await self._learn(task_id, request, result)
        except Stopped:
            out.update(status="cancelled", error="Siz vazifani to'xtatdingiz.")
        except Paused:
            out.update(status="paused", error="Vazifa /pause sababli to'xtatildi.")
        except TaskBudgetExceeded as e:
            out.update(status="limit", error=f"Vazifa limiti ({self.settings.max_task_usd}$) tugadi: {e}")
        except BudgetExhausted as e:
            out.update(status="failed", error=f"AI ishlamadi (limit, kalit yoki model nomi): {str(e)[:350]}")
        except Exception as e:  # noqa: BLE001 — vazifa jimgina yo'qolmasligi kerak
            await self.store.audit("orchestrator", "task_error", f"#{task_id}: {e!r}")
            out.update(status="failed", error=f"Xatolik: {str(e)[:350]}")
        if out["status"] != "done":
            out["result"] = "\n\n".join(f"[{m['agent']}]\n{m['content']}" for m in await self.store.task_messages(task_id)
                                        if m["agent"] not in ("hr", "qa"))
            await self.store.update_task(task_id, status=out["status"], result=out["result"] or None, finished_at=now(),
                                         note=(out.get("error") or "")[:400] or None)
        out["files"] = self._files(ws)
        if getattr(self.store, "remote", False):
            try:
                from .persist import save_workspace
                await save_workspace(self.store, ws, task_id)
            except Exception as e:  # noqa: BLE001 — saqlanmasa ham natija foydalanuvchiga yetadi
                await self.store.audit("orchestrator", "save_files_error", f"#{task_id}: {e!r}"[:300])
        try:
            await self._maybe_review(task_id, notify)
        except Exception:  # noqa: BLE001 — HR tahlili natijaga ta'sir qilmasin
            pass
        return out

    async def _run_guarded(self, task_id, request, notify, env, resume=None) -> str:
        """Ishni alohida vazifa sifatida yuritadi, shunda uni tashqaridan to'xtatish mumkin."""
        inner = asyncio.ensure_future(self._run(task_id, request, notify, env, resume))
        self.running[task_id] = inner
        try:
            return await inner
        except asyncio.CancelledError:
            if task_id in self._stopping:
                raise Stopped() from None
            raise  # dastur to'xtayotgan bo'lsa, bekor qilishni yashirmaymiz
        finally:
            self.running.pop(task_id, None)
            self._stopping.discard(task_id)

    async def _maybe_review(self, task_id: int, notify: Notify):
        if task_id % REVIEW_EVERY:
            return
        fired = await self.team.review(REVIEW_EVERY)
        if fired:
            await notify("🧑‍💼 HR tahlili: ishsiz qolgan xodimlar bo'shatildi: " + ", ".join(fired))

    async def _run(self, task_id: int, request: str, notify: Notify, env: ToolEnv, resume: dict | None = None) -> str:
        eco = await self.store.get_kv("eco") != "0"  # tejamkor rejim (standart: yoqilgan)
        outputs: dict[str, str] = {}
        if resume and resume.get("plan"):  # uzilgan vazifa: reja va bajarilgan qadamlar saqlangan, faqat qolgani bajariladi
            plan = resume["plan"]
            outputs = {k: v for k, v in (resume.get("outputs") or {}).items() if any(st["id"] == k for st in plan["steps"])}
            await notify(f"♻️ {len(outputs)}/{len(plan['steps'])} qadam avval bajarilgan, qolgani davom ettirilmoqda.")
        else:
            plan = await self._plan(task_id, request, env, eco)
        await self.store.update_task(task_id, plan=json.dumps(plan, ensure_ascii=False))
        simple = plan.get("complexity") == "simple" and len(plan["steps"]) == 1
        direct = str(plan.get("direct_answer") or "").strip()
        has_context = any(m in request for m in ("[Attached files", "[This continues task", "[RESUME"))
        if simple and direct and not resume and not has_context and len(direct) >= 20:  # oddiy savol: jamoa ishga tushmaydi (1 ta arzon so'rov)
            await notify("💡 Oddiy savol: rahbar o'zi javob berdi.")
            return self._package_simple(request, direct, env)

        for role in plan.get("new_roles", [])[:2]:
            await self._check_pause()
            name = await self.team.hire(role.get("name", "specialist"), role.get("why", ""), task_id=task_id)
            if name:
                await notify(f"🧑‍💼 HR yangi xodim oldi: {name}")

        steps = plan["steps"][:MAX_STEPS]
        await notify(f"📋 Reja: {str(plan.get('summary', ''))[:500]}\n" + "\n".join(
            f"• {s['agent']} [{s.get('tier') or 'auto'}]: {s['task'][:80]}" for s in steps))
        remaining = {s["id"]: s for s in steps if s["id"] not in outputs}
        steps_dir = env.workspace / ".steps"
        steps_dir.mkdir(exist_ok=True)
        while remaining:
            ready = [s for s in remaining.values() if not any(d in remaining for d in s.get("depends_on", []))]
            if not ready:  # sikl: qolganlarini ketma-ket bajaramiz
                ready = [next(iter(remaining.values()))]
            await self._check_pause()

            async def work(s):
                # oldingi qadam natijasi qisqa holda beriladi, to'liqi faylda (kerak bo'lsa agent o'qiydi): token tejaladi
                ctx = "\n\n".join(f"## {d}\n{clip(outputs[d], STEP_CTX)}" + (f"\n(To'liq matn: .steps/{d}.md fayli)" if len(outputs[d]) > STEP_CTX else "")
                                  for d in s.get("depends_on", []) if d in outputs)
                tier = s.get("tier")
                if eco and tier == "strong":
                    tier = "mid"  # eng qimmat daraja faqat «sifat» rejimida
                out = await self.team.run_agent(s["agent"], s["task"], ctx, task_id=task_id, tier=tier, env=env)
                await notify(f"✅ {s['agent']} tugatdi ({s['id']})")
                return s["id"], out

            for sid, out in await self._gather_or_cancel([work(s) for s in ready]):
                outputs[sid] = out
                remaining.pop(sid)
                (steps_dir / f"{sid}.md").write_text(out, encoding="utf-8")
            await self.store.set_kv(f"ckpt:{task_id}", json.dumps({"plan": plan, "outputs": outputs}, ensure_ascii=False))

        criteria = [str(c)[:200] for c in (plan.get("acceptance") or []) if str(c).strip()][:6]
        if (eco or simple) and len(outputs) == 1:  # bitta qadam: uni qayta yozishning keragi yo'q (pul va vaqt tejaladi)
            deliverable = next(iter(outputs.values()))
        else:
            deliverable = await self._synthesize(task_id, request, outputs, None, "mid", env, criteria=criteria)
        if simple and not self._files(env.workspace):  # oddiy, faylsiz ish: QA va qadoqlash so'rovlari kerak emas
            return self._package_simple(request, deliverable, env)
        deliverable = await self._qa_loop(task_id, request, deliverable, outputs, steps, env, criteria, eco, notify)
        return await self._package(task_id, request, deliverable, env, eco)

    QA_MAX = 6

    async def _qa_rounds(self) -> int:
        """Nechta tuzatish aylanishi: panel sozlamasi (kv qa_rounds), bo'lmasa MAX_REVISIONS (kamida 3)."""
        raw = await self.store.get_kv("qa_rounds")
        try:
            n = int(raw) if raw else max(self.max_revisions, 3)
        except ValueError:
            n = max(self.max_revisions, 3)
        return max(0, min(n, self.QA_MAX))

    @staticmethod
    def _fixer(steps) -> str:
        """Fayllardagi e'tirozlarni kim tuzatadi: ishni qilgan mutaxassis (dasturchi bo'lsa u), bo'lmasa generalist."""
        agents = [s["agent"] for s in steps if s.get("agent") not in ("ceo", "hr", "qa")]
        if "developer" in agents:
            return "developer"
        return agents[-1] if agents else "generalist"

    async def _qa_loop(self, task_id, request, deliverable, outputs, steps, env, criteria, eco, notify) -> str:
        """QA e'tirozi qolmaguncha tuzatadi: fayllarni ishni qilgan agent tuzatadi, javobni rahbar qayta yig'adi,
        QA esa avvalgi e'tirozlar haqiqatan tuzatilganini tekshiradi. To'xtaydi: QA o'tkazsa, limit tugasa yoki
        bir xil e'tirozlar takrorlansa (tuzatib bo'lmayapti: pul behuda ketmasin)."""
        rounds = await self._qa_rounds()
        prev_issues: list[str] = []
        seen: list[list[str]] = []
        for attempt in range(rounds + 1):
            await self._check_pause()
            verdict = await self._review(task_id, request, deliverable, env, criteria, prev_issues)
            issues = verdict.get("issues", [])
            if verdict.get("verdict") == "pass" or not issues:
                if attempt:
                    await notify(Progress(f"✅ QA tasdiqladi ({attempt} marta tuzatildi)."))
                return deliverable
            key = sorted(i.strip().lower() for i in issues)
            if attempt == rounds or key in seen:
                return deliverable + "\n\n⚠️ QA hali ham e'tiroz bildirgan:\n- " + "\n- ".join(issues)
            seen.append(key)
            await notify(Progress(f"🔎 QA {len(issues)} ta muammo topdi, tuzatilyapti ({attempt + 1}/{rounds})..."))
            if self._files(env.workspace):
                fixer = self._fixer(steps)
                fix = await self.team.run_agent(fixer, (
                    f"# Original request\n{request}\n\n# Quality reviewer found these problems\n- " + "\n- ".join(issues) +
                    "\n\nFix EVERY problem directly in the workspace files (read them first, then edit). Do not skip any. "
                    "At the end list briefly, per problem, what you changed."), task_id=task_id, tier="mid", env=env)
                outputs[f"qa_fix_{attempt + 1}"] = fix
            deliverable = await self._synthesize(task_id, request, outputs, issues, "mid" if eco else "strong", env,
                                                 previous=deliverable, criteria=criteria)
            prev_issues = issues
        return deliverable

    def _package_simple(self, request: str, answer: str, env) -> str:
        """LLM'siz qadoqlash (oddiy ishlar): NATIJA.md = javob, PROMPT.md = namunaviy prompt."""
        ws = env.workspace
        (ws / RESULT_FILE).write_text(answer, encoding="utf-8")
        (ws / PROMPT_FILE).write_text(f"# Vazifa\n{request}\n\n# Kutilgan natija (namuna)\n{clip(answer, 6000)}\n\n"
                                      "Yuqoridagi vazifani to'liq bajaring va natijani shu namunadagi tuzilishda bering.", encoding="utf-8")
        return answer

    async def _package(self, task_id, request, deliverable, env, eco: bool = False) -> str:
        """Yakuniy qadoqlash: NATIJA.md (to'liq tayyor natija), PROMPT.md (boshqa AI uchun to'liq prompt)
        va qaytariladigan qisqa, aniq javob (paneldagi «Natija»)."""
        ws = env.workspace
        (ws / RESULT_FILE).write_text(deliverable, encoding="utf-8")
        previews = []
        for rel in [f for f in self._files(ws) if f not in MAIN_FILES][:6]:
            try:
                previews.append(f"### {rel}\n{clip((ws / rel).read_text(encoding='utf-8'), 1500)}")
            except (UnicodeDecodeError, OSError):
                previews.append(f"### {rel}\n(binary)")
        prompt = (f"# Original request\n{request}\n\n# Final deliverable\n{clip(deliverable, 8000)}\n\n"
                  f"# Other files produced\n" + ("\n\n".join(previews) or "(none)") + "\n\n"
                  "Write two sections in the language of the request, in plain human language (no JSON):\n"
                  "===ANSWER===\nA short, direct answer to exactly what was asked (the conclusion, the numbers, the decision, "
                  "or what was built and how to use it). Use markdown headings/lists only if it helps. Max ~250 words. "
                  "Mention the main file names the owner should open.\n"
                  "===PROMPT===\nA complete, self-contained prompt that the owner can give to ANY other AI to get this same "
                  "result 100% correctly in one go: the goal, the context, every requirement and constraint, the decisions "
                  "made, the exact structure/sections/files to produce, the style and language, and acceptance criteria to "
                  "check the result. Do not refer to 'the team' or 'the files above'; include all needed details inline.")
        try:
            raw = await self.team.run_agent("ceo", prompt, task_id=task_id, tier="cheap" if eco else "mid", env=env)
        except (BudgetExhausted, TaskBudgetExceeded):
            raw = ""  # limit tugasa ham tayyor natija yo'qolmaydi
        if "===ANSWER===" not in raw and "===PROMPT===" not in raw:
            raw = ""  # format buzilgan: zaxira yo'li (to'liq natija + namunaviy prompt)
        answer, _, repro = raw.partition("===PROMPT===")
        answer = answer.replace("===ANSWER===", "").strip()
        repro = repro.strip() or (f"# Vazifa\n{request}\n\n# Kutilgan natija (namuna)\n{clip(deliverable, 6000)}\n\n"
                                  "Yuqoridagi vazifani to'liq bajaring va natijani shu namunadagi tuzilishda bering.")
        (ws / PROMPT_FILE).write_text(repro, encoding="utf-8")
        if not answer:
            return deliverable
        warn = deliverable[deliverable.find("\n\n⚠️ QA hali ham"):] if "⚠️ QA hali ham" in deliverable else ""
        return answer + warn

    @staticmethod
    async def _gather_or_cancel(coros):
        """Parallel qadamlar: bittasi yiqilsa (limit, xato, to'xtatish), qolganlari ham to'xtatiladi, pul sarflanmaydi."""
        tasks = [asyncio.ensure_future(c) for c in coros]
        try:
            return await asyncio.gather(*tasks)
        except BaseException:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _plan(self, task_id, request, env, eco: bool = False) -> dict:
        roster = await self.team.roster()
        mems = await self._recall(request, 5)
        memo = ("# Relevant memory about the owner/business\n" + "\n".join(f"- {m['text']}" for m in mems) + "\n\n") if mems else ""
        prefs = await self.store.owner_prefs(PREFS_SHOWN)
        if prefs:
            memo = "# Owner's standing preferences (ALWAYS follow)\n" + "\n".join(f"- {m['text']}" for m in prefs) + "\n\n" + memo
        prompt = (f"# Team\n{roster}\n\n{memo}# Request\n{request}\n\n"
                  "Plan the work. Use at most 6 steps; steps without dependencies run in parallel. "
                  "Specialists can write files, search the web and (with the owner's approval) run commands. "
                  "Pick a model tier per step: 'cheap' for lookups and simple drafting, 'mid' for substantive "
                  "creation, 'strong' ONLY for the hardest reasoning or architecture. Prefer the cheaper tier when unsure. "
                  "Only propose new_roles if no existing role fits (max 2). "
                  "Set complexity to 'simple' when ONE step by one specialist is enough and no files or research are needed. "
                  "If it is a simple question you can answer fully and correctly yourself from general knowledge (no files, "
                  "no current data, no web research, nothing to build), put the complete answer in direct_answer and give one step anyway. "
                  "List 2-6 concrete acceptance criteria the final result must meet (what the owner explicitly asked for). "
                  'Return ONLY JSON: {"summary": "...", "complexity": "simple|normal", "direct_answer": "", '
                  '"acceptance": ["..."], "steps": [{"id": "s1", "agent": "<team member name>", '
                  '"task": "self-contained instruction", "tier": "cheap|mid|strong", "depends_on": []}], '
                  '"new_roles": [{"name": "snake_case_name", "why": "..."}]}')
        last_err = None
        for attempt in range(2):
            # tejamkor rejimda reja arzon modelda; yaroqsiz chiqsa, ikkinchi urinish o'rta darajada
            raw = await self.team.run_agent("ceo", prompt, task_id=task_id, tier="cheap" if eco and attempt == 0 else None, env=env)
            try:
                plan = normalize_plan(extract_json(raw))
                if plan["steps"]:
                    return plan
                last_err = "steps bo'sh"
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                last_err = e
            prompt += "\n\nYour previous answer was not valid JSON in the required shape. Return ONLY the JSON."
        raise RuntimeError(f"Rahbar yaroqli reja tuza olmadi: {last_err}")

    async def _synthesize(self, task_id, request, outputs, issues, tier, env, previous=None, criteria=None) -> str:
        parts = "\n\n".join(f"## {k}\n{clip(v, 6000)}" for k, v in outputs.items())
        files = self._files(env.workspace)
        prompt = (f"# Original request\n{request}\n\n# Team outputs\n{parts}\n\n"
                  f"# Files in the workspace (delivered to the user automatically)\n{', '.join(files) or '(none)'}\n\n"
                  "Assemble ONE final, complete answer for the user. Merge the outputs, remove duplication, "
                  "keep all concrete content, and refer to the delivered files by name.")
        if criteria:
            prompt += "\n\n# The result MUST satisfy\n- " + "\n- ".join(criteria)
        if issues:
            prompt += ("\n\n# Previous draft\n" + clip(previous or "", 16000) +
                       "\n\n# Reviewer issues to fix (address EVERY one; keep everything that was already correct)\n- " + "\n- ".join(issues))
        return await self.team.run_agent("ceo", prompt, task_id=task_id, tier=tier, env=env)

    async def _review(self, task_id, request, deliverable, env, criteria=None, prev_issues=None) -> dict:
        files = self._files(env.workspace)
        checklist = ("# Acceptance criteria (check EACH one explicitly)\n- " + "\n- ".join(criteria) + "\n\n") if criteria else ""
        shown = clip(deliverable, 10000)
        if len(deliverable) > 10000:   # QA qisqartirilgan matnni "chala" deb e'tiroz bildirmasin: to'liq matn faylda
            (env.workspace / ".steps").mkdir(exist_ok=True)
            (env.workspace / ".steps" / "_draft.md").write_text(deliverable, encoding="utf-8")
            shown += "\n\n(Shown text is shortened; the FULL deliverable is in the file .steps/_draft.md: read it before saying anything is missing.)"
        recheck = ("# Problems you reported last time\n- " + "\n- ".join(prev_issues) + "\n\nFirst verify whether EACH of these is now "
                   "fixed. Report as issues only those still not fixed plus genuinely serious new problems; do not add new minor "
                   "nitpicks.\n\n") if prev_issues else ""
        prompt = (f"# Original request\n{request}\n\n{checklist}{recheck}# Deliverable\n{shown}\n\n"
                  f"# Workspace files (you may read them)\n{', '.join(files) or '(none)'}\n\n"
                  "Does the deliverable fully and correctly satisfy the request" + (" and every acceptance criterion" if criteria else "") +
                  "? Report only real problems (missing parts, errors, ignored requirements, unmet criteria). Return ONLY JSON: "
                  '{"verdict": "pass|fail", "issues": ["..."]}')
        raw = await self.team.run_agent("qa", prompt, task_id=task_id, env=env)
        try:
            v = extract_json(raw)
            v["issues"] = [str(i) for i in v.get("issues", [])]
            return v
        except (ValueError, TypeError):
            return {"verdict": "pass", "issues": []}  # QA ishlamasa vazifani bloklamaymiz

    async def _learn(self, task_id, request, result):
        """Vazifadan so'ng 0-2 ta uzoq muddatli fakt saqlaydi (arzon model, kichik so'rov)."""
        try:
            res = await self.team.router.call(
                "cheap", "You extract durable facts worth remembering about the owner or their business.",
                [{"role": "user", "content":
                  f"Request:\n{clip(request, 1500)}\n\nResult summary:\n{clip(result, 1500)}\n\n"
                  'Return ONLY JSON {"facts": ["..."]} with at most 2 facts about the owner\'s preferences, '
                  "business details or decisions that will matter in FUTURE tasks. No task content. "
                  "Empty list if nothing durable."}],
                task_id=task_id, agent="memory")
            facts = extract_json(res.text).get("facts", [])
            for fact in (facts if isinstance(facts, list) else [])[:2]:
                if str(fact).strip():
                    await self.store.add_memory(str(fact), source=f"task#{task_id}")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — xotira ixtiyoriy: tayyor vazifa hech qachon shu sabab "xato" bo'lmasin
            return
