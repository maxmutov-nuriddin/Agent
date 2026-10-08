from __future__ import annotations

import hashlib
import json
import re
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
    sa.Column("tools", sa.String, nullable=False, default=""),
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
    sa.Column("archived", sa.Integer, server_default=sa.text("0")),
    sa.Column("archived_at", sa.String),
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
memories = sa.Table(
    "memories", md,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("source", sa.String),
    sa.Column("created_at", sa.String),
)
approvals = sa.Table(
    "approvals", md,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("task_id", sa.Integer),
    sa.Column("agent", sa.String),
    sa.Column("description", sa.Text),
    sa.Column("status", sa.String, default="pending"),  # pending | approved | denied | expired
    sa.Column("created_at", sa.String),
    sa.Column("decided_at", sa.String),
)
chat_log = sa.Table(
    "chat_log", md,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("chat_id", sa.Integer, index=True),
    sa.Column("role", sa.String),  # owner | ceo
    sa.Column("text", sa.Text),
    sa.Column("created_at", sa.String),
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
        self.engine = create_async_engine(url, connect_args={"timeout": 30} if url.startswith("sqlite") else {})

    async def init(self):
        async with self.engine.begin() as c:
            await c.run_sync(md.create_all)
            await c.run_sync(self._add_missing_columns)

    @staticmethod
    def _add_missing_columns(conn):
        """Eski bazaga yangi ustunlarni qo'shadi (create_all mavjud jadvalni o'zgartirmaydi)."""
        insp = sa.inspect(conn)
        for table in md.sorted_tables:
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(conn.dialect)}"
                if col.server_default is not None:
                    ddl += f" DEFAULT {col.server_default.arg.text}"
                elif not col.nullable:
                    ddl += " DEFAULT ''"
                conn.execute(sa.text(ddl))

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

    async def create_agent(self, name, role, system_prompt, tier="mid", created_by="seed", tools=""):
        async with self.engine.begin() as c:
            await c.execute(sa.delete(agents).where(agents.c.name == name, agents.c.status == "fired"))
            await c.execute(sa.insert(agents).values(
                name=name, role=role, system_prompt=system_prompt, tier=tier,
                status="active", created_by=created_by, created_at=now(), tools=tools))

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

    async def list_tasks(self, limit=10, archived: bool = False):
        q = sa.select(tasks).order_by(tasks.c.id.desc()).limit(limit)
        q = q.where(tasks.c.archived == 1) if archived else q.where(sa.or_(tasks.c.archived == 0, tasks.c.archived.is_(None)))
        return await self._all(q)

    async def set_archived(self, task_id, flag: bool):
        await self._exec(sa.update(tasks).where(tasks.c.id == task_id)
                         .values(archived=1 if flag else 0, archived_at=now() if flag else None))

    async def delete_task(self, task_id):
        """Vazifa va uning xabarlarini butunlay o'chiradi. Sarf yozuvlari (usage) saqlanadi: pul haqiqatan sarflangan."""
        async with self.engine.begin() as c:
            await c.execute(sa.delete(messages).where(messages.c.task_id == task_id))
            await c.execute(sa.delete(approvals).where(approvals.c.task_id == task_id))
            await c.execute(sa.delete(tasks).where(tasks.c.id == task_id))

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

    # tasks (hisobot / HR tahlili)
    async def recent_task_ids(self, n):
        rows = await self._all(sa.select(tasks.c.id).order_by(tasks.c.id.desc()).limit(n))
        return [r["id"] for r in rows]

    async def tasks_since(self, iso_ts):
        return await self._all(sa.select(tasks).where(tasks.c.created_at >= iso_ts).order_by(tasks.c.id))

    async def agent_activity(self, task_ids):
        if not task_ids:
            return {}
        rows = await self._all(
            sa.select(messages.c.agent, sa.func.count().label("n"))
            .where(messages.c.task_id.in_(task_ids)).group_by(messages.c.agent))
        return {r["agent"]: r["n"] for r in rows}

    async def fail_stale_tasks(self) -> int:
        """Qayta ishga tushganda 'running' bo'lib qolgan vazifalarni 'interrupted' deb belgilaydi."""
        res = await self._exec(sa.update(tasks).where(tasks.c.status == "running")
                               .values(status="interrupted", finished_at=now()))
        return res.rowcount

    async def agent_message_counts(self):
        rows = await self._all(sa.select(messages.c.agent, sa.func.count().label("n")).group_by(messages.c.agent))
        return {r["agent"]: r["n"] for r in rows}

    async def chat_since(self, chat_id, after_id=0, limit=200):
        return await self._all(sa.select(chat_log).where(chat_log.c.chat_id == chat_id, chat_log.c.id > after_id)
                               .order_by(chat_log.c.id).limit(limit))

    # memory
    async def add_memory(self, text, source="agent"):
        await self._exec(sa.insert(memories).values(text=text.strip()[:1000], source=source, created_at=now()))

    async def recent_memories(self, limit=500):
        return await self._all(sa.select(memories).order_by(memories.c.id.desc()).limit(limit))

    async def search_memories(self, query, limit=5):
        words = {w for w in re.findall(r"\w{3,}", query.lower())}
        scored = []
        for m in await self.recent_memories(500):
            score = len(words & set(re.findall(r"\w{3,}", m["text"].lower())))
            if score:
                scored.append((score, m["id"], m))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        return [m for _, _, m in scored[:limit]]

    # suhbat tarixi
    async def add_chat(self, chat_id, role, text):
        await self._exec(sa.insert(chat_log).values(chat_id=chat_id, role=role, text=text[:4000], created_at=now()))

    async def recent_chat(self, chat_id, n=8):
        rows = await self._all(sa.select(chat_log).where(chat_log.c.chat_id == chat_id)
                               .order_by(chat_log.c.id.desc()).limit(n))
        return list(reversed(rows))

    async def clear_chat(self, chat_id):
        await self._exec(sa.delete(chat_log).where(chat_log.c.chat_id == chat_id))

    # approvals
    async def create_approval(self, task_id, agent, description) -> int:
        res = await self._exec(sa.insert(approvals).values(
            task_id=task_id, agent=agent, description=description[:2000], status="pending", created_at=now()))
        return res.inserted_primary_key[0]

    async def decide_approval(self, approval_id, status):
        await self._exec(sa.update(approvals).where(approvals.c.id == approval_id)
                         .values(status=status, decided_at=now()))

    # audit
    async def audit(self, actor, action, detail=""):
        await self._exec(sa.insert(audit_log).values(ts=now(), actor=actor, action=action, detail=detail))
