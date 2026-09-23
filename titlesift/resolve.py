"""Confirm a title against free title databases.

The sweep names titles; it cannot tell a real show from a phrase shaped like
one, and it cannot tell that "Shingeki no Kyojin" and "Attack on Titan" are
the same work. Three free sources can:

  AniList    anime, manga, manhwa, manhua, donghua  (GraphQL, no key)
  MangaDex   manga and manhwa, as a fallback        (REST, no key)
  Wikipedia  live-action dramas and films           (action API, no key)

Every response is cached to disk, so a report rebuild costs no requests and
the lookups are reproducible. Wikipedia rate-limits the shared egress IP
hard, so requests are spaced and retried with backoff rather than batched.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from rapidfuzz import fuzz

UA = "titlesift/0.2 (title demand research; https://claude.ai/code)"

# Seconds between requests to one host.
_SPACING = {"graphql.anilist.co": 2.1, "api.mangadex.org": 0.4,
            "en.wikipedia.org": 6.0}
_last: dict[str, float] = {}
# Batch fetches can afford to wait out a rate limit; a person waiting on the
# page cannot. The web app lowers these so one 429 costs seconds, not minutes.
MAX_TRIES = 99
MAX_WAIT = 60.0


@dataclass
class Entity:
    source: str                 # anilist | mangadex | wikipedia
    id: str
    name: str                   # display name, English where one exists
    names: list[str]            # every name the source knows it by
    kind: str                   # anime | donghua | manga | manhwa | manhua | novel | drama | film
    country: str | None = None  # ISO-ish: KR CN TW JP TH IN TR MX ES ...
    year: int | None = None
    url: str = ""
    spanish: bool = False       # Spanish-language production
    rank: int = 0               # position in the source's search results
    searched: bool = False      # found by full-text search, not by its exact title
    popularity: int = 0

    def to_dict(self) -> dict:
        return dict(self.__dict__)


class Cache:
    def __init__(self, path: str):
        self.path = path
        self.data: dict = {}
        if os.path.exists(path):
            with open(path) as f:
                self.data = json.load(f)
        self._dirty = 0

    def get(self, key):
        return self.data.get(key)

    def put(self, key, value):
        self.data[key] = value
        self._dirty += 1
        if self._dirty >= 10:
            self.save()

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f, ensure_ascii=False)
        os.replace(tmp, self.path)
        self._dirty = 0


def _http(url: str, data: bytes | None = None, headers: dict | None = None,
          tries: int = 6) -> dict | None:
    host = urllib.parse.urlparse(url).netloc
    delay = 10.0
    for attempt in range(min(tries, MAX_TRIES)):
        wait = _SPACING.get(host, 1.0) - (time.time() - _last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _last[host] = time.time()
        req = urllib.request.Request(url, data=data, headers={
            "User-Agent": UA, "Accept": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                ra = e.headers.get("Retry-After") if e.headers else None
                time.sleep(min(max(float(ra) if ra and ra.isdigit() else 0,
                                   min(delay, 60)), MAX_WAIT))
                delay *= 1.5
                continue
            if e.code == 404:
                return None
            raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(min(delay, MAX_WAIT))
            delay *= 2
    return None


# ----------------------------------------------------------------- AniList

_ANILIST_Q = """query($s:String){Page(perPage:8){media(search:$s){
id type format countryOfOrigin title{romaji english native} synonyms
startDate{year} popularity siteUrl}}}"""


def anilist_raw(title: str, cache: Cache) -> list | None:
    key = "anilist:" + title.casefold()
    hit = cache.get(key)
    if hit is not None:
        return hit
    body = json.dumps({"query": _ANILIST_Q, "variables": {"s": title}}).encode()
    res = _http("https://graphql.anilist.co", body,
                {"Content-Type": "application/json"})
    if res is None:
        return None                 # a failure, not an answer: do not cache
    media = ((res.get("data") or {}).get("Page") or {}).get("media") or []
    cache.put(key, media)
    return media


def anilist_entities(media: list) -> list[Entity]:
    out = []
    for i, m in enumerate(media):
        t = m.get("title") or {}
        country = m.get("countryOfOrigin")
        if m.get("type") == "ANIME":
            kind = "donghua" if country == "CN" else "anime"
        elif m.get("format") == "NOVEL":
            kind = "novel"
        else:
            kind = {"KR": "manhwa", "CN": "manhua", "TW": "manhua"}.get(country, "manga")
        names = [n for n in (t.get("english"), t.get("romaji"), t.get("native"),
                             *(m.get("synonyms") or [])) if n]
        out.append(Entity(
            source="anilist", id=f"anilist:{m['id']}",
            name=t.get("english") or t.get("romaji") or t.get("native") or "",
            names=names, kind=kind, country=country,
            year=(m.get("startDate") or {}).get("year"),
            url=m.get("siteUrl") or "", rank=i,
            popularity=m.get("popularity") or 0))
    return out


# ---------------------------------------------------------------- MangaDex

def mangadex_raw(title: str, cache: Cache) -> list | None:
    key = "mangadex:" + title.casefold()
    hit = cache.get(key)
    if hit is not None:
        return hit
    q = urllib.parse.urlencode({"title": title, "limit": 8})
    res = _http("https://api.mangadex.org/manga?" + q)
    if res is None:
        return None
    data = [{"id": d["id"],
             "title": d["attributes"].get("title") or {},
             "altTitles": d["attributes"].get("altTitles") or [],
             "lang": d["attributes"].get("originalLanguage"),
             "year": d["attributes"].get("year")}
            for d in res.get("data") or []]
    cache.put(key, data)
    return data


def mangadex_entities(data: list) -> list[Entity]:
    out = []
    for i, d in enumerate(data):
        names = list(d["title"].values())
        for alt in d["altTitles"]:
            names.extend(alt.values())
        en = d["title"].get("en") or next(
            (a["en"] for a in d["altTitles"] if "en" in a), None) or (names or [""])[0]
        country = {"ko": "KR", "zh": "CN", "zh-hk": "CN", "ja": "JP"}.get(d["lang"])
        kind = {"KR": "manhwa", "CN": "manhua"}.get(country, "manga")
        out.append(Entity(
            source="mangadex", id=f"mangadex:{d['id']}", name=en, names=names,
            kind=kind, country=country, year=d.get("year"),
            url=f"https://mangadex.org/title/{d['id']}", rank=i))
    return out


# --------------------------------------------------------------- Wikipedia

# Wikipedia rate-limits the shared egress IP to roughly one request in ten
# seconds, so search is the last resort. Most shows have an article at one of
# a handful of predictable titles, and the action API resolves fifty of those
# per request, following redirects, so exact titles are tried in bulk first.
WIKI_SUFFIXES = ("", " (TV series)", " (South Korean TV series)",
                 " (Chinese TV series)", " (Thai TV series)",
                 " (Japanese TV series)", " (film)", " (web series)",
                 " (manhwa)", " (telenovela)")

_SMALL = {"a", "an", "the", "of", "in", "on", "at", "to", "for", "and", "or",
          "but", "by", "with", "from", "as", "vs"}


def title_forms(title: str) -> list[str]:
    """The spellings an English Wikipedia article title is likely to use."""
    t = " ".join(title.split())
    if not t:
        return []
    words = t.split()
    smart = " ".join(w if (i and w.lower() in _SMALL) else w[:1].upper() + w[1:]
                     for i, w in enumerate(words))
    smart = " ".join(w.lower() if (i and w.lower() in _SMALL) else w
                     for i, w in enumerate(smart.split()))
    forms = [t[:1].upper() + t[1:], smart]
    return list(dict.fromkeys(forms))


def wiki_variants(title: str) -> list[str]:
    return [f + s for f in title_forms(title) for s in WIKI_SUFFIXES]


def wikipedia_bulk(titles: list[str], cache: Cache, log=None) -> None:
    """Fetch every exact-title variant not yet cached, fifty per request."""
    todo = [v for t in titles for v in wiki_variants(t)
            if cache.get("wikititle:" + v) is None]
    todo = list(dict.fromkeys(todo))
    for i in range(0, len(todo), 50):
        chunk = todo[i:i + 50]
        q = urllib.parse.urlencode({
            "action": "query", "format": "json", "formatversion": 2,
            "redirects": 1, "prop": "description|pageprops",
            "ppprop": "disambiguation", "titles": "|".join(chunk)})
        res = _http("https://en.wikipedia.org/w/api.php?" + q, tries=12)
        if res is None:
            if log:
                log(f"wikipedia bulk chunk {i // 50} failed, will retry next run")
            continue
        qq = res.get("query") or {}
        hop = {}
        for r in (qq.get("normalized") or []) + (qq.get("redirects") or []):
            hop[r["from"]] = r["to"]
        pages = {p["title"]: p for p in qq.get("pages") or []}
        for v in chunk:
            t = v
            for _ in range(4):
                if t not in hop:
                    break
                t = hop[t]
            p = pages.get(t)
            if not p or p.get("missing") or p.get("invalid"):
                cache.put("wikititle:" + v, {"missing": True})
            else:
                cache.put("wikititle:" + v, {
                    "title": p["title"], "description": p.get("description") or "",
                    "disambiguation": "disambiguation" in (p.get("pageprops") or {}),
                    "redirects": [v] if v != p["title"] else []})
        cache.save()
        if log:
            log(f"wikipedia bulk {min(i + 50, len(todo))}/{len(todo)}")


def wikipedia_exact(title: str, cache: Cache) -> list:
    out, seen = [], set()
    for i, v in enumerate(wiki_variants(title)):
        p = cache.get("wikititle:" + v)
        if not p or p.get("missing") or p["title"] in seen:
            continue
        seen.add(p["title"])
        out.append({**p, "index": i})
    return out


def wikipedia_raw(query: str, cache: Cache) -> list | None:
    key = "wikipedia:" + query.casefold()
    hit = cache.get(key)
    if hit is not None:
        return hit
    q = urllib.parse.urlencode({
        "action": "query", "format": "json", "formatversion": 2,
        "generator": "search", "gsrsearch": query, "gsrlimit": 8,
        "prop": "description|redirects|pageprops", "ppprop": "disambiguation",
        "rdlimit": "max"})
    res = _http("https://en.wikipedia.org/w/api.php?" + q, tries=12)
    if res is None:
        return None
    pages = (res.get("query") or {}).get("pages") or []
    data = [{"title": p["title"], "index": p.get("index", 99),
             "description": p.get("description") or "",
             "disambiguation": "disambiguation" in (p.get("pageprops") or {}),
             "redirects": [r["title"] for r in p.get("redirects") or []]}
            for p in pages]
    data.sort(key=lambda p: p["index"])
    cache.put(key, data)
    return data


_DEMONYM = [
    ("south korean", "KR"), ("korean", "KR"), ("taiwanese", "TW"),
    ("hong kong", "HK"), ("chinese", "CN"), ("japanese", "JP"), ("thai", "TH"),
    ("indian", "IN"), ("hindi", "IN"), ("tamil", "IN"), ("telugu", "IN"),
    ("pakistani", "PK"), ("turkish", "TR"), ("mexican", "MX"),
    ("colombian", "CO"), ("argentine", "AR"), ("venezuelan", "VE"),
    ("chilean", "CL"), ("peruvian", "PE"), ("spanish", "ES"),
    ("brazilian", "BR"), ("filipino", "PH"), ("philippine", "PH"),
    ("indonesian", "ID"), ("vietnamese", "VN"), ("american", "US"),
    ("british", "GB"), ("english", "GB"), ("canadian", "CA"),
    ("australian", "AU"), ("french", "FR"), ("german", "DE"),
]

# Order matters: "anime television series" is anime, "television film" a film.
_KIND = [
    ("telenovela", "drama"),        # before "novel", which it contains
    ("anime", "anime"), ("donghua", "donghua"), ("manhwa", "manhwa"),
    ("webtoon", "manhwa"), ("manhua", "manhua"), ("manga", "manga"),
    ("light novel", "novel"), ("web novel", "novel"), ("novel", "novel"),
    ("animated", "anime"), ("telenovela", "drama"), ("television film", "film"),
    ("film", "film"), ("movie", "film"), ("television series", "drama"),
    ("tv series", "drama"), ("web series", "drama"), ("miniseries", "drama"),
    ("drama", "drama"), ("soap opera", "drama"), ("sitcom", "drama"),
    ("television programme", "drama"), ("television program", "drama"),
    ("streaming series", "drama"), ("series", "drama"),
]

_PAREN = re.compile(r"\s*\(([^)]*)\)\s*$")
_YEAR = re.compile(r"\b(19[3-9]\d|20[0-4]\d)\b")


def _wiki_kind(text: str) -> str | None:
    t = text.lower()
    for word, kind in _KIND:
        if word in t:
            return kind
    return None


def wikipedia_entities(pages: list, searched: bool = False) -> list[Entity]:
    out = []
    for i, p in enumerate(pages):
        if p["disambiguation"]:
            continue
        paren = _PAREN.search(p["title"])
        hint = (paren.group(1) if paren else "") + " " + p["description"]
        kind = _wiki_kind(hint)
        if kind is None:
            continue                    # not a work of media: "School", "Filter"
        low = hint.lower()
        # "book series" and "video game series" say series and are neither.
        if re.search(r"\b(video game|game|album|song|single|band|group|"
                     r"book series|comic strip|character|franchise|episode)\b", low) \
                and kind in ("drama",):
            continue
        country = next((c for w, c in _DEMONYM if w in low), None)
        y = _YEAR.search(hint)
        name = _PAREN.sub("", p["title"]).strip()
        names = [name] + [_PAREN.sub("", r).strip() for r in p["redirects"]]
        out.append(Entity(
            source="wikipedia", id="wikipedia:" + p["title"], name=name,
            names=names, kind=kind, country=country,
            year=int(y.group(1)) if y else None,
            url="https://en.wikipedia.org/wiki/" + urllib.parse.quote(
                p["title"].replace(" ", "_")),
            spanish=("spanish-language" in low or "telenovela" in low),
            rank=i, searched=searched))
    return out


# ------------------------------------------------------------------ matching

def _keys(name: str, keyfn) -> set[str]:
    """A name and its part before a subtitle colon, both as group keys.

    "F4 Thailand: Boys Over Flowers" is what Wikipedia calls the show that
    every commenter calls "F4 Thailand".
    """
    out = {keyfn(name)}
    for sep in (":", " - ", " – "):
        if sep in name:
            out.add(keyfn(name.split(sep, 1)[0]))
    return {k for k in out if k}


def matches(title: str, ent: Entity, keyfn) -> bool:
    """Does the commenter's title name this entity?

    Exact on normalised names, with a narrow fuzzy allowance for long titles
    so "Exclusive fairy tail" still confirms, and none for short ones, where
    one letter is a different show ("Hidden Love" is not "Hidden Lover").
    """
    k = keyfn(title)
    if not k:
        return False
    kk = k.replace(" ", "")
    for n in ent.names:
        for nk in _keys(n, keyfn):
            if nk == k or nk.replace(" ", "") == kk:
                return True
            if len(k) >= 12 and len(nk) >= 12 and fuzz.ratio(nk, k) >= 92:
                return True
    return False


def lookup(title: str, caches: dict, keyfn, offline: bool = True,
           search: bool = False, mangadex: bool = False) -> list[Entity]:
    """Every entity in any source that this title names.

    caches maps source name to its Cache. offline=True answers from the
    caches only; the fetch script fills them. search=True allows a Wikipedia
    full-text search when no exact article title matched.
    """
    found: list[Entity] = []
    c = caches["anilist"]
    raw = c.get("anilist:" + title.casefold()) if offline else anilist_raw(title, c)
    found += [e for e in anilist_entities(raw or []) if matches(title, e, keyfn)]

    c = caches["wikipedia"]
    hits = [e for e in wikipedia_entities(wikipedia_exact(title, c))
            if matches(title, e, keyfn)]
    if not hits:
        raw = c.get("wikipedia:" + title.casefold())
        if raw is None and search and not offline:
            raw = wikipedia_raw(title, c)
        hits = [e for e in wikipedia_entities(raw or [], searched=True)
                if matches(title, e, keyfn)]
    found += hits

    if not any(e.source == "anilist" for e in found):
        c = caches["mangadex"]
        raw = c.get("mangadex:" + title.casefold())
        if raw is None and mangadex and not offline:
            raw = mangadex_raw(title, c)
        found += [e for e in mangadex_entities(raw or []) if matches(title, e, keyfn)]
    return found


def open_caches(directory: str) -> dict:
    os.makedirs(directory, exist_ok=True)
    return {s: Cache(os.path.join(directory, s + ".json"))
            for s in ("anilist", "wikipedia", "mangadex")}
