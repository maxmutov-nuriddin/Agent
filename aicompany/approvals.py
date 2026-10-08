from __future__ import annotations

import asyncio


class Approver:
    """Xavfli amallar uchun egasidan ruxsat so'raydi."""

    async def ask(self, task_id: int | None, agent: str, description: str) -> bool:
        return False


class DenyApprover(Approver):
    pass


class AutoApprover(Approver):  # faqat testlar uchun
    def __init__(self, answer=True):
        self.answer, self.asked = answer, []

    async def ask(self, task_id, agent, description):
        self.asked.append(description)
        return self.answer


class CliApprover(Approver):
    async def ask(self, task_id, agent, description):
        print(f"\n🔐 [{agent}] ruxsat so'ralmoqda:\n{description}")
        ans = await asyncio.to_thread(input, "Ruxsat berasizmi? [y/N] ")
        return ans.strip().lower() in ("y", "yes", "ha")
