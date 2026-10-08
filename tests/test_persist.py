import os

import pytest

from aicompany.app import build_app
from aicompany.db import Store, normalize_db_url
from aicompany.persist import collect, restore_workspaces, save_workspace, seal, unseal

from .conftest import scripted_company, settings


def test_normalize_db_url_for_supabase():
    assert normalize_db_url("postgres://u:p@h:5432/db") == ("postgresql+asyncpg://u:p@h:5432/db", {})
    assert normalize_db_url("postgresql://u:p@h:5432/db")[0] == "postgresql+asyncpg://u:p@h:5432/db"
    url, args = normalize_db_url("postgresql://u:p@aws-0.pooler.supabase.com:6543/postgres?pgbouncer=true")
    assert url == "postgresql+asyncpg://u:p@aws-0.pooler.supabase.com:6543/postgres" and args == {"statement_cache_size": 0}
    assert normalize_db_url("sqlite+aiosqlite:///data/x.db") == ("sqlite+aiosqlite:///data/x.db", {"timeout": 30})


def test_seal_roundtrip_and_wrong_key():
    t = seal("1BVtsOK8Bu...session", "sir")
    assert t.startswith("v1:") and "session" not in t
    assert unseal(t, "sir") == "1BVtsOK8Bu...session"
    assert unseal(t, "boshqa") is None and unseal(None, "sir") is None
    with pytest.raises(ValueError):
        seal("x", None)


async def test_files_saved_to_db_and_restored(tmp_path):
    st = settings()
    store = Store(st.database_url)
    await store.init()
    ws = tmp_path / "ws" / "task_7"
    (ws / "css").mkdir(parents=True)
    (ws / "index.html").write_text("<h1>salom</h1>")
    (ws / "css" / "a.css").write_bytes(b"body{}")
    (ws / "katta.bin").write_bytes(b"x" * 5_000_001)                      # juda katta: saqlanmaydi
    await save_workspace(store, ws, 7)
    assert set(collect(ws)) == {"index.html", "css/a.css"}
    restored = tmp_path / "new"
    assert await restore_workspaces(store, restored) == 2
    assert (restored / "task_7" / "css" / "a.css").read_bytes() == b"body{}"
    assert await restore_workspaces(store, restored) == 0                 # bor fayl qayta yozilmaydi
    await store.save_task_files(8, {"../../evil.txt": b"x"})              # yo'l tashqariga chiqolmaydi
    await restore_workspaces(store, restored)
    assert not (tmp_path / "evil.txt").exists() and not (restored / "evil.txt").exists()
    await store.delete_task_files(7)
    assert [r["task_id"] for r in await store.load_task_files()] == [8]
    await store.close()


@pytest.mark.skipif(not os.environ.get("AIC_TEST_PG"), reason="AIC_TEST_PG (Postgres) kerak")
async def test_remote_db_survives_restart_without_disk(tmp_path):
    """Render bepul tarifi: disk har safar bo'sh. Baza Supabase'da: fayllar va Telegram sessiyasi qaytadi."""
    from aicompany.tguser import TgUser
    from .conftest import MockProvider
    env = {"WEB_TOKEN": "tok", "WORKSPACE_DIR": str(tmp_path / "ws1")}
    st = settings(**env)
    app = await build_app(st, {"anthropic": MockProvider("anthropic", scripted_company())})
    assert app.store.remote and app.tg.use_db_session
    res = await app.orch.run_task("sayt yasab ber", 1)
    assert "NATIJA.md" in res["files"]
    # Telegram sessiyasi bazaga shifrlab yoziladi
    await app.tg.on_session("SESSION-STRING")
    sealed = await app.store.get_kv("tg_session_sealed")
    assert sealed.startswith("v1:") and "SESSION-STRING" not in sealed
    await app.store.set_kv("tg_api_id", "1")
    await app.store.set_kv("tg_api_hash", "h" * 32)
    await app.store.close()
    # "qayta ishga tushish": yangi bo'sh disk, o'sha baza
    st2 = settings(**{**env, "WORKSPACE_DIR": str(tmp_path / "ws2")})
    import dataclasses
    st2 = dataclasses.replace(st2, database_url=st.database_url)
    app2 = await build_app(st2, {"anthropic": MockProvider("anthropic", scripted_company())})
    ws = tmp_path / "ws2" / f"task_{res['task_id']}"
    assert (ws / "NATIJA.md").read_text() == "FINAL DELIVERABLE"
    assert app2.tg.session_str == "SESSION-STRING" and app2.tg.configured()
    assert (await app2.store.get_task(res["task_id"]))["status"] == "done"
    await app2.tg.logout()
    assert await app2.store.get_kv("tg_session_sealed") is None
    await app2.store.close()


async def test_supabase_direct_connection_gives_clear_message(monkeypatch):
    store = Store("postgresql://postgres:p@db.abcdefgh.supabase.co:5432/postgres")

    async def boom():
        raise OSError(101, "Network is unreachable")
    monkeypatch.setattr(store, "_init", boom)
    with pytest.raises(SystemExit, match="Session pooler"):
        await store.init()
