"""Titles hiding in the video's own metadata.

On the two videos this was first tested against, the answer was sitting in
plain sight the whole time:

    "... #kdramaedit #teachyoualesson"          -> Teach You A Lesson
    "... | May i help you ? | MBC | Tiki Tiki"  -> May I Help You

So before reading a single comment, read the title, the description and the
tags. A candidate that appears in BOTH the metadata and the comments is about
as certain as this gets, and metadata alone still beats nothing at all.

Hashtags arrive glued together (#teachyoualesson), so they are split back into
words with a frequency-weighted dynamic program rather than a dictionary of
known titles - the whole point is to find titles nobody has catalogued.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache

from wordfreq import zipf_frequency

from .gazetteer import fold

# Hashtags that describe the *format*, never the work.
GENERIC_TAGS = {
    "shorts", "short", "fyp", "foryou", "foryoupage", "viral", "trending",
    "edit", "edits", "amv", "fmv", "clip", "clips", "scene", "scenes", "funny",
    "comedy", "drama", "kdrama", "cdrama", "jdrama", "anime", "manhwa",
    "manga", "webtoon", "movie", "movies", "film", "series", "netflix",
    "reels", "reel", "tiktok", "youtube", "youtubeshorts", "explore", "fy",
    "subscribe", "like", "follow", "new", "best", "top", "love", "sad",
    # broadcasters and platforms are not works
    "mbc", "kbs", "sbs", "tvn", "jtbc", "ocn", "ena", "viki", "viu", "iqiyi",
    "wetv", "youku", "mango", "hulu", "prime", "disney", "crunchyroll",
    "romance", "action", "korean", "korea", "chinese", "japanese", "indian",
    "bollywood", "hollywood", "edittutorial", "aftereffects", "capcut",
    "turkishdrama", "chinesedrama", "koreandrama", "japanesedrama",
    "thaidrama", "indiandrama", "turkish drama", "chinese drama",
    "korean drama", "japanese drama", "thai drama", "kdramascene",
    "dramascene", "shortvideo", "shortsfeed", "shortsvideo", "viralvideo",
    "animeedit", "kdramaedit", "dramaedit", "moviescene", "status", "whatsapp",
}

# A category is not a work. Enumerating every nationality x form pairing in
# GENERIC_TAGS does not scale and already missed "turkish series", which rode
# three videos into the report as a title with 25 people asking behind it.
NATIONALITY = {
    "turkish", "chinese", "korean", "japanese", "indian", "thai", "spanish",
    "mexican", "filipino", "russian", "french", "german", "italian", "arabic",
    "vietnamese", "indonesian", "brazilian", "pakistani", "asian", "hindi",
    "tamil", "telugu", "urdu", "punjabi", "latino", "african", "american",
    "british", "taiwanese", "malaysian", "persian", "iranian", "egyptian",
}
FORM_WORDS = {
    "drama", "dramas", "series", "serial", "serials", "show", "shows",
    "movie", "movies", "film", "films", "clip", "clips", "scene", "scenes",
    "edit", "edits", "song", "songs", "novel", "novels", "webtoon", "anime",
    "manhwa", "manga", "manhua", "donghua", "telenovela", "kdrama", "cdrama",
}


def is_descriptor(s: str) -> bool:
    """True for "turkish series" or "korean drama edit" - a genre, not a title.

    A nationality is required, so "Love Song" and "Chinese Zodiac" survive.
    """
    key = fold(s)
    words = key.split()
    if not words or len(words) > 3:
        return False
    glued = key.replace(" ", "")
    if key in GENERIC_TAGS or glued in GENERIC_TAGS or glued in FORM_WORDS:
        return True    # "dong hua", "k drama" - a bad hashtag split, not a show
    if not any(w in NATIONALITY for w in words):
        return False
    return all(w in NATIONALITY or w in FORM_WORDS for w in words)


# Title bars: "Ep 3 | May i help you ? | MBC" splits on these.
_SPLIT = re.compile(r"\s*[|｜·•‧⧸/]\s*|\s+[-–—]\s+")
_HASHTAG = re.compile(r"#(\w{3,40})")
_EMOJI = re.compile("[" "\U0001F000-\U0001FAFF" "☀-➿" "️" "]+")
_NOISE = re.compile(r"\b(?:ep|episode|part|pt|season|s)\s*\.?\s*\d+\b", re.I)


@dataclass
class MetaCandidate:
    text: str
    source: str        # title | description | tag | hashtag
    confidence: float  # 0-1, how title-like the span is


@lru_cache(maxsize=100_000)
def _word_score(w: str) -> float:
    """Log-probability of a word; unknown words are heavily penalised."""
    z = zipf_frequency(w, "en")
    if z <= 0:
        # Unknown, but long unknowns are worse than short ones.
        return -6.0 - 0.6 * len(w)
    return math.log10(z / 10.0)


_KNOWN_GLUED: dict[str, str] = {}
_KNOWN_SOLO: set[str] = set()


def use_catalogue(gaz) -> None:
    """Let the segmenter and the hashtag scorer consult a catalogue."""
    _KNOWN_GLUED.clear()
    _KNOWN_SOLO.clear()
    _KNOWN_GLUED.update(getattr(gaz, "despaced", {}) or {})
    for key in getattr(gaz, "alias_to_canonical", {}) or {}:
        if " " not in key and len(key) >= 5:
            _KNOWN_SOLO.add(key)


def segment(glued: str, max_word: int = 16) -> list[str]:
    """Split "teachyoualesson" into ["teach", "you", "a", "lesson"].

    A dynamic program over word frequency. The frequency pass has to work on
    titles nobody has ever indexed, so it stays the fallback - but when the
    string is a title we already know, guessing at it is strictly worse:
    "demonslayer" came out as "demons layer" and "jujutsukaisen" as
    "jujutsu kai sen", destroying the answer on the way in.
    """
    s = glued.lower()
    hit = _KNOWN_GLUED.get(s)
    if hit:
        return hit.split()
    n = len(s)
    if not n:
        return []
    best = [-1e18] * (n + 1)
    back = [0] * (n + 1)
    best[0] = 0.0
    for i in range(1, n + 1):
        for j in range(max(0, i - max_word), i):
            cand = best[j] + _word_score(s[j:i])
            if cand > best[i]:
                best[i] = cand
                back[i] = j
    out, i = [], n
    while i > 0:
        out.append(s[back[i]:i])
        i = back[i]
    return out[::-1]


def _clean(text: str) -> str:
    t = _EMOJI.sub(" ", text)
    t = _HASHTAG.sub(" ", t)
    t = _NOISE.sub(" ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip(" -–—|:;,.!?\"'")


# Romanised East Asian family names. A hashtag that starts with one is
# almost always the actor, and actor tags outranked the show on video after
# video: "#baijingting #zhangruonan" beat "The First Frost", "#baesuzy" beat
# "Vagabond". This demotes them; it never rejects them, so a name that really
# is the title can still be carried by the comments.
PERSON_LEAD = {
    # Korean
    "kim", "lee", "park", "choi", "jung", "jeong", "kang", "cho", "jo",
    "yoon", "yun", "jang", "lim", "im", "han", "oh", "seo", "shin", "kwon",
    "hwang", "ahn", "an", "song", "ryu", "hong", "jeon", "jun", "ko", "go",
    "moon", "mun", "son", "yang", "bae", "baek", "heo", "nam", "sim", "noh",
    "ha", "gu", "koo", "min", "chae", "cha", "ju", "joo", "yu", "yoo",
    "byun", "ji", "jin", "nam gil", "sung", "seong", "woo", "pyo",
    # Chinese
    "wang", "li", "zhang", "liu", "chen", "huang", "zhao", "wu", "zhou",
    "xu", "sun", "ma", "zhu", "hu", "guo", "lin", "he", "gao", "luo",
    "zheng", "liang", "xie", "tang", "deng", "feng", "cao", "peng", "zeng",
    "xiao", "tian", "dong", "yuan", "pan", "jiang", "cai", "yan", "bai",
    "cheng", "yu", "shen", "lu", "fan", "wei", "ren", "qin", "xia",
}


PERSON_PENALTY = 0.45


def looks_like_person(s: str) -> bool:
    words = fold(s).split()
    return 2 <= len(words) <= 4 and words[0] in PERSON_LEAD


def _looks_like_title(s: str) -> float:
    """Rough 0-1 score for how much a span reads like a work's name."""
    words = s.split()
    if not (1 <= len(words) <= 9) or len(s) < 3:
        return 0.0
    if is_descriptor(s):
        return 0.0
    score = 0.5
    caps = sum(1 for w in words if w[:1].isupper())
    if caps >= max(1, len(words) - 2):
        score += 0.25
    if s.endswith("?") or s.endswith("!"):
        score += 0.05           # "May I Help You ?" keeps its punctuation
    if len(words) >= 2:
        score += 0.1
    if PERSON_PENALTY and looks_like_person(s):
        score -= PERSON_PENALTY
    return max(min(score, 1.0), 0.0)


def candidates(meta: dict) -> list[MetaCandidate]:
    """meta: {title, description, tags[]} as returned by videos.list."""
    out: list[MetaCandidate] = []
    title = meta.get("title", "") or ""

    for part in _SPLIT.split(title):
        c = _clean(part)
        if c:
            conf = _looks_like_title(c)
            if conf > 0:
                out.append(MetaCandidate(c, "title", conf))

    for tag in _HASHTAG.findall(title + " " + (meta.get("description", "") or "")):
        low = tag.lower()
        if low in GENERIC_TAGS or len(low) < 6:
            continue
        # "#monster" and "#lookism" are titles that need no splitting, and
        # the length-2 rule below was throwing every one of them away.
        if low in _KNOWN_SOLO:
            out.append(MetaCandidate(low, "hashtag", 0.95))
            continue
        known = low in _KNOWN_GLUED
        words = segment(low)
        if len(words) < 2:
            continue
        phrase = " ".join(words)
        if is_descriptor(phrase) or all(w in GENERIC_TAGS for w in words):
            continue
        if known:
            out.append(MetaCandidate(phrase, "hashtag", 0.95))
            continue
        # A split into plausible words is the signal; a garbage split is not.
        # Stray single letters mean the split failed ("#cünzey" -> "c ü nz ey",
        # "#wangyilei" -> "wang yi le i"). Non-English names do this a lot.
        if any(len(w) == 1 and w not in ("a", "i") for w in words):
            continue
        if sum(1 for w in words if zipf_frequency(w, "en") >= 2.5) >= max(2, len(words) - 1):
            conf = 0.7
            # An actor tag is not the show. Without this the hashtag branch
            # scored a flat 0.7 and "#baesuzy" outranked "Vagabond".
            if PERSON_PENALTY and looks_like_person(phrase):
                conf -= PERSON_PENALTY
            out.append(MetaCandidate(phrase, "hashtag", conf))

    for tag in (meta.get("tags") or [])[:20]:
        c = _clean(tag)
        if c and c.lower() not in GENERIC_TAGS and len(c.split()) >= 2:
            out.append(MetaCandidate(c, "tag", 0.4))

    # First line of the description often names the work outright.
    desc = (meta.get("description", "") or "").split("\n")
    for line in desc[:3]:
        c = _clean(line)
        if c and 3 <= len(c) <= 70:
            conf = _looks_like_title(c)
            if conf >= 0.7:
                out.append(MetaCandidate(c, "description", conf * 0.8))

    seen, uniq = set(), []
    for m in out:
        k = fold(m.text)
        if k and k not in seen:
            seen.add(k)
            uniq.append(m)
    return sorted(uniq, key=lambda m: -m.confidence)
