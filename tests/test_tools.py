import os

import httpx
import pytest

from aicompany.approvals import AutoApprover
from aicompany.providers import LLMResult
from aicompany.tools import TOOLS, ToolEnv, ToolError

from .conftest import scripted_company


@pytest.fixture
async def env(make_app, tmp_path):
    app, _ = await make_app(lambda *a: "ok")
    return ToolEnv(workspace=tmp_path, store=app.store, settings=app.settings, task_id=1, agent="dev",
                   approver=AutoApprover(True))


async def call(env, name, **args):
    return await TOOLS[name].handler(env, args)


async def test_files_roundtrip_and_listing(env):
    await call(env, "write_file", path="site/index.html", content="<h1>hi</h1>")
    assert "<h1>hi</h1>" in await call(env, "read_file", path="site/index.html")
    assert "site/index.html" in await call(env, "list_files")


@pytest.mark.parametrize("bad", ["../escape.txt", "/etc/passwd", "a/../../x"])
async def test_path_escape_blocked(env, bad):
    with pytest.raises(ToolError):
        await call(env, "write_file", path=bad, content="x")
    with pytest.raises(ToolError):
        await call(env, "read_file", path=bad)


async def test_symlink_escape_blocked(env, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (env.workspace / "link").symlink_to(outside)
    with pytest.raises(ToolError):
        await call(env, "write_file", path="link/pwned.txt", content="x")
    assert not (outside / "pwned.txt").exists()


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://169.254.169.254/latest/meta-data", "http://10.0.0.5/",
                                 "http://[::1]/", "file:///etc/passwd", "ftp://example.com/"])
async def test_ssrf_blocked(env, url):
    with pytest.raises(ToolError):
        await call(env, "fetch_url", url=url)


async def test_redirect_to_private_is_blocked(env):
    def h(req):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/secret"})
    env.http = httpx.AsyncClient(transport=httpx.MockTransport(h))
    with pytest.raises(ToolError):
        await call(env, "fetch_url", url="http://93.184.216.34/")


async def test_fetch_wraps_content_as_untrusted(env):
    env.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(
        200, headers={"content-type": "text/html"},
        text="<html><script>evil()</script><body><p>Hello &amp; welcome</p></body></html>")))
    out = await call(env, "fetch_url", url="http://93.184.216.34/")
    assert out.startswith("<untrusted_web_content>") and "Hello & welcome" in out and "evil" not in out


async def test_brave_search_parsing(env):
    env.settings = type("S", (), {"brave_key": "k"})()
    def h(req):
        assert req.headers["x-subscription-token"] == "k"
        return httpx.Response(200, json={"web": {"results": [{"title": "T", "url": "http://x.io", "description": "<b>D</b>"}]}})
    env.http = httpx.AsyncClient(transport=httpx.MockTransport(h))
    out = await call(env, "web_search", query="q")
    assert "T" in out and "http://x.io" in out and "<untrusted_web_content>" in out


async def test_run_command_requires_and_respects_approval(env):
    env.approver = AutoApprover(False)
    with pytest.raises(ToolError):
        await call(env, "run_command", command="echo hi")
    env.approver = AutoApprover(True)
    assert "hi" in await call(env, "run_command", command="echo hi")
    assert env.approver.asked == ["echo hi"]


async def test_run_command_does_not_leak_secrets(env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-123")
    out = await call(env, "run_command", command="env")
    assert "sk-secret-123" not in out and "ANTHROPIC" not in out


async def test_run_command_timeout(env):
    env.settings = type("S", (), {"command_timeout": 1})()
    with pytest.raises(ToolError):
        await call(env, "run_command", command="sleep 5")


async def test_run_command_cwd_is_workspace(env):
    out = await call(env, "run_command", command="pwd")
    assert str(env.workspace.resolve()) in out


async def test_memory_tools(env):
    await call(env, "remember", text="Owner sells coffee in Tashkent")
    assert "coffee" in await call(env, "recall", query="coffee shop")
    assert "topilmadi" in await call(env, "recall", query="unrelatedxyz")


# ---- asboblar sikli (Team.run_agent) ----
def tool_script(calls_then_text):
    """Mock: ketma-ket asbob chaqiruvlari, so'ng matn."""
    seq = list(calls_then_text)

    def handler(system, user, model):
        if "durable facts" in system or "'ceo'" in system or "'qa'" in system or "'hr'" in system:
            return scripted_company()(system, user, model)
        step = seq.pop(0)
        if isinstance(step, str):
            return step
        name, args = step
        return LLMResult("", 10, 5, 0, 0, [{"id": f"t{len(seq)}", "name": name, "input": args}], [{"type": "tool_use"}])
    return handler


async def test_agent_tool_loop_writes_file(make_app, tmp_path):
    app, provs = await make_app(tool_script([("write_file", {"path": "a.txt", "content": "X"}), "done"]))
    env = ToolEnv(workspace=tmp_path, store=app.store, settings=app.settings, task_id=None)
    out = await app.team.run_agent("developer", "make file", env=env)
    assert out == "done" and (tmp_path / "a.txt").read_text() == "X"
    assert "write_file" in provs["anthropic"].tool_seen[0] and "run_command" in provs["anthropic"].tool_seen[0]


async def test_agent_without_env_gets_no_tools(make_app):
    app, provs = await make_app(tool_script(["plain"]))
    assert await app.team.run_agent("developer", "x") == "plain"
    assert provs["anthropic"].tool_seen == [[]]


async def test_tool_errors_are_returned_to_agent_not_raised(make_app, tmp_path):
    seen = []

    def handler(system, user, model):
        if "durable facts" in system:
            return "{}"
        if not seen:
            seen.append(1)
            return LLMResult("", 1, 1, 0, 0, [{"id": "a", "name": "read_file", "input": {"path": "../../etc/passwd"}},
                                                  {"id": "b", "name": "nonexistent", "input": {}},
                                                  {"id": "c", "name": "write_file", "input": {"oops": 1}}], [])
        seen.append(user)
        return "recovered"
    app, _ = await make_app(handler)
    env = ToolEnv(workspace=tmp_path, store=app.store, settings=app.settings)
    assert await app.team.run_agent("developer", "x", env=env) == "recovered"
    results = seen[1]
    assert all(r.get("is_error") for r in results) and len(results) == 3


async def test_tool_turn_limit_stops_endless_loops(make_app, tmp_path):
    def handler(system, user, model):
        return LLMResult("partial", 1, 1, 0, 0, [{"id": "x", "name": "list_files", "input": {}}], [])
    app, provs = await make_app(handler, MAX_TOOL_TURNS="3")
    env = ToolEnv(workspace=tmp_path, store=app.store, settings=app.settings)
    out = await app.team.run_agent("developer", "x", env=env)
    assert out == "partial" and len(provs["anthropic"].calls) == 4


async def test_agents_without_tool_groups_get_none(make_app, tmp_path):
    app, provs = await make_app(scripted_company())
    env = ToolEnv(workspace=tmp_path, store=app.store, settings=app.settings)
    await app.team.run_agent("ceo", "plan", env=env)
    assert provs["anthropic"].tool_seen[-1] == []


async def test_approval_rows_and_audit_recorded(env):
    import sqlalchemy as sa
    env.approver = AutoApprover(False)
    try:
        await call(env, "run_command", command="rm -rf /")
    except ToolError:
        pass
    env.approver = AutoApprover(True)
    await call(env, "run_command", command="echo ok")
    rows = await env.store._all(sa.text("select description, status from approvals order by id"))
    assert [(r["description"], r["status"]) for r in rows] == [("rm -rf /", "denied"), ("echo ok", "approved")]
    audit = await env.store._all(sa.text("select detail from audit_log where action='run_command' order by id"))
    assert audit[0]["detail"].startswith("DENIED") and audit[1]["detail"].startswith("APPROVED")
