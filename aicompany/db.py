from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

md = sa.MetaData()

agents = sa.Table(
    "agents", md,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String, unique=True, nullable=False),
    sa.Column("role", sa.String, nullable=False),
    sa.Column("system_prompt", sa.Text, nullable=False),
    sa.Column("tier", sa.String, nullable=False, default="mid"),
    sa.Column("status", sa.String, nullable=False, default="active"),  # active | fired
    sa.Column("created_by", sa.String, default="seed"),
    sa.Column("created_at", sa.String),
    sa.Column("fired_at", sa.String),
)
tasks = sa.Table(
    "tasks", md,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("chat_id", sa.Integer),
    sa.Column("request", sa.Text, nullable=False),
    sa.Column("status", sa.String, nullable=False, default="running"),
    sa.Column("plan", sa.Text),
    sa.Column("result", sa.Text),
    sa.Column("created_at", sa.String),
    sa.Column("finished_at", sa.String),
)
messages = sa.Table(
    "messages", md,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("task_id", sa.Integer, index=True),
    sa.Column("agent", sa.String),
    sa.Column("content", sa.Text),
    sa.Column("created_at", sa.String),
)
usage = sa.Table(
    "usage", md,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("ts", sa.String, index=True),
    sa.Column("provider", sa.String, index=True),
    sa.Column("model", sa.String),
    sa.Column("task_id", sa.Integer, index=True),
    sa.Column("agent", sa.String),
    sa.Column("tokens_in", sa.Integer),
    sa.Column("tokens_out", sa.Integer),
    sa.Column("tokens_cached", sa.Integer),
    sa.Column("cost_usd", sa.Float),
)
cache = sa.Table(
    "cache", md,
    sa.Column("key", sa.String, primary_key=True),
    sa.Column("value", sa.Text),
    sa.Column("created_at", sa.String),
)
kv = sa.Table(
    "kv", md,
    sa.Column("key", sa.String, primary_key=True),
    sa.Column("value", sa.Text),
)
audit_log = sa.Table(
    "audit_log", md,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("ts", sa.String),
    sa.Column("actor", sa.String),
    sa.Column("action", sa.String),
    sa.Column("detail", sa.Text),
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def cache_key(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class Store:
    def __init__(self, url: str):
        if url.startswith("sqlite") and ":memory:" not in url:
            Path(url.split("///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_async_engine(url)

    async def init(self):
        async with self.engine.begin() as c:
            await c.run_sync(md.create_all)

    async def close(self):
        await self.engine.dispose()

    async def _all(self, stmt):
        async with self.engine.connect() as c:
            return [dict(r._mapping) for r in await c.execute(stmt)]

    async def _one(self, stmt):
        rows = await self._all(stmt)
        return rows[0] if rows else None

    async def _exec(self, stmt):
        async with self.engine.begin() as c:
            return await c.execute(stmt)

    # kv
    async def get_kv(self, key, default=None):
        row = await self._one(sa.select(kv).where(kv.c.key == key))
        return row["value"] if row else default

    async def set_kv(self, key, value):
        async with self.engine.begin() as c:
            await c.execute(sa.delete(kv).where(kv.c.key == key))
            await c.execute(sa.insert(kv).values(key=key, value=str(value)))

    # agents
    async def list_agents(self, active_only=True):
        q = sa.select(agents).order_by(agents.c.id)
        if active_only:
            q = q.where(agents.c.status == "active")
        return await self._all(q)

    async def get_agent(self, name):
        return await self._one(sa.select(agents).where(agents.c.name == name, agents.c.status == "active"))

    async def create_agent(self, name, role, system_prompt, tier="mid", created_by="seed"):
        async with self.engine.begin() as c:
            await c.execute(sa.delete(agents).where(agents.c.name == name, agents.c.status == "fired"))
            await c.execute(sa.insert(agents).values(
                name=name, role=role, system_prompt=system_prompt, tier=tier,
                status="active", created_by=created_by, created_at=now()))

    async def fire_agent(self, name) -> bool:
        res = await self._exec(sa.update(agents).where(agents.c.name == name, agents.c.status == "active")
                               .values(status="fired", fired_at=now()))
        return res.rowcount > 0

    # tasks
    async def create_task(self, chat_id, request) -> int:
        res = await self._exec(sa.insert(tasks).values(
            chat_id=chat_id, request=request, status="running", created_at=now()))
        return res.inserted_primary_key[0]

    async def update_task(self, task_id, **fields):
        await self._exec(sa.update(tasks).where(tasks.c.id == task_id).values(**fields))

    async def get_task(self, task_id):
        return await self._one(sa.select(tasks).where(tasks.c.id == task_id))

    async def list_tasks(self, limit=10):
        return await self._all(sa.select(tasks).order_by(tasks.c.id.desc()).limit(limit))

    async def add_message(self, task_id, agent, content):
        await self._exec(sa.insert(messages).values(task_id=task_id, agent=agent, content=content, created_at=now()))

    async def task_messages(self, task_id):
        return await self._all(sa.select(messages).where(messages.c.task_id == task_id).order_by(messages.c.id))

    # usage
    async def add_usage(self, provider, model, task_id, agent, tin, tout, tcached, cost):
        await self._exec(sa.insert(usage).values(
            ts=now(), provider=provider, model=model, task_id=task_id, agent=agent,
            tokens_in=tin, tokens_out=tout, tokens_cached=tcached, cost_usd=cost))

    async def _sum(self, *conds):
        row = await self._one(sa.select(sa.func.coalesce(sa.func.sum(usage.c.cost_usd), 0.0).label("s")).where(*conds))
        return float(row["s"])

    async def spent(self, provider):
        return await self._sum(usage.c.provider == provider)

    async def spent_task(self, task_id):
        return await self._sum(usage.c.task_id == task_id)

    async def spent_since(self, iso_ts):
        return await self._sum(usage.c.ts >= iso_ts)

    async def spent_by_agent(self):
        return await self._all(
            sa.select(usage.c.agent, sa.func.sum(usage.c.cost_usd).label("cost"))
            .group_by(usage.c.agent).order_by(sa.desc("cost")))

    # cache
    async def cache_get(self, key):
        row = await self._one(sa.select(cache).where(cache.c.key == key))
        return row["value"] if row else None

    async def cache_put(self, key, value):
        async with self.engine.begin() as c:
            await c.execute(sa.delete(cache).where(cache.c.key == key))
            await c.execute(sa.insert(cache).values(key=key, value=value, created_at=now()))

    # audit
    async def audit(self, actor, action, detail=""):
        await self._exec(sa.insert(audit_log).values(ts=now(), actor=actor, action=action, detail=detail))
