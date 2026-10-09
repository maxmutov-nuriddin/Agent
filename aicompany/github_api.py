"""GitHub ochiq API (bepul). Kalitsiz: faqat ochiq repolar, soatiga 60 so'rov.
Ixtiyoriy token (panelda kiritiladi, bazada shifrlanadi): limit 5000/soat; token egasining yopiq repolari ham ko'rinadi.
Token faqat api.github.com ga yuboriladi. Natijalar <untrusted_web_content> ichida."""
from __future__ import annotations

import base64
import re

import httpx

from .persist import seal, unseal
from .tools import TOOLS, Tool, ToolError, _obj, untrusted

API = "https://api.github.com"
KV = "github_token_sealed"
MAX_FILE = 12000
SLUG = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
ACTIONS = ("search_repos", "repo", "readme", "tree", "file", "issues", "releases", "commits", "user_repos", "search_issues")


def _secret(settings) -> str | None:
    return getattr(settings, "secret_key", None) or getattr(settings, "web_token", None)


async def get_token(store, settings) -> str | None:
    return unseal(await store.get_kv(KV), _secret(settings))


async def set_token(store, settings, token: str | None):
    if token:
        await store.set_kv(KV, seal(token, _secret(settings)))
    else:
        await store.delete_kv(KV)


def _slug(v: str, what: str) -> str:
    v = (v or "").strip()
    if not SLUG.match(v):
        raise ToolError(f"{what} noto'g'ri")
    return v


def _repo(a) -> str:
    r = (a.get("repo") or "").strip().removeprefix("https://github.com/").strip("/")
    owner, _, name = r.partition("/")
    return f"{_slug(owner, 'egasi')}/{_slug(name.removesuffix('.git'), 'repo nomi')}"


async def _api(env, path: str, params: dict | None = None, raw: bool = False):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "aicompany-bot/0.1", "X-GitHub-Api-Version": "2022-11-28"}
    token = await get_token(env.store, env.settings)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    client = env.http or httpx.AsyncClient(timeout=20)
    try:
        r = await client.get(API + path, params=params, headers=headers)
    except httpx.HTTPError as e:
        raise ToolError(f"GitHub so'rov xatosi: {e}") from e
    finally:
        if env.http is None:
            await client.aclose()
    if r.status_code == 404:
        raise ToolError("GitHub: topilmadi (yopiq repo bo'lishi mumkin — kalitsiz ko'rinmaydi)")
    if r.status_code in (401, 403, 429):
        if r.headers.get("x-ratelimit-remaining") == "0" or r.status_code == 429:
            raise ToolError("GitHub limiti tugadi (kalitsiz soatiga 60 ta so'rov). Keyinroq urinib ko'ring yoki panelda GitHub token kiriting")
        raise ToolError(f"GitHub ruxsat bermadi (HTTP {r.status_code})" + (" — token yaroqsiz bo'lishi mumkin" if token else ""))
    if r.status_code >= 400:
        raise ToolError(f"GitHub xatosi: HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError as e:
        raise ToolError("GitHub noto'g'ri javob qaytardi") from e


def _n(x) -> int:
    return max(1, min(int(x or 10), 30))


def _line(r: dict) -> str:
    return (f"- {r['full_name']} ★{r.get('stargazers_count', 0)} [{r.get('language') or '?'}] "
            f"{(r.get('description') or '')[:120]}\n  {r['html_url']}" + (" (yopiq)" if r.get("private") else ""))


async def github(env, a):
    act = a.get("action")
    if act not in ACTIONS:
        raise ToolError("action: " + ", ".join(ACTIONS))
    limit = _n(a.get("limit"))
    if act == "search_repos":
        d = await _api(env, "/search/repositories", {"q": a["query"], "sort": a.get("sort") or "stars", "per_page": limit})
        out = "\n".join(_line(r) for r in d.get("items", [])) or "Hech narsa topilmadi"
        return untrusted(f"Repolar (jami {d.get('total_count', 0)}):\n{out}")
    if act == "search_issues":
        d = await _api(env, "/search/issues", {"q": a["query"], "per_page": limit})
        out = "\n".join(f"- {i['title']} [{i['state']}] {i['html_url']}" for i in d.get("items", [])) or "Hech narsa topilmadi"
        return untrusted(out)
    if act == "user_repos":
        u = _slug(a.get("user", ""), "foydalanuvchi")
        d = await _api(env, f"/users/{u}/repos", {"sort": "updated", "per_page": limit})
        return untrusted("\n".join(_line(r) for r in d) or "Ochiq repo yo'q")
    repo = _repo(a)
    if act == "repo":
        r = await _api(env, f"/repos/{repo}")
        return untrusted(f"{r['full_name']} ★{r['stargazers_count']} 🍴{r['forks_count']} issue: {r['open_issues_count']}\n"
                         f"{r.get('description') or ''}\nTil: {r.get('language')} | Litsenziya: {(r.get('license') or {}).get('spdx_id')} | "
                         f"Yangilangan: {r['pushed_at']} | Asosiy tarmoq: {r['default_branch']}\nTopiklar: {', '.join(r.get('topics') or [])}\n{r['html_url']}")
    if act == "readme":
        d = await _api(env, f"/repos/{repo}/readme")
        text = base64.b64decode(d.get("content", "")).decode("utf-8", "replace")
        return untrusted(text[:MAX_FILE] + f"\nManba: {d.get('html_url')}")
    if act in ("tree", "file"):
        path = (a.get("path") or "").strip("/")
        if ".." in path.split("/"):
            raise ToolError("yo'l noto'g'ri")
        params = {"ref": a["ref"]} if a.get("ref") else None
        d = await _api(env, f"/repos/{repo}/contents/{path}", params)
        if isinstance(d, list):
            return untrusted("\n".join(f"{'📁' if x['type'] == 'dir' else '📄'} {x['path']}" for x in d[:200]))
        if d.get("encoding") != "base64":
            raise ToolError("fayl juda katta yoki o'qib bo'lmaydi")
        text = base64.b64decode(d["content"]).decode("utf-8", "replace")
        return untrusted(text[:MAX_FILE] + ("\n…(qisqartirildi)" if len(text) > MAX_FILE else "") + f"\nManba: {d.get('html_url')}")
    if act == "issues":
        d = await _api(env, f"/repos/{repo}/issues", {"state": a.get("state") or "open", "per_page": limit})
        return untrusted("\n".join(f"- #{i['number']} {i['title']} [{i['state']}]{' (PR)' if 'pull_request' in i else ''} {i['html_url']}" for i in d) or "Yo'q")
    if act == "releases":
        d = await _api(env, f"/repos/{repo}/releases", {"per_page": min(limit, 10)})
        return untrusted("\n".join(f"- {x.get('name') or x['tag_name']} ({x['published_at']}) {x['html_url']}\n  {(x.get('body') or '')[:300]}" for x in d) or "Release yo'q")
    d = await _api(env, f"/repos/{repo}/commits", {"per_page": limit})
    return untrusted("\n".join(f"- {c['sha'][:7]} {c['commit']['author']['date'][:10]} {c['commit']['message'].splitlines()[0][:100]}" for c in d))


TOOLS["github"] = Tool(
    "github", "web",
    "GitHub (free API): search_repos(query), repo, readme, tree/file(path, ref), issues(state), releases, commits, user_repos(user), "
    "search_issues(query). repo as 'owner/name'. Public repos only unless the owner added a token. Cite the URLs.",
    _obj({"action": {"type": "string", "enum": list(ACTIONS)}, "query": {"type": "string"}, "repo": {"type": "string"},
          "user": {"type": "string"}, "path": {"type": "string"}, "ref": {"type": "string"}, "state": {"type": "string"},
          "sort": {"type": "string"}, "limit": {"type": "integer"}}, ["action"]), github)
