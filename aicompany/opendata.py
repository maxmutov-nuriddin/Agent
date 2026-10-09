"""Bepul, kalitsiz ochiq ma'lumot manbalari (hech qanday to'lov va .env kerak emas):

- Overpass (OpenStreetMap): yaqin atrofdagi joylar — ish vaqti, telefon, manzil bilan
- Wikipedia / Wikidata: faktlar va raqamlar, manba havolasi bilan
- World Bank Open Data: mamlakatlar statistikasi (aholi, YaIM, inflyatsiya, internet...)
- Yangiliklar RSS: Kun.uz, Gazeta.uz, Daryo va boshqalar (RSS manzili sayt sahifasidan o'zi topiladi)

Barcha so'rovlar tools._get orqali: ichki/lokal manzillar taqiqlangan. Natijalar <untrusted_web_content> ichida.
Bepul xizmatlar cheklovli (sekin yoki band bo'lishi mumkin): xato tushunarli matn bilan qaytadi, vazifa to'xtamaydi.
"""
from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from types import SimpleNamespace
from urllib.parse import quote, urljoin, urlparse

from .tools import TOOLS, Tool, ToolError, _get, _obj, html_to_text, untrusted

OVERPASS = "https://overpass-api.de/api/interpreter"


async def _json(env, url: str, timeout: float = 20):
    r = await _get(env, url, timeout=timeout)
    if r.status_code >= 400:
        raise ToolError(f"xizmat xatosi: HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError as e:
        raise ToolError("xizmat noto'g'ri javob qaytardi") from e


# ---------- Overpass (OpenStreetMap) ----------
CATEGORIES = [
    (r"dorixona|apteka|pharmac", '["amenity"="pharmacy"]'),
    (r"kafe|cafe|qahva|kofe|coffee", '["amenity"="cafe"]'),
    (r"restoran|restaurant|oshxona|choyxona", '["amenity"="restaurant"]'),
    (r"fast ?food|lavash|burger|shaurma", '["amenity"="fast_food"]'),
    (r"bankomat|atm", '["amenity"="atm"]'),
    (r"\bbank", '["amenity"="bank"]'),
    (r"kasalxona|shifoxona|hospital|bolnitsa", '["amenity"="hospital"]'),
    (r"klinika|poliklinika|clinic", '["amenity"="clinic"]'),
    (r"stomatolog|tish|dentist", '["amenity"="dentist"]'),
    (r"maktab|school", '["amenity"="school"]'),
    (r"bog'cha|kindergarten", '["amenity"="kindergarten"]'),
    (r"supermarket|market|do'kon|magazin", '["shop"~"supermarket|convenience"]'),
    (r"zapravka|benzin|yoqilg'i|metan|propan|fuel", '["amenity"="fuel"]'),
    (r"masjid|mosque", '["amenity"="place_of_worship"]["religion"="muslim"]'),
    (r"park\b|bog'i", '["leisure"="park"]'),
    (r"mehmonxona|hotel|gostinitsa", '["tourism"="hotel"]'),
    (r"parkovka|avtoturargoh|parking", '["amenity"="parking"]'),
    (r"pochta|post", '["amenity"="post_office"]'),
    (r"sport ?zal|fitnes|fitness|gym", '["leisure"="fitness_centre"]'),
    (r"sartarosh|barber|salon", '["shop"~"hairdresser|beauty"]'),
    (r"avtoservis|ustaxona|car repair", '["shop"="car_repair"]'),
    (r"kutubxona|library", '["amenity"="library"]'),
    (r"metro", '["railway"="station"]["station"="subway"]'),
]


def osm_filter(query: str) -> str:
    q = (query or "").lower()
    for pat, flt in CATEGORIES:
        if re.search(pat, q):
            return flt
    safe = re.sub(r'["\\\[\]]', "", query)[:40]
    return f'["name"~"{safe}",i]'


async def osm_places(env, a):
    from .tools_ext import fmt_dist, haversine_m, resolve_point
    lat, lon, label = await resolve_point(env, a.get("near", "me"))
    radius = max(200, min(int(a.get("radius_m", 1500)), 10000))
    flt = osm_filter(a["query"])
    q = (f'[out:json][timeout:20];(node(around:{radius},{lat},{lon}){flt};way(around:{radius},{lat},{lon}){flt};);'
         'out center 40;')
    d = await _json(env, f"{OVERPASS}?data={quote(q)}", timeout=30)
    rows = []
    for el in d.get("elements", []):
        t = el.get("tags") or {}
        p = (el.get("lat"), el.get("lon")) if "lat" in el else ((el.get("center") or {}).get("lat"), (el.get("center") or {}).get("lon"))
        if p[0] is None:
            continue
        name = t.get("name:uz") or t.get("name") or t.get("brand") or "(nomsiz)"
        addr = " ".join(x for x in (t.get("addr:street"), t.get("addr:housenumber")) if x)
        extra = [f"ish vaqti: {t['opening_hours']}" if t.get("opening_hours") else "",
                 f"tel: {t.get('phone') or t.get('contact:phone')}" if (t.get("phone") or t.get("contact:phone")) else "",
                 t.get("website") or t.get("contact:website") or ""]
        rows.append((haversine_m((lat, lon), (float(p[0]), float(p[1]))), name, addr, " · ".join(x for x in extra if x)))
    rows.sort(key=lambda r: r[0])
    limit = min(int(a.get("limit", 8)), 15)
    if not rows:
        return untrusted(f"OpenStreetMap: '{a['query']}' {label} atrofida ({fmt_dist(radius)}) topilmadi")
    lines = [f"- {n} ({fmt_dist(dm)}){' · ' + ad if ad else ''}{chr(10) + '  ' + ex if ex else ''}" for dm, n, ad, ex in rows[:limit]]
    return untrusted(f"OpenStreetMap (Overpass), {label} atrofida '{a['query']}':\n" + "\n".join(lines) +
                     "\nManba: openstreetmap.org (ma'lumot jamoatchilik tomonidan to'ldiriladi, eskirgan bo'lishi mumkin)")


# ---------- Wikipedia / Wikidata ----------
async def wikipedia(env, a):
    query = a["query"].strip()
    langs = [a.get("lang") or "uz"] + [x for x in ("uz", "ru", "en") if x != (a.get("lang") or "uz")]
    for lang in langs:
        d = await _json(env, f"https://{lang}.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit=3"
                             f"&srsearch={quote(query)}")
        hits = (d.get("query") or {}).get("search") or []
        if not hits:
            continue
        title = hits[0]["title"]
        s = await _json(env, f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(title.replace(' ', '_'))}")
        url = ((s.get("content_urls") or {}).get("desktop") or {}).get("page") or f"https://{lang}.wikipedia.org/wiki/{quote(title)}"
        others = ", ".join(h["title"] for h in hits[1:])
        return untrusted(f"Wikipedia ({lang}): {s.get('title', title)}\n{s.get('extract', '')}\nManba: {url}" +
                         (f"\nO'xshash maqolalar: {others}" if others else ""))
    return untrusted(f"Wikipedia: '{query}' bo'yicha maqola topilmadi (uz, ru, en)")


WD_PROPS = {"P1082": "aholi", "P2046": "maydon", "P36": "poytaxt", "P17": "mamlakat", "P571": "tashkil topgan",
            "P856": "rasmiy sayt", "P112": "asoschisi", "P1128": "xodimlar soni", "P2139": "daromad",
            "P38": "valyuta", "P35": "davlat rahbari", "P6": "hukumat rahbari", "P2131": "YaIM (nominal)", "P159": "bosh ofis"}


UNITS = {"Q712226": "km²", "Q4917": "$", "Q4916": "€", "Q487888": "so'm", "Q41044": "rubl"}


def _wd_value(snak):
    dv = (snak.get("datavalue") or {})
    v, t = dv.get("value"), dv.get("type")
    if t == "quantity":
        amt = v.get("amount", "").lstrip("+")
        try:
            num = float(amt)
            amt = f"{num:,.0f}".replace(",", " ") if abs(num) >= 1000 else amt
        except ValueError:
            pass
        unit = UNITS.get(v.get("unit", "").rsplit("/", 1)[-1], "")
        return amt + (" " + unit if unit else ""), None
    if t == "time":
        return v.get("time", "")[1:11].replace("-00", ""), None
    if t == "wikibase-entityid":
        return None, v.get("id")
    if isinstance(v, str):
        return v, None
    return None, None


async def wikidata(env, a):
    lang = a.get("lang") or "uz"
    d = await _json(env, "https://www.wikidata.org/w/api.php?action=wbsearchentities&format=json&limit=1"
                         f"&language={lang}&uselang={lang}&search={quote(a['query'])}")
    if not d.get("search"):
        d = await _json(env, f"https://www.wikidata.org/w/api.php?action=wbsearchentities&format=json&limit=1&language=en&search={quote(a['query'])}")
    if not d.get("search"):
        return untrusted(f"Wikidata: '{a['query']}' topilmadi")
    qid = d["search"][0]["id"]
    e = (await _json(env, f"https://www.wikidata.org/w/api.php?action=wbgetentities&format=json&ids={qid}"
                          f"&props=labels|descriptions|claims&languages={lang}|en"))["entities"][qid]
    label = ((e.get("labels") or {}).get(lang) or (e.get("labels") or {}).get("en") or {}).get("value", qid)
    desc = ((e.get("descriptions") or {}).get(lang) or (e.get("descriptions") or {}).get("en") or {}).get("value", "")
    facts, need = [], {}
    for pid, name in WD_PROPS.items():
        claims = (e.get("claims") or {}).get(pid) or []
        if not claims:
            continue
        best = [c for c in claims if c.get("rank") == "preferred"] or claims
        c = best[-1]
        val, ref = _wd_value(c.get("mainsnak") or {})
        when = ""
        for q in ((c.get("qualifiers") or {}).get("P585") or []):
            year = _wd_value(q)[0]
            when = f" ({year[:4]})" if year else ""
        if ref:
            need[ref] = (name, when)
        elif val:
            facts.append(f"- {name}: {val}{when}")
    if need:
        labs = (await _json(env, "https://www.wikidata.org/w/api.php?action=wbgetentities&format=json&props=labels"
                                 f"&languages={lang}|en&ids={'|'.join(list(need)[:30])}")).get("entities", {})
        for ref, (name, when) in need.items():
            lb = ((labs.get(ref) or {}).get("labels") or {})
            facts.append(f"- {name}: {(lb.get(lang) or lb.get('en') or {}).get('value', ref)}{when}")
    return untrusted(f"Wikidata: {label} — {desc}\n" + ("\n".join(facts) or "(asosiy raqamlar yo'q)") +
                     f"\nManba: https://www.wikidata.org/wiki/{qid}")


# ---------- World Bank ----------
WB = {"aholi": "SP.POP.TOTL", "yaim": "NY.GDP.MKTP.CD", "yaim_jon": "NY.GDP.PCAP.CD", "yaim_osish": "NY.GDP.MKTP.KD.ZG",
      "inflyatsiya": "FP.CPI.TOTL.ZG", "ishsizlik": "SL.UEM.TOTL.ZS", "internet": "IT.NET.USER.ZS",
      "mobil": "IT.CEL.SETS.P2", "eksport": "NE.EXP.GNFS.CD", "import": "NE.IMP.GNFS.CD",
      "shahar_aholi": "SP.URB.TOTL.IN.ZS", "pul_otkazmalari": "BX.TRF.PWKR.DT.GD.ZS", "xorijiy_investitsiya": "BX.KLT.DINV.CD.WD"}


async def world_bank(env, a):
    ind = str(a.get("indicator", "")).strip()
    code = WB.get(ind.lower(), ind)
    if not re.fullmatch(r"[A-Za-z0-9.]{3,40}", code):
        raise ToolError("ko'rsatkich: " + ", ".join(WB) + " yoki World Bank kodi (masalan SP.POP.TOTL)")
    country = re.sub(r"[^A-Za-z;]", "", str(a.get("country") or "UZ"))[:30] or "UZ"
    years = max(1, min(int(a.get("years", 6)), 20))
    d = await _json(env, f"https://api.worldbank.org/v2/country/{country}/indicator/{code}?format=json&mrnev={years}&per_page=100")
    if not isinstance(d, list) or len(d) < 2 or not d[1]:
        msg = ((d[0].get("message") or [{}])[0].get("value") if isinstance(d, list) and d and isinstance(d[0], dict) else "") or "ma'lumot yo'q"
        return untrusted(f"World Bank: {code} / {country}: {msg}")
    name = (d[1][0].get("indicator") or {}).get("value", code)
    lines = []
    for r in d[1]:
        v = r.get("value")
        if v is None:
            continue
        val = f"{v:,.0f}".replace(",", " ") if abs(v) >= 1000 else f"{v:.2f}"
        lines.append(f"- {(r.get('country') or {}).get('value', r.get('countryiso3code'))} {r.get('date')}: {val}")
    return untrusted(f"World Bank: {name} ({code})\n" + "\n".join(lines) +
                     f"\nManba: https://data.worldbank.org/indicator/{code}?locations={country.split(';')[0]}")


# ---------- Yangiliklar (RSS) ----------
DEFAULT_SITES = ["kun.uz", "gazeta.uz", "daryo.uz", "uza.uz"]
FEED_PATHS = ["/rss", "/uz/rss", "/oz/rss/", "/feed", "/news/rss", "/rss.xml", "/uz/rss.xml", "/ru/rss/"]
FEED_TTL = 7 * 86400


async def news_sites(store) -> list[str]:
    raw = await store.get_kv("news_sites")
    sites = [s.strip().lower() for s in (raw or "").split(",") if s.strip()] if raw is not None else DEFAULT_SITES
    return [re.sub(r"^https?://", "", s).strip("/") for s in sites][:10]


def parse_feed(xml: str, source: str) -> list[dict]:
    try:
        root = ET.fromstring(xml.encode() if isinstance(xml, str) else xml)
    except ET.ParseError:
        return []
    out = []
    atom = "{http://www.w3.org/2005/Atom}"
    for it in root.iter("item"):
        out.append({"title": (it.findtext("title") or "").strip(), "link": (it.findtext("link") or "").strip(),
                    "date": (it.findtext("pubDate") or "").strip(), "source": source})
    for it in root.iter(atom + "entry"):
        link = it.find(atom + "link")
        out.append({"title": (it.findtext(atom + "title") or "").strip(), "link": link.get("href", "") if link is not None else "",
                    "date": (it.findtext(atom + "updated") or it.findtext(atom + "published") or "").strip(), "source": source})
    for x in out:
        try:
            x["ts"] = parsedate_to_datetime(x["date"]).timestamp() if "," in x["date"] else datetime.fromisoformat(x["date"].replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            x["ts"] = 0
        x["title"] = html_to_text(x["title"])[:200]
    return [x for x in out if x["title"] and x["link"].startswith(("http://", "https://"))]


def _looks_like_feed(text: str) -> bool:
    head = text.lstrip()[:500].lower()
    return head.startswith("<?xml") or "<rss" in head or "<feed" in head


async def discover_feed(env, site: str) -> str | None:
    """Saytning RSS manzili: avval bosh sahifadagi <link rel=alternate>, keyin keng tarqalgan yo'llar."""
    base = "https://" + site.split("/")[0]
    if "/" in site:   # foydalanuvchi to'liq manzil bergan bo'lishi mumkin
        try:
            r = await _get(env, "https://" + site, timeout=12)
            if r.status_code < 400 and _looks_like_feed(r.text):
                return "https://" + site
        except ToolError:
            pass
    try:
        r = await _get(env, base + "/", timeout=12)
        if r.status_code < 400:
            for m in re.finditer(r"<link[^>]+>", r.text[:200_000], re.I):
                tag = m.group(0)
                if re.search(r"application/(rss|atom)\+xml", tag, re.I):
                    href = re.search(r'href=["\']([^"\']+)', tag)
                    if href:
                        return urljoin(base + "/", href.group(1))
    except ToolError:
        pass
    for p in FEED_PATHS:
        try:
            r = await _get(env, base + p, timeout=10)
            if r.status_code < 400 and _looks_like_feed(r.text):
                return base + p
        except ToolError:
            continue
    return None


async def feed_url(env, store, site: str) -> str | None:
    key = f"news_feed:{site}"
    try:
        cached = json.loads(await store.get_kv(key) or "{}")
    except ValueError:
        cached = {}
    if cached and time.time() - cached.get("ts", 0) < FEED_TTL:
        return cached.get("url")
    url = await discover_feed(env, site)
    await store.set_kv(key, json.dumps({"url": url, "ts": time.time()}))
    return url


async def latest_news(env, store, limit: int = 10, query: str = "") -> tuple[list[dict], dict]:
    """Barcha manbalardan oxirgi yangiliklar (yangi birinchi) va har sayt holati {sayt: "ok N" | "rss topilmadi" | "xato"}."""
    items, status = [], {}
    for site in await news_sites(store):
        try:
            url = await feed_url(env, store, site)
            if not url:
                status[site] = "RSS topilmadi"
                continue
            r = await _get(env, url, timeout=15)
            got = parse_feed(r.text[:3_000_000], urlparse(url).hostname or site) if r.status_code < 400 else []
            status[site] = f"✅ {len(got)} ta" if got else f"bo'sh (HTTP {r.status_code})"
            items += got
        except ToolError as e:
            status[site] = f"xato: {str(e)[:60]}"
    if query:
        words = [w for w in re.findall(r"\w{3,}", query.lower())]
        items = [x for x in items if any(w in x["title"].lower() for w in words)]
    items.sort(key=lambda x: x["ts"], reverse=True)
    seen, out = set(), []
    for x in items:
        k = x["title"].lower()[:60]
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out[:limit], status


async def news(env, a):
    items, status = await latest_news(env, env.store, min(int(a.get("limit", 10)), 25), a.get("query", ""))
    if not items:
        return untrusted("Yangilik topilmadi. Manbalar holati: " + "; ".join(f"{k}: {v}" for k, v in status.items()))
    return untrusted("Oxirgi yangiliklar (RSS):\n" + "\n".join(f"- [{x['source']}] {x['title']}\n  {x['link']}" for x in items))


def plain_env():
    """Asbobdan tashqarida (ertalabki xulosa, panel) foydalanish uchun minimal muhit."""
    return SimpleNamespace(http=None)


TOOLS.update({t.name: t for t in [
    Tool("osm_places", "maps", "Find places near a point from OpenStreetMap (free): pharmacy, cafe, bank, atm, hospital, fuel, "
         "mosque, supermarket... with opening hours, phone and address when known. near: 'me', a saved place, an address or 'lat,lon'.",
         _obj({"query": {"type": "string"}, "near": {"type": "string"}, "radius_m": {"type": "integer"}, "limit": {"type": "integer"}}, ["query"]),
         osm_places),
    Tool("wikipedia", "web", "Search Wikipedia (uz, then ru, en) and return the article summary with its URL to cite.",
         _obj({"query": {"type": "string"}, "lang": {"type": "string"}}, ["query"]), wikipedia),
    Tool("wikidata", "web", "Structured facts about a country, city, company or person from Wikidata (population, area, capital, "
         "founded, website, employees, revenue...) with the source URL.",
         _obj({"query": {"type": "string"}, "lang": {"type": "string"}}, ["query"]), wikidata),
    Tool("world_bank", "web", "Official country statistics from World Bank Open Data with source URL. indicator: one of " +
         ", ".join(WB) + " or a World Bank code; country: ISO code(s) like UZ or UZ;KZ;KG (default UZ); years: how many recent years.",
         _obj({"indicator": {"type": "string"}, "country": {"type": "string"}, "years": {"type": "integer"}}, ["indicator"]), world_bank),
    Tool("news", "web", "Latest Uzbekistan news headlines with links from RSS feeds (Kun.uz, Gazeta.uz, Daryo...). "
         "query: optional keywords to filter.",
         _obj({"query": {"type": "string"}, "limit": {"type": "integer"}}, []), news),
]})
