"""One link in, one answer out: the "what is this show?" lookup.

This is the whole pipeline behind the paste-a-link page. It reads a video's
title, description and comments (replies included), picks the best answer,
shows the comment the answer came from, and checks the name against AniList,
Wikipedia and MangaDex so the viewer gets a link to the actual work.

The confidence note is written for a viewer, not a developer. It never
claims more than the evidence: a name nobody in the comments said is a guess,
and the page says so.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from pathlib import Path

from rapidfuzz import fuzz

from .analyze import analyze
from .discover import _REPLY_HANDLE, QUESTION_PATTERNS
from .extract import Extractor, clean
from .fetch import YouTubeAPISource, video_id
from .gazetteer import SEED, Gazetteer, Title, fold

_HERE = Path(__file__).resolve().parent.parent
_ZERO_WIDTH = re.compile(r"[​-‏⁠﻿]")

_extractor: Extractor | None = None

import concurrent.futures as _cf
# Database lookups back off hard when rate-limited; running them on a worker
# lets the page answer on time and the cache fill in behind it.
_POOL = _cf.ThreadPoolExecutor(max_workers=2)


def extractor() -> Extractor:
    """Seed catalogue plus the titles the sweep learned, as measured held-out."""
    global _extractor
    if _extractor is None:
        titles = list(SEED)
        learned = _HERE / "learned_catalogue.json"
        if learned.exists():
            titles += [Title(**r) for r in json.loads(learned.read_text())]
        _extractor = Extractor(Gazetteer(titles))
    return _extractor


# ------------------------------------------------------------ the evidence

def _mentions(text: str, names: list[str]) -> bool:
    t = " " + fold(_ZERO_WIDTH.sub("", text)) + " "
    return any(n and (" " + n + " ") in t for n in names)


def evidence(answer, comments: list[dict], limit: int = 2) -> list[dict]:
    """The comments a viewer would want to see: the answer, in context.

    A reply naming the title under someone asking "what is this?" is the most
    convincing thing the page can show, so it goes first, with the question.
    """
    names = [fold(answer.title)] + [fold(n) for n, _k in answer.alternates]
    by_id = {c.get("id"): c for c in comments}
    found = []
    for c in comments:
        if not _mentions(c.get("text", ""), names):
            continue
        # The asker's own question often repeats the title back ("is this
        # Lookism?"); that is not an answer.
        if QUESTION_PATTERNS.search(clean(c.get("text", ""))) and "?" in c.get("text", ""):
            continue
        cid = c.get("id") or ""
        parent = by_id.get(cid.split(".", 1)[0]) if "." in cid else None
        asked = None
        if parent and QUESTION_PATTERNS.search(clean(parent.get("text", ""))):
            asked = {"text": _trim(parent.get("text", "")),
                     "likes": parent.get("likes", 0)}
        text = c.get("text", "")
        if parent:
            text = _REPLY_HANDLE.sub("", _ZERO_WIDTH.sub("", text))
        found.append({"text": _trim(text), "likes": c.get("likes", 0),
                      "reply_to": asked})
    found.sort(key=lambda e: (e["reply_to"] is None, -e["likes"]))
    return found[:limit]


def _display(title: str) -> str:
    """"firework on my heart" reads better as a name: capitalise it only
    when the commenter wrote it all in lowercase, never otherwise."""
    if title != title.lower():
        return title
    from .resolve import title_forms
    return title_forms(title)[-1]


def _kind_hint(meta: dict, confirmed: dict | None) -> str:
    if confirmed:
        return confirmed["what"].lower()
    t = (meta.get("title", "") + " " + " ".join(meta.get("tags") or [])).lower()
    for word, hint in (("manhwa", "manhwa"), ("manhua", "manhua"), ("webtoon", "webtoon"),
                       ("donghua", "donghua"), ("anime", "anime"),
                       ("manga", "manga"), ("cdrama", "chinese drama"),
                       ("kdrama", "korean drama"), ("drama", "drama")):
        if word in t:
            return hint
    return ""


def _trim(text: str, n: int = 280) -> str:
    text = _ZERO_WIDTH.sub("", text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def note(answer, asked: int) -> str:
    """Plain-language confidence, for someone who just wants the name."""
    said = answer.commenters
    in_meta = any(s.startswith("metadata") for s in answer.sources)
    people = f"{said} {'person' if said == 1 else 'people'}"
    if answer.confidence == "high":
        if said and in_meta:
            return f"{people} named it in the comments, and it matches what the uploader wrote."
        if said:
            return f"{people} named it in the comments."
        return "The uploader wrote it, and it matches a known title."
    if answer.confidence == "medium":
        if said:
            return f"{people} named it in the comments. Likely, but worth a quick check."
        return "The uploader wrote it. Likely, but nobody in the comments confirmed it."
    return "Only a guess from what the uploader wrote. Nobody in the comments confirmed it."


# ---------------------------------------------------------- the database

def confirm(title: str, meta: dict, caches: dict | None, budget: float = 12.0):
    """Look the answer up in AniList, Wikipedia and MangaDex.

    Returns a dict for the page, or None when no database knows it. Live
    lookups are allowed but bounded, so a slow database costs the viewer a
    few seconds rather than the answer.
    """
    if caches is None:
        return None
    import concurrent.futures as cf
    try:
        return _POOL.submit(_confirm, title, meta, caches, budget).result(budget + 3)
    except (cf.TimeoutError, Exception):
        return None


def _context(meta: dict) -> str:
    return " ".join([meta.get("title", ""), " ".join(meta.get("tags") or []),
                     (meta.get("description") or "")[:400]])


def _confirm(title: str, meta: dict, caches: dict, budget: float):
    from . import resolve
    from .report import _DRAWN, choose_entity, group_key, kind_label, live_action, segments_of

    ctx = _context(meta)
    segs = segments_of(ctx)
    live = live_action(segs, ctx)
    deadline = time.time() + budget
    try:
        resolve.anilist_raw(title, caches["anilist"])
        # Wikipedia is for live action, and it rate-limits hard. On a video
        # tagged manhwa or anime, AniList and MangaDex are the right books.
        if live and not segs & _DRAWN and time.time() < deadline:
            resolve.wikipedia_bulk([title], caches["wikipedia"])
        ents = resolve.lookup(title, caches, group_key, offline=True)
        if not any(e.source == "anilist" for e in ents) and time.time() < deadline:
            resolve.mangadex_raw(title, caches["mangadex"])
            ents = resolve.lookup(title, caches, group_key, offline=True)
    except Exception:
        return None
    finally:
        for c in caches.values():
            try:
                c.save()
            except OSError:
                pass
    ent, status = choose_entity(title, ents, segs, live)
    if ent is None:
        return {"status": status}
    return {"status": "confirmed", "name": ent.name, "what": kind_label(ent),
            "year": ent.year, "url": ent.url, "names": ent.names, "source": {
                "anilist": "AniList", "wikipedia": "Wikipedia",
                "mangadex": "MangaDex"}[ent.source]}


# ------------------------------------------------- what the uploader wrote

# Recap and edit channels label the work in the description: "Title : War of
# Extinction", "Manhwa Name : A Life-Changing Turn". The metadata layer kept
# the label as part of the name and ranked it below the clickbait title bar.
_WORK = r"(?:manhwa|manhua|manga|anime|webtoon|donghua|novel|comic|series|manhwa\s*/\s*manhua)"
_LABEL = re.compile(
    r"^[^\w]*(?:" + _WORK + r"(?:\s*(?:name|title))?|(?:name|title)(?:\s+of\s+(?:the\s+|this\s+)?"
    + _WORK + r")?|original\s+title)\s*[:：=\-–—]+\s*(.+)$", re.I)
_CUT = re.compile(r"\s*(?:[|#]|https?://|\(ch|\bch(?:apter)?\.?\s*\d|\bep(?:isode)?\.?\s*\d).*$", re.I)
_NOT_A_NAME = re.compile(r"^(?:ongoing|completed?|hiatus|unknown|n/?a|in\s+description|"
                         r"comment|below|pinned|see|check|webtoons?|tapas|naver|kakao(?:page)?|"
                         r"bilibili|crunchyroll|netflix|youtube|tiktok|google)\b", re.I)


def labelled(meta: dict) -> list[str]:
    """Names the uploader stated outright with a label, in order."""
    out: list[str] = []
    lines = [meta.get("title", "")] + (meta.get("description") or "").splitlines()
    for line in lines[:40]:
        for part in re.split(r"\s[|/]\s", line):
            m = _LABEL.match(part.strip())
            if not m:
                continue
            v = _CUT.sub("", m.group(1)).strip(" \t.,;:!-–—\"'“”‘’")
            v = re.sub(r"\s+", " ", v)
            if not (2 <= len(v) <= 80) or len(v.split()) > 12 or _NOT_A_NAME.match(v):
                continue
            if not re.search(r"[^\W\d_]{2}", v) or fold(v) in {fold(x) for x in out}:
                continue
            out.append(v)
    return out


_PREFIX = re.compile(r"^\s*(?:" + _WORK + r"\s*)?(?:name|title)\s*[:：\-–—]\s*", re.I)


_NOISE = re.compile(r"(?:\s+(?:4k|hd|amv|fmv|edit|edits|scene|scenes|recap|explained|shorts?|"
                    r"manhwa|manhua|manga|anime|webtoon|clip|part\s*\d+|ep\.?\s*\d+))+\s*$", re.I)


def _uploader_candidates(meta: dict) -> list[str]:
    """Other things the uploader wrote that might be the name, best first.

    Pieces of the title bar come before hashtags: an edit titled
    "Angry Rage Mode 《 Tokyo Revengers 4k Edit 》" tagged #jujutsukaisen is
    Tokyo Revengers.
    """
    from .metadata import candidates
    out: list[str] = []
    title = re.split(r"\s#", meta.get("title", ""))[0]
    for seg in re.split(r"[|《》「」【】\[\]•]|\s[-–—]\s", title):
        seg = _NOISE.sub("", _EMOJI_ISH.sub(" ", seg)).strip(" -–—:!.,\"'")
        seg = re.sub(r"\s+", " ", seg)
        if 1 <= len(seg.split()) <= 16 and re.search(r"[^\W\d_]{2}", seg):
            out.append(seg)
    for c in candidates(meta)[:5]:
        if c.source in ("title", "hashtag", "description"):
            out.append(_PREFIX.sub("", c.text))
    seen, uniq = set(), []
    for x in out:
        if x and fold(x) not in seen:
            seen.add(fold(x))
            uniq.append(x)
    return uniq


_EMOJI_ISH = re.compile(r"[^\w\s'’:&!?,.\-–—()]+")


# ------------------------------------------------------------- the lookup

def _answer_dict(title: str, confidence: str, note_text: str, db, meta: dict,
                 comments_shown: list, also: list[str]) -> dict:
    confirmed = db if db and db.get("status") == "confirmed" else None
    shown = _display(title)
    # Show the database's name for the work ("Kimetsu No Yaiba" becomes
    # "Demon Slayer: Kimetsu no Yaiba"), unless it shares nothing with what
    # people called it: AniList files some manhwa under romaji only, and
    # "Segyemyeolmangjeon" means nothing to someone who read "War of Extinction".
    if confirmed and fuzz.token_set_ratio(fold(confirmed["name"]), fold(title)) >= 50:
        shown = confirmed["name"]
    elif confirmed:
        also = also + [confirmed["name"]]
    if confirmed and fold(confirmed["name"]) != fold(title):
        also = [title] + [a for a in also if fold(a) != fold(title)]
    # Every other name the database knows the work by: the Korean, Chinese
    # or Japanese original, its romanisation and any English titles. People
    # hunting for a manhwa often find it under a different name elsewhere.
    if confirmed:
        also = also + [n for n in confirmed.get("names") or [] if len(n) <= 60]
    seen, names = {fold(shown)}, []
    for a in also:
        k = fold(a)
        if not k or k in seen or k in fold(shown):
            continue
        seen.add(k)
        names.append(a)
    also = names
    if confirmed:
        confirmed = {k: v for k, v in confirmed.items() if k != "names"}
    return {
        "title": shown,
        "confidence": confidence,
        "note": note_text,
        "also_called": also[:6],
        "comments": comments_shown,
        "database": confirmed,
        "search": "https://www.google.com/search?q=" + urllib.parse.quote(
            f'"{shown}" ' + _kind_hint(meta, confirmed)),
    }


def _db_note(db) -> str:
    return f"it's a real {db['what'].lower()} on {db['source']}"


def find(url: str, source: YouTubeAPISource | None = None,
         caches: dict | None = None, limit: int = 600) -> dict:
    vid = video_id(url.strip())
    source = source or YouTubeAPISource()
    meta = source.video_meta(vid)
    comments = list(source.comments(vid, limit=limit))
    answers, asked = analyze(meta, comments, extractor())

    out = {
        "video": {"id": vid, "title": meta["title"], "channel": meta["channel"],
                  "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                  "comments_total": meta["comment_count"]},
        "comments_read": len(comments),
        "asked": asked,
        "answer": None,
        "others": [],
    }

    stated = labelled(meta)
    # Stated in the comments, or a known title people mentioned that the
    # uploader's own words corroborate.
    spoken = [x for x in answers
              if (any(src.startswith("comments") for src in x.sources)
                  or (x.confidence == "high" and x.commenters > 0))
              and not _about_song(x, comments)]
    passing = [x for x in answers if x.commenters > 0 and x not in spoken
               and x.confidence != "low" and not _about_song(x, comments)]
    probes = 0

    def take(ans, conf, text, db, shown_comments=None):
        out["answer"] = _answer_dict(
            ans if isinstance(ans, str) else ans.title, conf, text, db, meta,
            shown_comments if shown_comments is not None else [],
            [] if isinstance(ans, str) else [n for n, _k in ans.alternates])
        if not isinstance(ans, str):
            out["others"] = [{"title": x.title, "commenters": x.commenters}
                             for x in (spoken + passing)[:5]
                             if x is not ans and x.confidence != "low"][:3]
        return out

    def ok(db):
        return bool(db) and db.get("status") == "confirmed"

    # 1. Someone in the comments stated the name ("it's called X", a reply
    #    under "name?"). This is what the tool is for. A labelled name from
    #    the uploader beats one or two people, though not a crowd.
    for x in spoken:
        if x.confidence == "low":
            break
        if x.confidence != "high" and stated and not any(
                fold(s) == fold(x.title) for s in stated):
            break
        db = confirm(x.title, meta, caches)
        probes += 1
        if db and db.get("status") == "contradicted" and x.confidence != "high":
            continue
        return take(x, x.confidence, note(x, asked), db, evidence(x, comments))

    # 2. The uploader said it outright with a label.
    for name in stated[:2]:
        db = confirm(name, meta, caches)
        probes += 1
        if db and db.get("status") == "contradicted":
            continue
        text = ("The uploader named it in the video's description"
                + (f", and {_db_note(db)}." if ok(db) else ". No database has it yet, so check it."))
        return take(name, "high" if ok(db) else "medium", text, db)

    # 3. One person stated a name and it is a real work. On a yuri Short,
    #    one reply saying "Tamen de Gushi" (417 likes) is the answer; five
    #    people mentioning Blue Lock in passing are not.
    for x in [x for x in spoken if x.confidence == "low"][:2]:
        db = confirm(x.title, meta, caches, budget=6.0)
        probes += 1
        if ok(db):
            text = (f"{x.commenters} {'person' if x.commenters == 1 else 'people'} named it "
                    f"in the comments and {_db_note(db)}. Worth a quick check.")
            return take(x, "medium", text, db, evidence(x, comments))

    # 4. Something the uploader wrote, if a title database knows it.
    #    Unconfirmed, the title bar of a Short is clickbait ("Bro Sacrificed
    #    His Own Kidney To Kill A God") far more often than a name.
    for name in _uploader_candidates(meta):
        if probes >= 6:
            break
        db = confirm(name, meta, caches, budget=6.0)
        probes += 1
        if ok(db):
            text = (f"The uploader wrote it and {_db_note(db)}. "
                    "Nobody in the comments confirmed it.")
            return take(name, "medium", text, db)

    # 5. A known title people kept mentioning. Weakest: on an edit, people
    #    name the shows they compare it to.
    for x in passing[:1]:
        db = confirm(x.title, meta, caches)
        if db and db.get("status") == "contradicted":
            continue
        return take(x, "medium", note(x, asked), db, evidence(x, comments))
    return out


_SONG = re.compile(r"\b(?:songs?|music|audio|sound|remix|phonk|montagem|funk|lyrics|"
                   r"cancion|canci[oó]n|musica|m[uú]sica|gaana|gana)\b", re.I)


def _about_song(answer, comments: list[dict]) -> bool:
    """People ask for the song under edits as often as for the show."""
    names = [fold(answer.title)]
    by_id = {c.get("id"): c for c in comments}
    hits = total = 0
    for c in comments:
        if not _mentions(c.get("text", ""), names):
            continue
        total += 1
        cid = c.get("id") or ""
        parent = by_id.get(cid.split(".", 1)[0]) if "." in cid else None
        if _SONG.search(c.get("text", "")) or (parent and _SONG.search(parent.get("text", ""))):
            hits += 1
    return total > 0 and hits * 2 >= total


def interactive() -> None:
    """Give up on a rate-limited database fast: someone is waiting."""
    from . import resolve
    resolve.MAX_TRIES = 2
    resolve.MAX_WAIT = 3.0


def default_caches() -> dict | None:
    """Title-database caches, seeded from the project's own dbcache."""
    from . import resolve
    d = os.environ.get("TITLESIFT_DBCACHE") or str(_HERE / "dbcache")
    try:
        return resolve.open_caches(d)
    except OSError:
        return None
