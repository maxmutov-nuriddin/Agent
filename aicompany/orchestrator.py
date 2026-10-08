from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Awaitable, Callable

from .approvals import Approver, DenyApprover
from .config import TIERS
from .db import Store, now
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
    return {"summary": str(plan.get("summary") or ""), "steps": steps, "new_roles": roles}


class Orchestrator:
    def __init__(self, store: Store, team: Team, settings, max_revisions: int = 1,
                 approver: Approver | None = None, tg=None):
        self.store, self.team, self.settings, self.max_revisions = store, team, settings, max_revisions
        self.approver = approver or DenyApprover()
        self.tg = tg
        self.sem = asyncio.Semaphore(getattr(settings, "max_parallel", 2))
        self.running: dict[int, asyncio.Task] = {}
        self._stopping: set[int] = set()

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
        files = sorted(str(f.relative_to(ws)) for f in ws.rglob("*") if f.is_file())
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

    async def handle(self, text: str, chat_id: int = 0, notify: Notify = _noop,
                     attachments: list[Path] | None = None, allow_tasks: bool = True) -> dict:
        """Oddiy xabarni qabul qiladi. allow_tasks=True: suhbat yoki vazifa ekanini o'zi aniqlaydi (Telegram).
        allow_tasks=False: faqat suhbat; ish so'ralsa vazifa taklif qiladi (veb-chat)."""
        await self.store.add_chat(chat_id, "owner", text)
        decision = {"mode": "task", "task": text, "reply": ""}
        if not attachments:  # fayl yuborilgan bo'lsa, bu aniq vazifa
            decision = await self._front_desk(text, chat_id, allow_tasks)
        if decision.get("reminder"):
            from . import reminders
            try:
                r = await reminders.create(self.store, self.settings, chat_id, decision["reminder"]["text"], decision["reminder"]["when"])
                decision["reply"] = f"⏰ Eslatma qo'yildi: {r['local']} — {r['text']}"
            except ValueError as e:
                decision["reply"] = f"Eslatmani qo'ya olmadim: {e}. Vaqtni aniqroq yozing (masalan: ertaga 9:00)."
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
        return res

    async def resume_stopped(self, notify: Notify, statuses=("interrupted",), *, max_age_h: float = 6, limit: int = 3, delay: float = 15) -> int:
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
                await _safe(notify)(f"♻️ #{t['id']} vazifa avtomatik davom ettirilmoqda.")
                res = await self.submit_task(t["request"], t["chat_id"] or 0, notify, None, based_on=t["id"])
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

    async def _front_desk(self, text: str, chat_id: int, allow_tasks: bool = True) -> dict:
        history = [h for h in await self.store.recent_chat(chat_id, 40) if h["role"] in ("owner", "ceo")][-9:-1]
        # oxirgisi hozirgi xabarning o'zi
        hist = "\n".join(f"{'Owner' if h['role'] == 'owner' else 'CEO'}: {clip(h['text'], 600)}" for h in history)
        mems = await self.store.search_memories(text, 5)
        memo = "\n".join(f"- {m['text']}" for m in mems)
        roster = await self.team.roster()
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo
        now_local = _dt.now(ZoneInfo(self.settings.report_tz)).strftime("%Y-%m-%d %H:%M (%A)")
        prompt = (f"# Now\n{now_local}, {self.settings.report_tz}\n\n# Team\n{roster}\n\n" + (f"# Memory\n{memo}\n\n" if memo else "") +
                  (f"# Recent conversation\n{hist}\n\n" if hist else "") + f"# Latest owner message\n{text}")
        res = await self.team.router.call("cheap", self.FRONT_DESK if allow_tasks else self.CHAT_ONLY,
                                          [{"role": "user", "content": prompt}], agent="ceo-chat")
        try:
            d = extract_json(res.text)
            reply = str(d.get("reply", "")).strip()
            based_on = await self._valid_task_id(d.get("based_on"))
            rem = d.get("reminder")
            if isinstance(rem, dict) and rem.get("when") and rem.get("text"):
                return {"mode": "chat", "reply": reply, "task": "", "reminder": {"when": str(rem["when"]), "text": str(rem["text"])}}
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
        notify = _safe(notify)
        based_on = await self._valid_task_id(based_on) if based_on else None
        task_id = await self.store.create_task(chat_id, request, based_on)
        ws = self._workspace(task_id)
        if based_on:
            request += await self._continue_from(based_on, ws)
        for src in attachments or []:
            shutil.copy(src, ws / Path(src).name)
        if attachments:
            request += "\n\n[Attached files in the workspace: " + ", ".join(Path(a).name for a in attachments) + "]"
        env = ToolEnv(workspace=ws, store=self.store, settings=self.settings, task_id=task_id,
                      approver=self.approver, notify=notify, tg=self.tg)
        await notify(f"📝 Vazifa #{task_id} qabul qilindi. Rahbar rejalashtiryapti...")
        out = {"task_id": task_id, "workspace": str(ws)}
        try:
            result = await self._run_guarded(task_id, request, notify, env)
            await self.store.update_task(task_id, status="done", result=result, finished_at=now())
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

    async def _run_guarded(self, task_id, request, notify, env) -> str:
        """Ishni alohida vazifa sifatida yuritadi, shunda uni tashqaridan to'xtatish mumkin."""
        inner = asyncio.ensure_future(self._run(task_id, request, notify, env))
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

    async def _run(self, task_id: int, request: str, notify: Notify, env: ToolEnv) -> str:
        plan = await self._plan(task_id, request, env)
        await self.store.update_task(task_id, plan=json.dumps(plan, ensure_ascii=False))

        for role in plan.get("new_roles", [])[:2]:
            await self._check_pause()
            name = await self.team.hire(role.get("name", "specialist"), role.get("why", ""), task_id=task_id)
            if name:
                await notify(f"🧑‍💼 HR yangi xodim oldi: {name}")

        steps = plan["steps"][:MAX_STEPS]
        await notify(f"📋 Reja: {str(plan.get('summary', ''))[:500]}\n" + "\n".join(
            f"• {s['agent']} [{s.get('tier') or 'auto'}]: {s['task'][:80]}" for s in steps))
        outputs: dict[str, str] = {}
        remaining = {s["id"]: s for s in steps}
        while remaining:
            ready = [s for s in remaining.values() if not any(d in remaining for d in s.get("depends_on", []))]
            if not ready:  # sikl: qolganlarini ketma-ket bajaramiz
                ready = [next(iter(remaining.values()))]
            await self._check_pause()

            async def work(s):
                ctx = "\n\n".join(f"## {d}\n{clip(outputs[d])}" for d in s.get("depends_on", []) if d in outputs)
                out = await self.team.run_agent(s["agent"], s["task"], ctx, task_id=task_id, tier=s.get("tier"), env=env)
                await notify(f"✅ {s['agent']} tugatdi ({s['id']})")
                return s["id"], out

            for sid, out in await self._gather_or_cancel([work(s) for s in ready]):
                outputs[sid] = out
                remaining.pop(sid)

        deliverable = await self._synthesize(task_id, request, outputs, None, "mid", env)
        for attempt in range(self.max_revisions + 1):
            await self._check_pause()
            verdict = await self._review(task_id, request, deliverable, env)
            if verdict.get("verdict") == "pass" or attempt == self.max_revisions:
                if verdict.get("verdict") != "pass":
                    deliverable += "\n\n⚠️ QA hali ham e'tiroz bildirgan:\n- " + "\n- ".join(verdict.get("issues", []))
                break
            await notify(f"🔎 QA {len(verdict.get('issues', []))} ta muammo topdi, tuzatilyapti...")
            deliverable = await self._synthesize(task_id, request, outputs, verdict.get("issues", []), "strong", env,
                                                 previous=deliverable)
        return await self._package(task_id, request, deliverable, env)

    async def _package(self, task_id, request, deliverable, env) -> str:
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
        prompt = (f"# Original request\n{request}\n\n# Final deliverable\n{clip(deliverable, 10000)}\n\n"
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
            raw = await self.team.run_agent("ceo", prompt, task_id=task_id, tier="mid", env=env)
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

    async def _plan(self, task_id, request, env) -> dict:
        roster = await self.team.roster()
        mems = await self.store.search_memories(request, 5)
        memo = ("# Relevant memory about the owner/business\n" + "\n".join(f"- {m['text']}" for m in mems) + "\n\n") if mems else ""
        prompt = (f"# Team\n{roster}\n\n{memo}# Request\n{request}\n\n"
                  "Plan the work. Use at most 6 steps; steps without dependencies run in parallel. "
                  "Specialists can write files, search the web and (with the owner's approval) run commands. "
                  "Pick a model tier per step: 'cheap' for lookups and simple drafting, 'mid' for substantive "
                  "creation, 'strong' ONLY for the hardest reasoning or architecture. Prefer the cheaper tier when unsure. "
                  "Only propose new_roles if no existing role fits (max 2). "
                  'Return ONLY JSON: {"summary": "...", "steps": [{"id": "s1", "agent": "<team member name>", '
                  '"task": "self-contained instruction", "tier": "cheap|mid|strong", "depends_on": []}], '
                  '"new_roles": [{"name": "snake_case_name", "why": "..."}]}')
        last_err = None
        for _ in range(2):
            raw = await self.team.run_agent("ceo", prompt, task_id=task_id, env=env)
            try:
                plan = normalize_plan(extract_json(raw))
                if plan["steps"]:
                    return plan
                last_err = "steps bo'sh"
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                last_err = e
            prompt += "\n\nYour previous answer was not valid JSON in the required shape. Return ONLY the JSON."
        raise RuntimeError(f"Rahbar yaroqli reja tuza olmadi: {last_err}")

    async def _synthesize(self, task_id, request, outputs, issues, tier, env, previous=None) -> str:
        parts = "\n\n".join(f"## {k}\n{clip(v, 8000)}" for k, v in outputs.items())
        files = self._files(env.workspace)
        prompt = (f"# Original request\n{request}\n\n# Team outputs\n{parts}\n\n"
                  f"# Files in the workspace (delivered to the user automatically)\n{', '.join(files) or '(none)'}\n\n"
                  "Assemble ONE final, complete answer for the user. Merge the outputs, remove duplication, "
                  "keep all concrete content, and refer to the delivered files by name.")
        if issues:
            prompt += ("\n\n# Previous draft\n" + clip(previous or "", 8000) +
                       "\n\n# Reviewer issues to fix\n- " + "\n- ".join(issues))
        return await self.team.run_agent("ceo", prompt, task_id=task_id, tier=tier, env=env)

    async def _review(self, task_id, request, deliverable, env) -> dict:
        files = self._files(env.workspace)
        prompt = (f"# Original request\n{request}\n\n# Deliverable\n{clip(deliverable, 12000)}\n\n"
                  f"# Workspace files (you may read them)\n{', '.join(files) or '(none)'}\n\n"
                  "Does the deliverable fully and correctly satisfy the request? Report only real problems "
                  '(missing parts, errors, ignored requirements). Return ONLY JSON: '
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
