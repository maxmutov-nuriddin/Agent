from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

APPROVAL_TIMEOUT = 600


class Approver:
    """Xavfli amallar uchun egasidan ruxsat so'raydi."""

    async def ask(self, approval_id: int, task_id: int | None, agent: str, description: str) -> bool:
        return False


class DenyApprover(Approver):
    pass


class AutoApprover(Approver):  # faqat testlar uchun
    def __init__(self, answer=True):
        self.answer, self.asked = answer, []

    async def ask(self, approval_id, task_id, agent, description):
        self.asked.append(description)
        return self.answer


class CliApprover(Approver):
    async def ask(self, approval_id, task_id, agent, description):
        print(f"\n🔐 [{agent}] ruxsat so'ralmoqda:\n{description}")
        ans = await asyncio.to_thread(input, "Ruxsat berasizmi? [y/N] ")
        return ans.strip().lower() in ("y", "yes", "ha")


Announcer = Callable[[int, int | None, str, str], Awaitable[None]]


class ApprovalCenter(Approver):
    """Telegram va veb uchun umumiy: ruxsat so'rovlari shu yerda kutadi, istalgan kanaldan hal qilinadi."""

    def __init__(self, timeout: float | None = None):
        self.timeout = timeout if timeout is not None else APPROVAL_TIMEOUT
        self.pending: dict[int, dict] = {}
        self.announcers: list[Announcer] = []
        self.expired: set[int] = set()  # javob berilmagan (muddati o'tgan) so'rovlar

    async def ask(self, approval_id, task_id, agent, description) -> bool:
        fut = asyncio.get_running_loop().create_future()
        self.pending[approval_id] = {"id": approval_id, "task_id": task_id, "agent": agent,
                                     "description": description, "future": fut}
        try:
            for announce in self.announcers:
                try:
                    await announce(approval_id, task_id, agent, description)
                except Exception:  # noqa: BLE001 — bitta kanal ishlamasa ikkinchisi baribir ishlaydi
                    continue
            return await asyncio.wait_for(fut, self.timeout)
        except asyncio.TimeoutError:
            self.expired.add(approval_id)
            return False
        finally:
            self.pending.pop(approval_id, None)

    def resolve(self, approval_id: int, ok: bool) -> bool:
        item = self.pending.get(approval_id)
        if item and not item["future"].done():
            item["future"].set_result(ok)
            return True
        return False

    def list(self) -> list[dict]:
        return [{k: v for k, v in p.items() if k != "future"} for p in self.pending.values()]
