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


class Paused(Exception):
    pass


class Stopped(Exception):
    """Egasi vazifani to'xtatdi."""


async def _noop(_: str):
    return None


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
        return sorted(str(f.relative_to(ws)) for f in ws.rglob("*") if f.is_file())

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
        "Reply in the language the owner uses (Uzbek, Russian or English).")

    CHAT_ONLY = (
        "You are the CEO of an AI company, talking with its owner in a CHAT. This chat is for conversation only: "
        "greetings, questions about the company, team, budget and earlier results, advice, opinions, brainstorming, "
        "clarifying questions. NEVER start or claim to do work from here. Return ONLY JSON: "
        "{\"reply\": \"...\", \"proposed_task\": \"...\"}. Put your conversational answer in \"reply\" (short, warm, "
        "in the owner's language). If, and only if, the owner clearly asks for real work to be done (create, write, "
        "research, build, analyze...), set \"proposed_task\" to a COMPLETE self-contained description (merging earlier "
        "messages) and make the reply a short offer such as 'Buni vazifa qilib topshiraymi?'; the app shows a "
        "'Submit as task' button. Otherwise proposed_task is an empty string.")

    async def handle(self, text: str, chat_id: int = 0, notify: Notify = _noop,
                     attachments: list[Path] | None = None, allow_tasks: bool = True) -> dict:
        """Oddiy xabarni qabul qiladi. allow_tasks=True: suhbat yoki vazifa ekanini o'zi aniqlaydi (Telegram).
        allow_tasks=False: faqat suhbat; ish so'ralsa vazifa taklif qiladi (veb-chat)."""
        await self.store.add_chat(chat_id, "owner", text)
        decision = {"mode": "task", "task": text, "reply": ""}
        if not attachments:  # fayl yuborilgan bo'lsa, bu aniq vazifa
            decision = await self._front_desk(text, chat_id, allow_tasks)
        if decision["mode"] == "chat":
            await self.store.add_chat(chat_id, "ceo", decision["reply"])
            await notify(decision["reply"])
            if decision.get("task"):
                await self.store.add_chat(chat_id, "proposal", json.dumps({"task": decision["task"]}, ensure_ascii=False))
            return {"kind": "chat", "reply": decision["reply"], "proposed_task": decision.get("task") or None}
        if decision["reply"]:
            await self.store.add_chat(chat_id, "ceo", decision["reply"])
            await notify(decision["reply"])
        return await self._run_and_log(decision["task"], chat_id, notify, attachments)

    async def submit_task(self, text: str, chat_id: int = 0, notify: Notify = _noop,
                          attachments: list[Path] | None = None) -> dict:
        """Aniq vazifa (suhbatsiz): «Vazifa berish» tugmasi."""
        await self.store.add_chat(chat_id, "owner", "📌 " + text)
        return await self._run_and_log(text, chat_id, notify, attachments)

    async def _run_and_log(self, task_text, chat_id, notify, attachments) -> dict:
        res = await self.run_task(task_text, chat_id, notify, attachments)
        summary = f"[Vazifa #{res['task_id']} {res['status']}] " + (res.get("result") or res.get("error") or "")
        await self.store.add_chat(chat_id, "ceo", summary[:1500])
        res["kind"] = "task"
        return res

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
        prompt = (f"# Team\n{roster}\n\n" + (f"# Memory\n{memo}\n\n" if memo else "") +
                  (f"# Recent conversation\n{hist}\n\n" if hist else "") + f"# Latest owner message\n{text}")
        res = await self.team.router.call("cheap", self.FRONT_DESK if allow_tasks else self.CHAT_ONLY,
                                          [{"role": "user", "content": prompt}], agent="ceo-chat")
        try:
            d = extract_json(res.text)
            reply = str(d.get("reply", "")).strip()
            if allow_tasks and d.get("mode") == "task":
                return {"mode": "task", "reply": reply, "task": str(d.get("task", "")).strip() or text}
            if not allow_tasks:  # faqat suhbat: ish so'ralgan bo'lsa taklif sifatida qaytaramiz
                proposal = str(d.get("proposed_task") or (d.get("task") if d.get("mode") == "task" else "") or "").strip()
                return {"mode": "chat", "reply": reply or ("Buni vazifa qilib topshiraymi?" if proposal else "Tushundim."),
                        "task": proposal}
            if reply:
                return {"mode": "chat", "reply": reply, "task": ""}
        except (ValueError, TypeError):
            pass
        return {"mode": "chat", "reply": res.text.strip() or "Tushunmadim, qaytadan yozing.", "task": ""}

    async def run_task(self, request: str, chat_id: int = 0, notify: Notify = _noop,
                       attachments: list[Path] | None = None) -> dict:
        async with self.sem:
            return await self._run_task(request, chat_id, notify, attachments)

    async def _run_task(self, request: str, chat_id: int, notify: Notify,
                        attachments: list[Path] | None) -> dict:
        task_id = await self.store.create_task(chat_id, request)
        ws = self._workspace(task_id)
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
            out.update(status="failed", error=f"Xatolik: {e}")
        if out["status"] != "done":
            out["result"] = "\n\n".join(f"[{m['agent']}]\n{m['content']}" for m in await self.store.task_messages(task_id)
                                        if m["agent"] not in ("hr", "qa"))
            await self.store.update_task(task_id, status=out["status"], result=out["result"] or None, finished_at=now(),
                                         note=(out.get("error") or "")[:400] or None)
        out["files"] = self._files(ws)
        await self._maybe_review(task_id, notify)
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
                tier = s.get("tier") if s.get("tier") in TIERS else None
                out = await self.team.run_agent(s["agent"], s["task"], ctx, task_id=task_id, tier=tier, env=env)
                await notify(f"✅ {s['agent']} tugatdi ({s['id']})")
                return s["id"], out

            for sid, out in await asyncio.gather(*(work(s) for s in ready)):
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
        return deliverable

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
                plan = extract_json(raw)
                steps = [s for s in plan["steps"] if s.get("id") and s.get("agent") and s.get("task")]
                if steps:
                    plan["steps"] = steps
                    plan.setdefault("new_roles", [])
                    return plan
                last_err = "steps bo'sh"
            except (ValueError, KeyError, TypeError) as e:
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
            for fact in extract_json(res.text).get("facts", [])[:2]:
                await self.store.add_memory(str(fact), source=f"task#{task_id}")
        except (ValueError, TypeError, BudgetExhausted, TaskBudgetExceeded):
            return  # xotira yozilmasa vazifa natijasiga ta'sir qilmaydi
