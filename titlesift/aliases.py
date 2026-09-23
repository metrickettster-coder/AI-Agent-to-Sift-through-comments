"""Alternate names for the same work.

One work carries many names, and comment sections mix them freely:

    Teach You A Lesson   = Get Schooled       (Netflix English vs Korean manhwa)
    Attack on Titan      = Shingeki no Kyojin (English vs romanised Japanese)
    All of Us Are Dead   = 지금 우리 학교는       (English vs Korean)

Left alone these compete as separate answers and split the vote, which is
exactly what happened on the first Short tested: "teach you a lesson" came
back with 26 commenters and "Get Schooled" as a separate entry with 1.

Two sources of truth, neither needing a full catalogue:

1. People say it outright - "in Korean the title is X", "aka X", "based on
   the manhwa X". An alias stated in a video's comments is an alternate name
   for whatever that video is, so it attaches to that video's answer rather
   than competing with it.
2. The catalogue, for works already known.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .extract import clean
from .gazetteer import Gazetteer, fold

LANGS = (r"korean|japanese|chinese|english|spanish|french|german|hindi|thai"
         r"|vietnamese|indonesian|original|japan|korea|china")

ALIAS_FRAMES: list[tuple[str, str]] = [
    # "In Korean, the title is "Get Schooled""
    ("translated", rf"\b(?:in|the)\s+(?:{LANGS})\s*(?:,\s*)?(?:the\s+)?"
                   rf"(?:title|name|version)?\s*(?:is|:|it'?s|was)\s*[\"“']?([^\n\"“”'.!?]{{2,60}})"),
    ("translated", rf"\b(?:{LANGS})\s+(?:title|name)\s*(?:is|:)\s*[\"“']?([^\n\"“”'.!?]{{2,60}})"),
    ("aka",        r"\b(?:a\.?k\.?a\.?|also\s+known\s+as|otherwise\s+known\s+as)\s*[:\-]?\s*"
                   r"[\"“']?([^\n\"“”'.!?]{2,60})"),
    ("source",     r"\bbased\s+on\s+(?:the\s+)?(?:manhwa|manga|webtoon|novel|book|comic)\s+"
                   r"[\"“']?([^\n\"“”'.!?]{2,60})"),
    ("source",     r"\bthe\s+(?:manhwa|manga|webtoon|novel)\s+(?:is\s+)?(?:called|named|titled)\s+"
                   r"[\"“']?([^\n\"“”'.!?]{2,60})"),
    ("original",   r"\boriginal(?:ly)?\s+(?:title|name)?\s*(?:is|was|called|titled)\s*[:\-]?\s*"
                   r"[\"“']?([^\n\"“”'.!?]{2,60})"),
]

_COMPILED = [(k, re.compile(p, re.IGNORECASE)) for k, p in ALIAS_FRAMES]

_STOP = {"the", "a", "an", "same", "it", "this", "that", "different", "better",
         "good", "amazing", "peak", "fire", "not", "no", "yes", "called",
         "titled", "named", "english", "korean", "japanese", "chinese"}


@dataclass
class Alias:
    name: str
    kind: str          # translated | aka | source | original | catalogue
    commenters: int
    example: str = ""


# YouTube replies begin with the handle being replied to, which glues onto the
# next word ("@shbani423you both can also fight later").
_HANDLE = re.compile(r"^\s*@[\w.\-]+", re.UNICODE)


def _clean_name(raw: str) -> str:
    from .discover import _clean_slot
    return _clean_slot(_HANDLE.sub(" ", raw))


def _plausible_alias(name: str) -> bool:
    """Cheap quality gate. Alias extraction is the weakest part of this
    pipeline (see README) - these rules cut the worst of it, not all of it."""
    if "@" in name or "http" in name.lower():
        return False
    words = name.split()
    if not (1 <= len(words) <= 5):
        return False
    # Unbalanced brackets mean the span was cut out of something larger,
    # e.g. "Goblin) for the US".
    for l, r in (("(", ")"), ("[", "]")):
        if name.count(l) != name.count(r):
            return False
    return True


def stated_aliases(comments: list[dict]) -> list[Alias]:
    """Alternate names people state in this video's comments."""
    found: dict[str, dict] = {}
    for c in comments:
        text = clean(c.get("text", ""))
        if not text:
            continue
        for kind, pat in _COMPILED:
            for m in pat.finditer(text):
                name = _clean_name(m.group(1))
                f = fold(name)
                if not f or len(f) < 3 or f in _STOP:
                    continue
                if all(w in _STOP for w in f.split()):
                    continue
                if not _plausible_alias(name):
                    continue
                rec = found.setdefault(f, {"name": name, "kind": kind,
                                           "authors": set(), "example": text})
                rec["authors"].add(c.get("author", ""))
    return [Alias(r["name"], r["kind"], len(r["authors"]), r["example"][:110])
            for r in found.values()]


def catalogue_aliases(title: str, gaz: Gazetteer) -> list[Alias]:
    """Known alternate names for a title already in the catalogue."""
    canonical = gaz.alias_to_canonical.get(fold(title))
    if not canonical:
        return []
    t = gaz.titles.get(canonical)
    if not t:
        return []
    return [Alias(a, "catalogue", 0) for a in t.aliases if fold(a) != fold(title)]
