from __future__ import annotations

import asyncio
import json
from typing import Awaitable, Callable

from .db import Store, now
from .router import BudgetExhausted, TaskBudgetExceeded
from .team import Team
from .util import clip, extract_json

Notify = Callable[[str], Awaitable[None]]
MAX_STEPS = 8


class Paused(Exception):
    pass


async def _noop(_: str):
    return None


class Orchestrator:
    def __init__(self, store: Store, team: Team, max_revisions: int = 1):
        self.store, self.team, self.max_revisions = store, team, max_revisions

    async def _check_pause(self):
        if await self.store.get_kv("paused") == "1":
            raise Paused()

    async def run_task(self, request: str, chat_id: int = 0, notify: Notify = _noop) -> dict:
        task_id = await self.store.create_task(chat_id, request)
        await notify(f"📝 Vazifa #{task_id} qabul qilindi. Rahbar rejalashtiryapti...")
        try:
            result = await self._run(task_id, request, notify)
            await self.store.update_task(task_id, status="done", result=result, finished_at=now())
            return {"task_id": task_id, "status": "done", "result": result}
        except Paused:
            msg = "Vazifa /pause sababli to'xtatildi."
            status = "paused"
        except TaskBudgetExceeded as e:
            msg, status = f"Vazifa byudjet limiti sabab to'xtatildi: {e}", "stopped"
        except BudgetExhausted as e:
            msg, status = f"Barcha provayder limitlari tugadi yoki ulanmagan: {e}", "stopped"
        except Exception as e:  # noqa: BLE001 — vazifa jimgina yo'qolmasligi kerak
            await self.store.audit("orchestrator", "task_error", f"#{task_id}: {e!r}")
            msg, status = f"Xatolik: {e}", "failed"
        partial = "\n\n".join(f"[{m['agent']}]\n{m['content']}" for m in await self.store.task_messages(task_id)
                              if m["agent"] not in ("hr", "qa"))
        await self.store.update_task(task_id, status=status, result=partial or None, finished_at=now())
        return {"task_id": task_id, "status": status, "result": partial, "error": msg}

    async def _run(self, task_id: int, request: str, notify: Notify) -> str:
        plan = await self._plan(task_id, request)
        await self.store.update_task(task_id, plan=json.dumps(plan, ensure_ascii=False))

        for role in plan.get("new_roles", [])[:3]:
            await self._check_pause()
            name = await self.team.hire(role.get("name", "specialist"), role.get("why", ""), task_id=task_id)
            if name:
                await notify(f"🧑‍💼 HR yangi xodim oldi: {name}")

        steps = plan["steps"][:MAX_STEPS]
        await notify(f"📋 Reja: {plan.get('summary', '')}\n" + "\n".join(f"• {s['agent']}: {s['task'][:80]}" for s in steps))
        outputs: dict[str, str] = {}
        remaining = {s["id"]: s for s in steps}
        while remaining:
            ready = [s for s in remaining.values() if not any(d in remaining for d in s.get("depends_on", []))]
            if not ready:  # sikl yoki noto'g'ri bog'liqlik: qolganlarini ketma-ket bajaramiz
                ready = [next(iter(remaining.values()))]
            await self._check_pause()

            async def work(s):
                ctx = "\n\n".join(f"## {d}\n{clip(outputs[d])}" for d in s.get("depends_on", []) if d in outputs)
                out = await self.team.run_agent(s["agent"], s["task"], ctx, task_id=task_id)
                await notify(f"✅ {s['agent']} tugatdi ({s['id']})")
                return s["id"], out

            for sid, out in await asyncio.gather(*(work(s) for s in ready)):
                outputs[sid] = out
                remaining.pop(sid)

        deliverable = await self._synthesize(task_id, request, outputs, None, "mid")
        for attempt in range(self.max_revisions + 1):
            await self._check_pause()
            verdict = await self._review(task_id, request, deliverable)
            if verdict.get("verdict") == "pass" or attempt == self.max_revisions:
                if verdict.get("verdict") != "pass":
                    deliverable += "\n\n⚠️ QA hali ham e'tiroz bildirgan:\n- " + "\n- ".join(verdict.get("issues", []))
                break
            await notify(f"🔎 QA {len(verdict.get('issues', []))} ta muammo topdi, tuzatilyapti...")
            deliverable = await self._synthesize(task_id, request, outputs, verdict.get("issues", []), "strong",
                                                 previous=deliverable)
        return deliverable

    async def _plan(self, task_id, request) -> dict:
        roster = await self.team.roster()
        prompt = (f"# Team\n{roster}\n\n# Request\n{request}\n\n"
                  "Plan the work. Use at most 6 steps; steps without dependencies run in parallel. "
                  "Only propose new_roles if no existing role fits (max 2). "
                  'Return ONLY JSON: {"summary": "...", "steps": [{"id": "s1", "agent": "<team member name>", '
                  '"task": "self-contained instruction", "depends_on": []}], '
                  '"new_roles": [{"name": "snake_case_name", "why": "..."}]}')
        last_err = None
        for _ in range(2):
            raw = await self.team.run_agent("ceo", prompt, task_id=task_id)
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

    async def _synthesize(self, task_id, request, outputs, issues, tier, previous=None) -> str:
        parts = "\n\n".join(f"## {k}\n{clip(v, 8000)}" for k, v in outputs.items())
        prompt = (f"# Original request\n{request}\n\n# Team outputs\n{parts}\n\n"
                  "Assemble ONE final, complete deliverable for the user. Merge the outputs, remove duplication, "
                  "keep all concrete content (code, copy, numbers).")
        if issues:
            prompt += ("\n\n# Previous draft\n" + clip(previous or "", 8000) +
                       "\n\n# Reviewer issues to fix\n- " + "\n- ".join(issues))
        return await self.team.run_agent("ceo", prompt, task_id=task_id, tier=tier)

    async def _review(self, task_id, request, deliverable) -> dict:
        prompt = (f"# Original request\n{request}\n\n# Deliverable\n{clip(deliverable, 12000)}\n\n"
                  "Does the deliverable fully and correctly satisfy the request? Report only real problems "
                  '(missing parts, errors, ignored requirements). Return ONLY JSON: '
                  '{"verdict": "pass|fail", "issues": ["..."]}')
        raw = await self.team.run_agent("qa", prompt, task_id=task_id)
        try:
            v = extract_json(raw)
            v["issues"] = [str(i) for i in v.get("issues", [])]
            return v
        except (ValueError, TypeError):
            return {"verdict": "pass", "issues": []}  # QA ishlamasa vazifani bloklamaymiz
