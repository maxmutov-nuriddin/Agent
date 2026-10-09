"""GitHub ochiq API: tarmoqsiz, soxta transport orqali."""
import base64
import json
from types import SimpleNamespace as NS

import httpx
import pytest

from aicompany import github_api
from aicompany.tools import ToolError
from .test_web import get, post, web  # noqa: F401  (fixture)


class KV:
    def __init__(self):
        self.d = {}

    async def get_kv(self, k, default=None):
        return self.d.get(k, default)

    async def set_kv(self, k, v):
        self.d[k] = v

    async def delete_kv(self, k):
        self.d.pop(k, None)


def env_with(handler, store=None):
    return NS(http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), store=store or KV(),
              settings=NS(secret_key="s3cret", web_token="t"))


async def test_search_repos_and_repo():
    seen = []

    def h(req):
        seen.append(req)
        if req.url.path == "/search/repositories":
            return httpx.Response(200, json={"total_count": 1, "items": [{"full_name": "a/b", "stargazers_count": 5, "language": "Python",
                                                                        "description": "x", "html_url": "https://github.com/a/b"}]})
        return httpx.Response(200, json={"full_name": "a/b", "stargazers_count": 5, "forks_count": 1, "open_issues_count": 0,
                                         "description": "d", "language": "Python", "license": None, "pushed_at": "2026-01-01",
                                         "default_branch": "main", "topics": [], "html_url": "https://github.com/a/b"})
    env = env_with(h)
    out = await github_api.github(env, {"action": "search_repos", "query": "bot"})
    assert "a/b ★5" in out and "untrusted" in out
    assert "authorization" not in seen[0].headers                      # token yo'q: sarlavha yuborilmaydi
    assert "a/b" in await github_api.github(env, {"action": "repo", "repo": "https://github.com/a/b"})


async def test_file_and_tree_and_bad_input():
    def h(req):
        if req.url.path.endswith("/contents/"):
            return httpx.Response(200, json=[{"type": "dir", "path": "src"}, {"type": "file", "path": "README.md"}])
        return httpx.Response(200, json={"encoding": "base64", "content": base64.b64encode(b"print(1)").decode(), "html_url": "u"})
    env = env_with(h)
    assert "📁 src" in await github_api.github(env, {"action": "tree", "repo": "a/b"})
    assert "print(1)" in await github_api.github(env, {"action": "file", "repo": "a/b", "path": "x.py"})
    for bad in ({"action": "file", "repo": "a/b", "path": "../etc"}, {"action": "repo", "repo": "a/b;rm"}, {"action": "nope"}):
        with pytest.raises(ToolError):
            await github_api.github(env, bad)


async def test_rate_limit_and_not_found_messages():
    env = env_with(lambda r: httpx.Response(403, headers={"x-ratelimit-remaining": "0"}))
    with pytest.raises(ToolError, match="limiti tugadi"):
        await github_api.github(env, {"action": "repo", "repo": "a/b"})
    env = env_with(lambda r: httpx.Response(404))
    with pytest.raises(ToolError, match="topilmadi"):
        await github_api.github(env, {"action": "repo", "repo": "a/b"})


async def test_token_is_sealed_and_sent_only_when_set():
    store = KV()
    env = env_with(lambda r: httpx.Response(200, json=[], headers={}), store)
    await github_api.set_token(store, env.settings, "ghp_" + "a" * 30)
    assert "ghp_" not in store.d["github_token_sealed"]
    assert await github_api.get_token(store, env.settings) == "ghp_" + "a" * 30
    got = {}

    def h(req):
        got["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json=[])
    env = env_with(h, store)
    await github_api.github(env, {"action": "commits", "repo": "a/b"})
    assert got["auth"] == "Bearer ghp_" + "a" * 30


async def test_github_endpoint_validates_and_hides_token(web):
    c, app = web
    assert (await post(c, "/api/github", {"token": "bad token!"}))[0] == 400
    code, d = await post(c, "/api/github", {"token": "ghp_" + "b" * 30})
    assert code == 200 and d["token"] is True
    st = (await get(c, "/api/state"))[1]
    assert st["github"] is True and "ghp_" not in json.dumps(st)
    assert (await post(c, "/api/github", {"token": ""}))[1]["token"] is False


def test_tool_registered():
    from aicompany.tools import TOOLS
    assert TOOLS["github"].group == "web"
