import json
from types import SimpleNamespace

from aicompany import watch as W

from .conftest import scripted_company
from .test_web import TOK, get, post, web  # noqa: F401  (fixture)


def test_price_parsing_and_extraction():
    assert W.parse_price("12 990 000 so'm") == 12990000
    assert W.parse_price("1,299.99") == 1299.99
    assert W.parse_price("12.990.000") == 12990000
    assert W.parse_price("1 299,50 ₽") == 1299.5
    assert W.parse_price("narx yo'q") is None
    ld = '<script type="application/ld+json">{"@type":"Product","offers":{"@type":"Offer","price":"8499000","priceCurrency":"UZS"}}</script>'
    assert W.structured_price(ld) == 8499000
    assert W.structured_price('<meta property="product:price:amount" content="199.90">') == 199.9
    assert W.structured_price("<p>hech narsa</p>") is None
    text = W.page_text("<div>iPhone 15</div><span>Narxi:</span> <b>11 500 000</b> so'm")
    assert W.price_after(text, "Narxi:") == 11500000


class FakeResp:
    def __init__(self, status, text):
        self.status_code, self.text = status, text


def fake_http(monkeypatch, pages):
    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def get(self, url, **kw):
            return pages.get(url, FakeResp(404, ""))
    monkeypatch.setattr(W.httpx, "AsyncClient", Client)


async def test_price_watch_detects_drop_and_respects_robots(make_app, monkeypatch):
    app, _ = await make_app(scripted_company())
    url = "https://shop.example/p/1"
    page = lambda p: FakeResp(200, f'<script type="application/ld+json">{{"offers":{{"price":"{p}"}}}}</script>')  # noqa: E731
    pages = {"https://shop.example/robots.txt": FakeResp(200, "User-agent: *\nAllow: /"), url: page(10000000)}
    fake_http(monkeypatch, pages)
    wid = await app.store.add_watch(kind="price", title="iPhone", target=url, target_price=9000000)
    w = await app.store.get_watch(wid)
    assert await app.watch.check(w) == []                                       # birinchi narx: faqat eslab qolinadi
    pages[url] = page(9500000)
    hits = await app.watch.check(await app.store.get_watch(wid))
    assert hits and "10 000 000 → 9 500 000" in hits[0]
    pages[url] = page(9500000)
    assert await app.watch.check(await app.store.get_watch(wid)) == []          # o'zgarmadi: xabar yo'q
    pages["https://shop.example/robots.txt"] = FakeResp(200, "User-agent: *\nDisallow: /p/")
    assert await app.watch.check(await app.store.get_watch(wid)) == []
    assert "robots.txt" in (await app.store.get_watch(wid))["last_error"]


async def test_price_without_structured_data_needs_smart_mode(make_app, monkeypatch):
    def handler(system, user, model):
        if "CURRENT selling price" in system:
            return json.dumps({"price_text": "7 200 000 so'm", "before": "Narxi:"})
        return scripted_company()(system, user, model)
    app, provs = await make_app(handler)
    url = "https://shop.example/p/2"
    pages = {url: FakeResp(200, "<span>Narxi:</span> <b>7 200 000</b> so'm")}
    fake_http(monkeypatch, pages)
    wid = await app.store.add_watch(kind="price", title="TV", target=url)
    await app.watch.check(await app.store.get_watch(wid))
    assert "Aqlli kuzatuv" in (await app.store.get_watch(wid))["last_error"]
    await app.store.set_kv("watch_smart", "1")
    await app.watch.check(await app.store.get_watch(wid))
    st = json.loads((await app.store.get_watch(wid))["state"])
    assert st["price"] == 7200000 and st["anchor"] == "Narxi:"
    n = len(provs["anthropic"].calls)
    pages[url] = FakeResp(200, "<span>Narxi:</span> <b>6 900 000</b> so'm")
    hits = await app.watch.check(await app.store.get_watch(wid))
    assert hits and len(provs["anthropic"].calls) == n                          # keyingi tekshiruv AI'siz (eslab qolingan joydan)


class FakeTg:
    def __init__(self, msgs):
        self.msgs = msgs

    def configured(self):
        return True

    async def resolve(self, name):
        return SimpleNamespace(username="uzjobs")

    async def client(self):
        tg = self

        class C:
            async def get_messages(self, ent, limit=10, min_id=0):
                return [m for m in sorted(tg.msgs, key=lambda m: -m.id) if m.id > min_id][:limit]
        return C()


def msg(i, text):
    return SimpleNamespace(id=i, message=text)


async def test_tg_watch_keywords_free_and_smart_filter(make_app):
    seen = []

    def handler(system, user, model):
        if "filter Telegram channel posts" in system:
            seen.append(user)
            return json.dumps({"match": [0]})
        return scripted_company()(system, user, model)
    app, _ = await make_app(handler)
    app.tg = FakeTg([msg(1, "eski post frontend")])
    wid = await app.store.add_watch(kind="tg", title="@uzjobs", target="@uzjobs", keywords="frontend, react",
                                    description="Toshkentda junior frontend ish")
    assert await app.watch.check(await app.store.get_watch(wid)) == []          # birinchi marta: eski postlar yuborilmaydi
    app.tg.msgs += [msg(2, "Senior Java dasturchi kerak"), msg(3, "Junior Frontend (React) Toshkent, 800$"), msg(4, "Frontend kursi 50% chegirma")]
    hits = await app.watch.check(await app.store.get_watch(wid))
    assert len(hits) == 2 and "t.me/uzjobs/3" in hits[0] and not seen          # kalit so'z rejimi: AI yo'q
    await app.store.set_kv("watch_smart", "1")
    app.tg.msgs += [msg(5, "Junior frontend vakansiya, ofis Chilonzor"), msg(6, "Frontend bo'yicha vebinar")]
    hits = await app.watch.check(await app.store.get_watch(wid))
    assert len(seen) == 1 and len(hits) == 1 and "vakansiya" in hits[0]         # aqlli rejim: AI faqat moslarini saraladi
    assert len(await app.store.watch_hits_recent()) == 3


async def test_watch_api_and_chat_creation(web):
    c, app = web
    assert (await post(c, "/api/watches", {"kind": "price", "target": "shop.uz/x"}))[0] == 400
    code, w = await post(c, "/api/watches", {"kind": "tg", "target": "@uzjobs", "keywords": "frontend"})
    assert code == 200 and w["title"] == "@uzjobs" and w["enabled"]
    code, w2 = await post(c, f"/api/watches/{w['id']}", {"enabled": False, "keywords": "react"})
    assert not w2["enabled"] and w2["keywords"] == "react"
    lst = (await get(c, "/api/watches"))[1]
    assert lst["watches"][0]["id"] == w["id"] and lst["hits"] == []
    assert (await post(c, "/api/watch_smart", {"enabled": True}))[1] == {"watch_smart": True}
    assert (await get(c, "/api/state"))[1]["watch_smart"] is True
    assert (await c.delete(f"/api/watches/{w['id']}", headers=TOK)).status == 200
    title = await app.orch._save_watch({"kind": "price", "target": "https://shop.uz/p/9", "target_price": "500000"})
    assert title == "Narx: shop.uz" and (await app.store.list_watches())[0]["target_price"] == 500000


async def test_kind_master_switch_stops_all_watches_of_that_kind(make_app, monkeypatch, web):
    c, _ = web
    app, _ = await make_app(scripted_company())
    checked = []

    async def fake_check(w):
        checked.append(w["kind"])
        return []
    app.watch.check = fake_check
    await app.store.add_watch(kind="tg", title="a", target="@a")
    await app.store.add_watch(kind="price", title="b", target="https://x.uz/p")
    await app.store.set_kv("watch_on:price", "0")
    import asyncio
    t = asyncio.create_task(app.watch.loop([], tick=0.01))
    await asyncio.sleep(0.05)
    t.cancel()
    assert checked == ["tg"]                                                     # narx kuzatuvi butunlay o'chiq
    assert (await post(c, "/api/watch_kind", {"kind": "tg", "enabled": False}))[1] == {"kind": "tg", "enabled": False}
    assert (await get(c, "/api/state"))[1]["watch_on"] == {"tg": False, "price": True}
    assert (await post(c, "/api/watch_kind", {"kind": "x", "enabled": False}))[0] == 400
