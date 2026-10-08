from __future__ import annotations

from .db import Store
from .router import Router
from .util import extract_json, slug

LANG = ("Reply in the language the user wrote their request in (Uzbek, Russian or English). "
        "Be concrete and complete; deliver finished work, not advice about how to do it. "
        "Never invent facts, links, prices or credentials; say what is unknown.")

SEED = {
    "ceo": ("Chief executive: understands the request, plans the work, assigns it to the team and assembles the final deliverable.", "mid"),
    "hr": ("HR manager: designs new team roles and writes their instructions.", "cheap"),
    "qa": ("Quality reviewer: strictly checks a deliverable against the original request and finds real problems.", "cheap"),
    "developer": ("Senior software engineer: writes clean, working code with tests and short run instructions.", "mid"),
    "marketer": ("Marketing strategist and copywriter: positioning, content plans, ad copy, social media.", "mid"),
    "researcher": ("Analyst: structured research, comparisons, summaries and recommendations.", "cheap"),
    "generalist": ("Versatile specialist used when no other role fits.", "cheap"),
}


def system_prompt(name: str, role: str) -> str:
    return f"You are '{name}', a member of an AI company team. Your role: {role}\n{LANG}"


class Team:
    def __init__(self, store: Store, router: Router, max_agents: int):
        self.store, self.router, self.max_agents = store, router, max_agents

    async def ensure_seed(self):
        for name, (role, tier) in SEED.items():
            if not await self.store.get_agent(name):
                await self.store.create_agent(name, role, system_prompt(name, role), tier, "seed")

    async def roster(self) -> str:
        return "\n".join(f"- {a['name']}: {a['role']}" for a in await self.store.list_agents())

    async def run_agent(self, name: str, instruction: str, context: str = "", *, task_id=None, tier=None) -> str:
        agent = await self.store.get_agent(name) or await self.store.get_agent("generalist")
        content = instruction if not context else f"{instruction}\n\n# Context from teammates\n{context}"
        res = await self.router.call(tier or agent["tier"], agent["system_prompt"],
                                     [{"role": "user", "content": content}],
                                     task_id=task_id, agent=agent["name"])
        if task_id is not None:
            await self.store.add_message(task_id, agent["name"], res.text)
        return res.text

    async def hire(self, name: str, why: str, *, created_by="hr", task_id=None) -> str | None:
        """HR yangi agent yaratadi. Joy bo'lmasa None qaytaradi."""
        name = slug(name)
        if await self.store.get_agent(name):
            return name
        if len(await self.store.list_agents()) >= self.max_agents:
            await self.store.audit("hr", "hire_rejected", f"{name}: jamoa to'lgan")
            return None
        prompt = (f"Design a new team role.\nName: {name}\nWhy needed: {why}\n\n"
                  'Return ONLY JSON: {"role": "one sentence describing expertise and duties", '
                  '"tier": "cheap|mid|strong"}. Use "cheap" unless the work needs deep expertise.')
        raw = await self.run_agent("hr", prompt, task_id=task_id)
        try:
            spec = extract_json(raw)
            role, tier = str(spec["role"]), spec.get("tier", "mid")
        except (ValueError, KeyError):
            role, tier = why, "mid"
        if tier not in ("cheap", "mid", "strong"):
            tier = "mid"
        if tier == "strong":
            tier = "mid"  # yangi agent avtomatik "strong" bo'lib ketmasin: narx nazorati
        await self.store.create_agent(name, role, system_prompt(name, role), tier, created_by)
        await self.store.audit(created_by, "hire", f"{name} ({tier}): {role}")
        return name

    async def fire(self, name: str, *, actor="owner") -> bool:
        if name in ("ceo", "hr", "qa", "generalist"):
            return False
        ok = await self.store.fire_agent(name)
        if ok:
            await self.store.audit(actor, "fire", name)
        return ok
