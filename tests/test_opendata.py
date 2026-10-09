"""Bepul ochiq manbalar: Overpass, Wikipedia/Wikidata, World Bank, RSS. Tarmoqsiz: javoblar soxta transport orqali."""
import json
from pathlib import Path
from types import SimpleNamespace as NS

import httpx
import pytest

from aicompany import opendata, tools
from .conftest import scripted_company
from .test_web import post, web  # noqa: F401  (fixture)


def env_with(routes: dict, store=None):
    def handler(req: httpx.Request):
        url = str(req.url)
        for prefix, (code, body, ctype) in sorted(routes.items(), key=lambda kv: -len(kv[0])):
            if url.startswith(prefix):
                return httpx.Response(code, content=body if isinstance(body, bytes) else body.encode(), headers={"content-type": ctype})
        return httpx.Response(404, text="yo'q")
    return NS(http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), store=store, settings=None)


@pytest.fixture(autouse=True)
def no_dns(monkeypatch):
    async def ok(url):
        return None
    monkeypatch.setattr(tools, "_check_url", ok)


RSS = """<?xml version="1.0"?><rss><channel><title>Kun</title>
<item><title>Dollar kursi oshdi</title><link>https://kun.uz/news/1</link><pubDate>Fri, 09 Oct 2026 08:00:00 +0500</pubDate></item>
<item><title>Toshkentda yangi metro</title><link>https://kun.uz/news/2</link><pubDate>Fri, 09 Oct 2026 07:00:00 +0500</pubDate></item>
</channel></rss>"""
ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Dollar kursi oshdi</title>
<link href="https://gazeta.uz/a"/><updated>2026-10-09T09:00:00+05:00</updated></entry>
<entry><title>Futbol: terma jamoa g'alaba</title><link href="https://gazeta.uz/b"/><updated>2026-10-09T10:00:00+05:00</updated></entry></feed>"""


def test_osm_filter_categories():
    assert opendata.osm_filter("yaqin dorixona") == '["amenity"="pharmacy"]'
    assert opendata.osm_filter("metan zapravka") == '["amenity"="fuel"]'
    assert opendata.osm_filter('Korzinka"]') == '["name"~"Korzinka",i]'      # in'ektsiyaga qarshi tozalanadi


def test_parse_rss_and_atom():
    a = opendata.parse_feed(RSS, "kun.uz")
    b = opendata.parse_feed(ATOM, "gazeta.uz")
    assert [x["title"] for x in a] == ["Dollar kursi oshdi", "Toshkentda yangi metro"] and a[0]["ts"] > a[1]["ts"]
    assert b[1]["link"] == "https://gazeta.uz/b" and b[1]["ts"] > 0
    assert opendata.parse_feed("<html>not xml", "x") == []


async def test_news_discovers_feed_and_merges(make_app):
    app, _ = await make_app(scripted_company())
    await app.store.set_kv("news_sites", "kun.uz,gazeta.uz,nofeed.uz")
    env = env_with({
        "https://kun.uz/": (200, '<html><head><link rel="alternate" type="application/rss+xml" href="/news/rss"></head></html>', "text/html"),
        "https://kun.uz/news/rss": (200, RSS, "application/rss+xml"),
        "https://gazeta.uz/oz/rss/": (200, ATOM, "application/atom+xml"),
        "https://gazeta.uz/": (200, "<html></html>", "text/html"),
    }, app.store)
    items, status = await opendata.latest_news(env, app.store, 10)
    titles = [x["title"] for x in items]
    assert titles[0] == "Futbol: terma jamoa g'alaba" and titles.count("Dollar kursi oshdi") == 1     # yangi birinchi, takror yo'q
    assert status["kun.uz"].startswith("✅") and status["nofeed.uz"] == "RSS topilmadi"
    out = await opendata.news(env, {"query": "dollar"})
    assert "Dollar kursi oshdi" in out and "Futbol" not in out and "<untrusted_web_content>" in out


async def test_world_bank_parses_and_cites():
    body = json.dumps([{"page": 1}, [
        {"indicator": {"value": "Population, total"}, "country": {"value": "Uzbekistan"}, "date": "2024", "value": 36361859},
        {"indicator": {"value": "Population, total"}, "country": {"value": "Uzbekistan"}, "date": "2023", "value": 35652307}]])
    env = env_with({"https://api.worldbank.org/v2/country/UZ/indicator/SP.POP.TOTL": (200, body, "application/json")})
    out = await opendata.world_bank(env, {"indicator": "aholi"})
    assert "Uzbekistan 2024: 36 361 859" in out and "data.worldbank.org/indicator/SP.POP.TOTL" in out
    with pytest.raises(tools.ToolError):
        await opendata.world_bank(env, {"indicator": "x; rm -rf"})


async def test_wikipedia_falls_back_to_other_language():
    env = env_with({
        "https://uz.wikipedia.org/w/api.php": (200, json.dumps({"query": {"search": []}}), "application/json"),
        "https://ru.wikipedia.org/w/api.php": (200, json.dumps({"query": {"search": [{"title": "Ташкент"}]}}), "application/json"),
        "https://ru.wikipedia.org/api/rest_v1/page/summary/": (200, json.dumps({"title": "Ташкент", "extract": "Столица Узбекистана.",
            "content_urls": {"desktop": {"page": "https://ru.wikipedia.org/wiki/Ташкент"}}}), "application/json")})
    out = await opendata.wikipedia(env, {"query": "Toshkent"})
    assert "Wikipedia (ru)" in out and "Столица" in out and "https://ru.wikipedia.org/wiki/" in out


async def test_wikidata_facts_with_labels():
    entity = {"entities": {"Q265": {"labels": {"uz": {"value": "O'zbekiston"}}, "descriptions": {"uz": {"value": "davlat"}},
        "claims": {"P1082": [{"rank": "preferred", "mainsnak": {"datavalue": {"type": "quantity", "value": {"amount": "+36361859", "unit": "1"}}},
                              "qualifiers": {"P585": [{"datavalue": {"type": "time", "value": {"time": "+2024-01-01T00:00:00Z"}}}]}}],
                   "P36": [{"rank": "normal", "mainsnak": {"datavalue": {"type": "wikibase-entityid", "value": {"id": "Q269"}}}}]}}}}
    labels = {"entities": {"Q269": {"labels": {"uz": {"value": "Toshkent"}}}}}

    def handler(req):
        u = str(req.url)
        if "wbsearchentities" in u:
            return httpx.Response(200, json={"search": [{"id": "Q265"}]})
        if "props=labels%7Cdescriptions" in u or "props=labels|descriptions" in u:
            return httpx.Response(200, json=entity)
        return httpx.Response(200, json=labels)
    env = NS(http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out = await opendata.wikidata(env, {"query": "O'zbekiston"})
    assert "aholi: 36 361 859 (2024)" in out and "poytaxt: Toshkent" in out and "wikidata.org/wiki/Q265" in out


async def test_osm_places_lists_with_hours(make_app, monkeypatch):
    from aicompany import tools_ext

    async def point(env, ref):
        return 41.31, 69.28, "Siz"
    monkeypatch.setattr(tools_ext, "resolve_point", point)
    body = json.dumps({"elements": [
        {"type": "node", "lat": 41.311, "lon": 69.281, "tags": {"name": "Dorixona 24", "opening_hours": "24/7", "phone": "+998 71 000"}},
        {"type": "way", "center": {"lat": 41.32, "lon": 69.29}, "tags": {"name": "Uzoq dorixona"}}]})
    env = env_with({opendata.OVERPASS: (200, body, "application/json")})
    out = await opendata.osm_places(env, {"query": "dorixona"})
    assert out.index("Dorixona 24") < out.index("Uzoq dorixona") and "ish vaqti: 24/7" in out and "tel: +998" in out


async def test_tools_registered_for_web_agents():
    names = {t.name for t in tools.tools_for("web")}
    assert {"wikipedia", "wikidata", "world_bank", "news"} <= names and "osm_places" in {t.name for t in tools.tools_for("maps")}


async def test_morning_news_settings_api(web, monkeypatch):
    c, app = web
    assert (await post(c, "/api/morning", {"sites": "kun.uz, gazeta.uz"}))[1]["sites"] == ["kun.uz", "gazeta.uz"]
    assert (await post(c, "/api/morning", {"sites": "kun.uz; rm -rf /"}))[0] == 400
    assert (await post(c, "/api/morning", {"news": False}))[1]["news"] is False

    async def fake_latest(env, store, limit=10, query=""):
        return [{"title": "T", "source": "kun.uz", "link": "https://kun.uz/1"}], {"kun.uz": "✅ 1 ta"}
    monkeypatch.setattr(opendata, "latest_news", fake_latest)
    status, d = await post(c, "/api/news/check")
    assert status == 200 and d["status"]["kun.uz"].startswith("✅")


async def test_briefing_includes_news(make_app, monkeypatch):
    from aicompany import briefing
    app, _ = await make_app(scripted_company())

    async def fake_latest(env, store, limit=10, query=""):
        return [{"title": "Muhim yangilik", "source": "kun.uz", "link": "https://kun.uz/1"}], {}

    async def no_net(*a, **kw):
        raise RuntimeError("tarmoq yo'q")
    monkeypatch.setattr(opendata, "latest_news", fake_latest)
    monkeypatch.setattr(briefing, "weather", no_net)
    monkeypatch.setattr(briefing, "rates", no_net)
    _, full = await briefing.build(app)
    assert "📰 Yangiliklar" in full and "Muhim yangilik" in full
    await app.store.set_kv("morning_news", "0")
    _, full = await briefing.build(app)
    assert "Yangiliklar" not in full
