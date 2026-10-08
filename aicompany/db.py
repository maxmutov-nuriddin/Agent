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
    sa.Column("chat_id", sa.BigInteger),
    sa.Column("request", sa.Text, nullable=False),
    sa.Column("status", sa.String, nullable=False, default="running"),
    sa.Column("plan", sa.Text),
    sa.Column("result", sa.Text),
    sa.Column("created_at", sa.String),
    sa.Column("finished_at", sa.String),
    sa.Column("based_on", sa.Integer),  # qaysi vazifa natijasi ustida davom etilgan
    sa.Column("note", sa.Text),  # to'xtash/xato sababi (foydalanuvchiga ko'rsatiladi)
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
    sa.Column("kind", sa.String, server_default="command"),  # command | telegram
    sa.Column("status", sa.String, default="pending"),  # pending | approved | denied | expired
    sa.Column("created_at", sa.String),
    sa.Column("decided_at", sa.String),
)
chat_log = sa.Table(
    "chat_log", md,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("chat_id", sa.BigInteger, index=True),
    sa.Column("role", sa.String),  # owner | ceo
    sa.Column("text", sa.Text),
    sa.Column("created_at", sa.String),
)
reminders = sa.Table(
    "reminders", md,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("chat_id", sa.BigInteger),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("due_at", sa.String, index=True),   # UTC ISO
    sa.Column("status", sa.String, server_default="pending"),  # pending | sent | cancelled | failed
    sa.Column("created_at", sa.String),
    sa.Column("sent_at", sa.String),
)
# Vazifa fayllari bazada ham (Render kabi disksiz serverda qayta ishga tushganda yo'qolmasin)
task_files = sa.Table(
    "task_files", md,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("task_id", sa.Integer, index=True, nullable=False),
    sa.Column("path", sa.String, nullable=False),
    sa.Column("data", sa.LargeBinary, nullable=False),
    sa.Column("updated_at", sa.String),
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


def normalize_db_url(url: str) -> tuple[str, dict]:
    """Supabase/Render bergan postgres://... satrini SQLAlchemy async drayveriga moslaydi."""
    url = url.strip()
    if url.startswith(("postgres://", "postgresql://")):
        url = "postgresql+asyncpg://" + url.split("://", 1)[1]
    if url.startswith("postgresql+asyncpg://"):
        args = {}
        if ":6543/" in url or "pgbouncer=true" in url:  # Supabase tranzaksiya pooler'i tayyorlangan so'rovlarni qo'llamaydi
            args["statement_cache_size"] = 0
            url = re.sub(r"[?&]pgbouncer=true", "", url)
        return url, args
    return url, ({"timeout": 30} if url.startswith("sqlite") else {})


class Store:
    def __init__(self, url: str):
        url, connect_args = normalize_db_url(url)
        self._kv_cache: dict[str, str | None] = {}
        self.remote = not url.startswith("sqlite")  # tashqi baza: fayllar va Telegram sessiyasi ham bazada saqlanadi
        if url.startswith("sqlite") and ":memory:" not in url:
            Path(url.split("///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
        # tashqi baza uzoqda (har borib kelish ~0.2 s): ulanishlar doimiy ochiq turadi (har so'rovda yangi SSL ulanish
        # ochilmaydi), pre_ping yo'q (har so'rovga +1 borib kelish), soni Supabase bepul pooler limitidan kichik
        extra = {"pool_recycle": 600, "pool_size": 8, "max_overflow": 2} if self.remote else {}
        self.engine = create_async_engine(url, connect_args=connect_args, **extra)
        # o'qish: BEGIN/ROLLBACK siz (har o'qishda 2 ta ortiqcha borib kelish tejaladi)
        self.reader = self.engine.execution_options(isolation_level="AUTOCOMMIT") if self.remote else self.engine
        if url.startswith("sqlite") and ":memory:" not in url:
            @sa.event.listens_for(self.engine.sync_engine, "connect")
            def _pragmas(dbapi_conn, _):  # parallel o'qish/yozish uchun (agentlar bir vaqtda ishlaydi)
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.close()

    async def init(self):
        try:
            await self._init()
        except OSError as e:
            host = self.engine.url.host or ""
            if re.fullmatch(r"db\.[a-z0-9]+\.supabase\.co", host):
                raise SystemExit("DATABASE_URL: Supabase 'Direct connection' (db....supabase.co) faqat IPv6, Render ulana olmaydi. "
                                 "Supabase -> Connect -> Session pooler satrini oling (...pooler.supabase.com:5432).") from e
            raise SystemExit(f"Bazaga ulanib bo'lmadi ({host}): {e}. DATABASE_URL ni tekshiring.") from e

    async def _init(self):
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
                    arg = col.server_default.arg
                    ddl += f" DEFAULT {arg.text if hasattr(arg, 'text') else repr(str(arg))}"  # sa.text("0") yoki oddiy matn
                elif not col.nullable:
                    ddl += " DEFAULT ''"
                conn.execute(sa.text(ddl))

    async def close(self):
        await self.engine.dispose()

    async def _all(self, stmt):
        async with self.reader.connect() as c:
            return [dict(r._mapping) for r in await c.execute(stmt)]

    async def _one(self, stmt):
        rows = await self._all(stmt)
        return rows[0] if rows else None

    async def _exec(self, stmt):
        async with self.engine.begin() as c:
            return await c.execute(stmt)

    # kv
    async def get_kv(self, key, default=None):
        # sozlamalar juda tez-tez o'qiladi, kam yoziladi: jarayon xotirasida keshlanadi (bitta jarayon yozadi)
        if key in self._kv_cache:
            v = self._kv_cache[key]
            return default if v is None else v
        row = await self._one(sa.select(kv).where(kv.c.key == key))
        self._kv_cache[key] = row["value"] if row else None
        return row["value"] if row else default

    def _upsert(self, table, key_col: str, values: dict):
        """Bor bo'lsa yangilaydi, yo'q bo'lsa qo'shadi: bir vaqtdagi yozuvlarda to'qnashuv bo'lmaydi (Postgres ham, SQLite ham)."""
        if self.engine.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        stmt = insert(table).values(**values)
        return stmt.on_conflict_do_update(index_elements=[key_col],
                                          set_={k: stmt.excluded[k] for k in values if k != key_col})

    async def set_kv(self, key, value):
        await self._exec(self._upsert(kv, "key", {"key": key, "value": str(value)}))
        self._kv_cache[key] = str(value)

    # vazifa fayllari (tashqi bazada nusxa)
    async def save_task_files(self, task_id, files: dict[str, bytes]):
        async with self.engine.begin() as c:
            await c.execute(sa.delete(task_files).where(task_files.c.task_id == task_id))
            for path, data in files.items():
                await c.execute(sa.insert(task_files).values(task_id=task_id, path=path, data=data, updated_at=now()))

    async def load_task_files(self, task_id=None) -> list[dict]:
        q = sa.select(task_files.c.task_id, task_files.c.path, task_files.c.data)
        return await self._all(q if task_id is None else q.where(task_files.c.task_id == task_id))

    async def delete_task_files(self, task_id):
        await self._exec(sa.delete(task_files).where(task_files.c.task_id == task_id))

    async def delete_kv(self, key):
        await self._exec(sa.delete(kv).where(kv.c.key == key))
        self._kv_cache.pop(key, None)

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

    async def set_agent_tools(self, name, tools):
        await self._exec(sa.update(agents).where(agents.c.name == name, agents.c.status == "active").values(tools=tools))

    async def fire_agent(self, name) -> bool:
        res = await self._exec(sa.update(agents).where(agents.c.name == name, agents.c.status == "active")
                               .values(status="fired", fired_at=now()))
        return res.rowcount > 0

    # tasks
    async def create_task(self, chat_id, request, based_on=None) -> int:
        res = await self._exec(sa.insert(tasks).values(
            chat_id=chat_id, request=request, status="running", created_at=now(), based_on=based_on))
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
            await c.execute(sa.delete(task_files).where(task_files.c.task_id == task_id))

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

    async def daily_spend(self, since_iso: str) -> list[dict]:
        """Kunlik sarf (UTC sana bo'yicha): [{"day": "2026-10-08", "cost": 0.12}]. SQLite va Postgres'da bir xil."""
        day = sa.func.substr(usage.c.ts, 1, 10).label("day")
        rows = await self._all(sa.select(day, sa.func.sum(usage.c.cost_usd).label("cost"))
                               .where(usage.c.ts >= since_iso).group_by(day).order_by(day))
        return [{"day": r["day"], "cost": float(r["cost"] or 0)} for r in rows]

    async def spent_by_task(self, task_ids) -> dict[int, float]:
        if not task_ids:
            return {}
        rows = await self._all(sa.select(usage.c.task_id, sa.func.sum(usage.c.cost_usd).label("cost"))
                               .where(usage.c.task_id.in_(list(task_ids))).group_by(usage.c.task_id))
        return {r["task_id"]: float(r["cost"] or 0) for r in rows}

    async def spent_by_agent(self):
        return await self._all(
            sa.select(usage.c.agent, sa.func.sum(usage.c.cost_usd).label("cost"))
            .group_by(usage.c.agent).order_by(sa.desc("cost")))

    # cache
    async def cache_get(self, key):
        row = await self._one(sa.select(cache).where(cache.c.key == key))
        return row["value"] if row else None

    async def cache_put(self, key, value):
        await self._exec(self._upsert(cache, "key", {"key": key, "value": value, "created_at": now()}))

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

    async def interrupted_since(self, iso_ts: str):
        return await self._all(sa.select(tasks).where(tasks.c.status == "interrupted", tasks.c.finished_at >= iso_ts).order_by(tasks.c.id))

    async def audit_since(self, iso_ts: str, limit: int = 300):
        return await self._all(sa.select(audit_log).where(audit_log.c.ts >= iso_ts).order_by(audit_log.c.id.desc()).limit(limit))

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

    async def delete_memory(self, memory_id) -> bool:
        res = await self._exec(sa.delete(memories).where(memories.c.id == memory_id))
        return res.rowcount > 0

    async def kv_prefix(self, prefix) -> dict:
        rows = await self._all(sa.select(kv).where(kv.c.key.like(prefix + "%")))
        return {r["key"][len(prefix):]: r["value"] for r in rows}

    async def recent_audit(self, limit=60):
        return await self._all(sa.select(audit_log).order_by(audit_log.c.id.desc()).limit(limit))

    # eslatmalar
    async def add_reminder(self, chat_id, text, due_iso) -> int:
        res = await self._exec(sa.insert(reminders).values(chat_id=chat_id, text=text, due_at=due_iso, status="pending", created_at=now()))
        return res.inserted_primary_key[0]

    async def due_reminders(self, now_iso):
        return await self._all(sa.select(reminders).where(reminders.c.status == "pending", reminders.c.due_at <= now_iso)
                               .order_by(reminders.c.due_at))

    async def mark_reminder(self, rid, status):
        await self._exec(sa.update(reminders).where(reminders.c.id == rid).values(status=status, sent_at=now()))

    async def list_reminders(self, include_done=False, limit=50):
        q = sa.select(reminders).order_by(reminders.c.due_at).limit(limit)
        return await self._all(q if include_done else q.where(reminders.c.status == "pending"))

    async def cancel_reminder(self, rid) -> bool:
        res = await self._exec(sa.update(reminders).where(reminders.c.id == rid, reminders.c.status == "pending")
                               .values(status="cancelled", sent_at=now()))
        return res.rowcount > 0

    # approvals
    async def create_approval(self, task_id, agent, description, kind="command") -> int:
        res = await self._exec(sa.insert(approvals).values(
            task_id=task_id, agent=agent, description=description[:2000], kind=kind, status="pending", created_at=now()))
        return res.inserted_primary_key[0]

    async def decide_approval(self, approval_id, status):
        await self._exec(sa.update(approvals).where(approvals.c.id == approval_id)
                         .values(status=status, decided_at=now()))

    async def task_approvals(self, task_id):
        return await self._all(sa.select(approvals).where(approvals.c.task_id == task_id).order_by(approvals.c.id))

    async def count_audit_since(self, action, iso_ts) -> int:
        row = await self._one(sa.select(sa.func.count().label("n")).where(audit_log.c.action == action, audit_log.c.ts >= iso_ts))
        return int(row["n"])

    # audit
    async def audit(self, actor, action, detail=""):
        await self._exec(sa.insert(audit_log).values(ts=now(), actor=actor, action=action, detail=detail))
