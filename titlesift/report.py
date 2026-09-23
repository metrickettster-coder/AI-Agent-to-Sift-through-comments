"""Product B: what people are asking about and recommending, in aggregate.

A single video's answer can be wrong without much consequence here, because
nothing downstream depends on any one row - which is exactly why this is
shippable at accuracy the per-video answer is not. A ranking over hundreds of
videos absorbs a bad row; a wrong answer under someone's Short does not.

Input is a list of per-video analyses. Output is the report: titles ranked by
how many independent videos carry them, sliced by language and genre, with the
demand that went unanswered.
"""
from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field, asdict

from .gazetteer import fold, LEADING_ARTICLES, MEDIA_WORDS
from .metadata import is_descriptor
from .disambiguate import FUNCTION_WORDS

from rapidfuzz import fuzz
from wordfreq import zipf_frequency

CONF_WEIGHT = {"high": 1.0, "medium": 0.5, "low": 0.0}

# The sweep's search terms map onto rough market segments.
GENRE = {
    "kdrama": "Korean", "korean": "Korean", "cdrama": "Chinese",
    "chinese": "Chinese", "anime": "Anime", "donghua": "Chinese animation",
    "manhwa": "Manhwa/Webtoon", "webtoon": "Manhwa/Webtoon",
    "turkish": "Turkish", "telenovela": "Spanish-language",
    "bollywood": "Indian", "indian": "Indian", "thai": "Thai",
    "japanese": "Japanese", "jdrama": "Japanese", "movie": "Film",
    # Not every search names a country. These come last so "kdrama funny
    # scene" still reads as Korean rather than as a theme.
    "what drama": "Identification query", "school": "School drama",
    "romance": "Romance", "revenge": "Revenge", "drama": "General drama",
}


_JUNK_EDGE = re.compile(r"^[^\w(\[]+|[^\w)\]]+$", re.U)


def clean(title: str) -> str:
    """Trim the punctuation a commenter left on the end of a title."""
    t = _JUNK_EDGE.sub("", title.strip())
    # Unbalanced closers survive the edge trim ("Resident playbook)}").
    while t and t[-1] in ")]}" and t.count(t[-1]) > t.count({")": "(", "]": "[", "}": "{"}[t[-1]]):
        t = t[:-1].rstrip()
    return t


def group_key(title: str) -> str:
    """The key two spellings of the same show must share.

    Folding alone is not enough: "A Business Proposal" and "Business
    Proposal" are one show and were ranking as two, which splits the vote
    the whole product is built on measuring.
    """
    # fold() turns an apostrophe into a space, which split "Omniscient
    # Reader's Viewpoint" from "Omniscient Readers Viewpoint" into two rows
    # of the same show. Drop it here rather than in fold(), which the whole
    # matcher indexes on.
    k = fold(clean(title).replace("'", "").replace("’", ""))
    for art in LEADING_ARTICLES:
        if k.startswith(art):
            k = k[len(art):]
            break
    parts = k.split()
    while len(parts) > 1 and parts[-1] in MEDIA_WORDS:
        parts.pop()
    return " ".join(parts)


def _case_score(t: str) -> int:
    """Prefer the spelling a reader would accept as the title."""
    words = [w for w in t.split() if w]
    if not words:
        return -1
    if t.isupper():
        return 0
    if t.islower():
        return 1
    capped = sum(1 for w in words if w[:1].isupper())
    return 3 if capped == len(words) else 2


def _lexical(t: str) -> float:
    """How many of the words are real words.

    Without this the shorter spelling won ties and "unflrgetable love"
    was printed as the title over "unforgettable love".
    """
    words = [w for w in fold(t).split() if w.isalpha()]
    if not words:
        return 0.0
    # A ratio, not a count: counting favours whichever spelling has more
    # words, which is how "Exclusive fairytale" lost to "Exclusive fairy
    # tail" - both are real words, only one is the show.
    return sum(1 for w in words if zipf_frequency(w, "en") > 0) / len(words)


def display_form(variants: collections.Counter) -> str:
    """Pick the best-cased, most complete spelling actually observed."""
    # Real words beat frequency: a typo repeated twice ("unflrgetable love")
    # outvoted the correct spelling and then taught the catalogue the typo.
    best = max(variants, key=lambda t: (_case_score(t), t[:1].isupper(),
                                        _lexical(t), variants[t], len(t)))
    return best[:1].upper() + best[1:] if best[:1].islower() else best


_HAS_DIGIT = re.compile(r"\d")


def _acronym_of(alt: str, canonical: str) -> bool:
    words = [w for w in group_key(canonical).split() if w]
    if len(words) < 2:
        return False
    return fold(alt).replace(" ", "") == "".join(w[0] for w in words)


def variant_of(alt: str, canonical: str) -> bool:
    """A different spelling of the same words, not a different name."""
    a, c = group_key(alt), group_key(canonical)
    if not a or not c:
        return False
    if a.replace(" ", "") == c.replace(" ", ""):
        return True
    if _acronym_of(alt, canonical):
        return True
    return fuzz.ratio(a, c) >= 85


def alt_is_clean(alt: str) -> bool:
    """Reject the channel handles and stray fragments the alias layer emits."""
    words = alt.split()
    if not (1 <= len(words) <= 5) or len(alt) < 2:
        return False
    if _HAS_DIGIT.search(alt) or "@" in alt or "/" in alt:
        return False
    return not any(len(w) == 1 and w.isalpha() for w in words)


def genre_of(query: str) -> str:
    q = (query or "").lower()
    for k, v in GENRE.items():
        if k in q:
            return v
    return "Other"


@dataclass
class TitleRow:
    title: str
    videos: int = 0
    commenters: int = 0
    likes: int = 0
    demand: int = 0                 # people asking on videos where this is the answer
    confidence: float = 0.0         # mean tier weight
    genres: dict = field(default_factory=dict)
    languages: dict = field(default_factory=dict)
    alternates: list = field(default_factory=list)
    views: int = 0
    variants: list = field(default_factory=list)
    confirmed: bool = False         # a title database knows this work
    kind: str = ""                  # "K-drama", "Manhwa", ... from the database
    year: int | None = None
    url: str = ""                   # the database page it was confirmed against
    entities: dict = field(default_factory=dict)   # entity id -> videos

    def to_dict(self) -> dict:
        return asdict(self)


def answers_of(results: list[dict], min_conf: str = "medium"):
    """(video, top answer, cleaned title) for every answer the report counts."""
    floor = CONF_WEIGHT[min_conf]
    for r in results:
        answers = r.get("answers") or []
        if not answers:
            continue
        top = answers[0]
        if CONF_WEIGHT.get(top.get("conf", "low"), 0.0) < floor:
            continue
        shown = clean(top["title"])
        if is_descriptor(shown):
            continue
        if all(w in FUNCTION_WORDS for w in fold(shown).split()):
            continue    # "And then" - a repeated catchphrase, not a title
        key = group_key(shown)
        if not key or len(key) < 3:
            continue
        yield r, top, extend_from_metadata(shown, r.get("title", ""))


def _dedupe_alts(alts: list[str], limit: int = 5) -> list[str]:
    """One entry per name, in its best-cased spelling."""
    best: dict[str, str] = {}
    for a in alts:
        k = group_key(a)
        if k not in best or _case_score(a) > _case_score(best[k]):
            best[k] = a
    return list(best.values())[:limit]


def _db_display(primary: str, ents: list, spellings: collections.Counter) -> str:
    """The database's name for the work, in the form the audience knows it.

    The primary name wins unless no commenter used it and they all used
    another name the database also lists: Regressor Instruction Manual is
    AniList's synonym for "How to Use a Returner", and it is the name every
    commenter and the official English webtoon use.
    """
    from .resolve import Entity, _keys, matches
    said = spellings.most_common(1)[0][0] if spellings else primary
    prim = Entity("x", "x", primary, [primary], "")
    if not any(matches(sp, prim, group_key) for sp in spellings):
        for e in ents:
            for n in e.names:
                if n.isascii() and matches(said, Entity("x", "x", n, [n], ""), group_key):
                    primary = n.strip()
                    break
            else:
                continue
            break
    if primary.isupper() and len(primary) > 3:
        # AniList writes some titles in capitals ("NARUTO", "BLUE LOCK").
        same = [sp for sp in spellings if group_key(sp) == group_key(primary)
                and not sp.isupper() and not sp.islower()]
        primary = same[0] if same else primary.title()
    return primary


def _db_alternates(ents: list, title: str) -> list[str]:
    """Official alternate names worth printing: the romanised original title.

    Only AniList's romaji is used. Wikipedia redirects and AniList synonyms
    include every fan translation and every script, which is noise on a
    ranking page.
    """
    out = []
    for e in ents:
        if e.source != "anilist":
            continue
        for n in e.names[1:2]:           # names[1] is the romaji title
            if (n and n.isascii() and group_key(n) != group_key(title)
                    and len(n.split()) <= 6 and n not in out):
                out.append(n)
    return out[:1]


def build(results: list[dict], min_conf: str = "medium",
          caches: dict | None = None) -> dict:
    """results: the per-video records produced by the sweep.

    caches: title-database caches from resolve.open_caches(). When given,
    every answer is checked against AniList, Wikipedia and MangaDex: confirmed
    answers are grouped under the database's name for the work, answers the
    database contradicts are dropped, and the rest are reported separately
    as unconfirmed.
    """
    from . import resolve

    rows: dict[str, TitleRow] = {}
    spellings: dict[str, collections.Counter] = {}
    db_names: dict[str, collections.Counter] = {}
    alt_counts: dict[str, collections.Counter] = {}
    row_ents: dict[str, dict] = {}
    rejected: list[dict] = []

    for r, top, shown in answers_of(results, min_conf):
        ent, status = None, "unconfirmed"
        if caches is not None:
            ctx = r.get("query", "") + " " + r.get("title", "")
            segs = segments_of(ctx)
            ents = resolve.lookup(shown, caches, group_key)
            ent, status = choose_entity(shown, ents, segs, live_action(segs, ctx))
            commented = any(s.startswith("comments") for s in top.get("sources", []))
            # The database knows this name only as something the video cannot
            # be, and no commenter stated it: a common word the catalogue
            # matched ("Monster" under a telenovela). Drop it.
            if status == "contradicted" and not commented:
                rejected.append({"video": r.get("vid"), "title": shown,
                                 "query": r.get("query"),
                                 "known_as": sorted({f"{kind_label(e)} ({e.source})"
                                                     for e in ents})})
                continue
        # Key on the database's own name when the commenter used it (or its
        # short form), otherwise on the name they used: AniList files "When
        # the Phone Rings" under its romaji title, and keying on that split
        # the manhwa from the drama of the same name. Rows naming the same
        # entity are merged below either way.
        if ent and group_key(shown) in resolve._keys(ent.name, group_key):
            key = group_key(ent.name)
        else:
            key = group_key(shown)
        if not key:
            continue
        row = rows.setdefault(key, TitleRow(title=shown))
        spellings.setdefault(key, collections.Counter())[shown] += 1
        if ent:
            row.confirmed = True
            row.entities[ent.id] = row.entities.get(ent.id, 0) + 1
            row_ents.setdefault(key, {})[ent.id] = ent
            db_names.setdefault(key, collections.Counter())[ent.name] += 1
        row.videos += 1
        row.commenters += top.get("commenters", 0)
        row.likes += top.get("likes", 0)
        row.demand += r.get("asked", 0)
        row.views += r.get("views", 0)
        row.confidence += CONF_WEIGHT.get(top.get("conf", "low"), 0.0)
        g = genre_of(r.get("query", ""))
        row.genres[g] = row.genres.get(g, 0) + 1
        for lang, n in (r.get("langs") or {}).items():
            row.languages[lang] = row.languages.get(lang, 0) + n
        for a in top.get("alternates", []):
            a = clean(a[0] if isinstance(a, (list, tuple)) else a)
            if group_key(a) and group_key(a) != key and alt_is_clean(a):
                alt_counts.setdefault(key, collections.Counter())[a] += 1

    def finish_names(key: str, row: TitleRow) -> None:
        counts = spellings.get(key) or collections.Counter([row.title])
        ents = list(row_ents.get(key, {}).values())
        if ents:
            # The database's name for the work, not a commenter's spelling.
            best = row_ents[key][max(row.entities, key=row.entities.get)]
            row.title = _db_display(db_names[key].most_common(1)[0][0], ents, counts)
            row.kind, row.year, row.url = kind_label(best), best.year, best.url
            known = {nk for e in ents for n in e.names
                     for nk in resolve._keys(n, group_key)}
            # A confirmed title's alternate must be a name the database
            # knows or a respelling. "saitama" is the hero of One Punch Man,
            # not another name for it, and recurring twice did not change that.
            observed = [a for a, _n in
                        (alt_counts.get(key) or collections.Counter()).most_common()
                        if group_key(a) in known or variant_of(a, row.title)]
            official = best.name if group_key(best.name) != group_key(row.title) else None
            row.alternates = _dedupe_alts(([official] if official else []) + observed
                                          + _db_alternates(ents, row.title))
        else:
            row.title = display_form(counts)
            # An alternate earns its place the same way everything else here
            # does: it recurs across videos, or it is visibly the same words.
            # Alone and unlike the title it is usually an actor, a character
            # or a channel handle, and a wrong alias on the page is worse
            # than a missing one.
            row.alternates = [a for a, n in
                              (alt_counts.get(key) or collections.Counter()).most_common()
                              if n >= 2 or variant_of(a, row.title)][:5]
        row.variants = [t for t, _ in counts.most_common() if t != row.title]

    for key, row in rows.items():
        row.confidence = round(row.confidence / max(row.videos, 1), 2)
        finish_names(key, row)

    # The same argument that groups a show's alternate names applies to the
    # rows themselves: "F4 Thiland" and "F4 Thailand" ranked separately and
    # split the count the whole report is measuring. Two rows a database
    # confirmed as different works are never merged on spelling alone.
    order = sorted(rows, key=lambda k: (-rows[k].videos, -rows[k].commenters))
    merged: dict[str, str] = {}
    for key in order:
        if key in merged:
            continue
        for other in order:
            if other == key or other in merged or len(other) < 6:
                continue
            if rows[other].videos > rows[key].videos:
                continue
            # "School 2017" and "School 2021" are different shows one
            # character apart.
            if re.findall(r"\d+", key) != re.findall(r"\d+", other):
                continue
            same_work = bool(set(rows[key].entities) & set(rows[other].entities))
            both_known = rows[key].confirmed and rows[other].confirmed
            ratio = fuzz.ratio(key, other)
            # Two confirmed rows are merged on spelling only at typo
            # distance: "When the phone Ring" is the drama's webtoon, but
            # "Hidden Love" and "Hidden Lover" are two shows.
            if same_work or (ratio >= 90 and not both_known) or ratio >= 96:
                merged[other] = key
    for src, dst in merged.items():
        while dst in merged:
            dst = merged[dst]
        a, b = rows.pop(src), rows[dst]
        b.videos += a.videos
        b.commenters += a.commenters
        b.likes += a.likes
        b.demand += a.demand
        b.views += a.views
        b.variants.append(a.title)
        # The row with more videos behind it keeps its name. Re-running
        # display_form here compared two already-chosen spellings with no
        # frequency behind either, and picked on string shape alone.
        if not b.confirmed and not a.confirmed and _case_score(a.title) > _case_score(b.title):
            b.title = a.title
        if a.confirmed and not b.confirmed:
            b.title, b.kind, b.year, b.url = a.title, a.kind, a.year, a.url
            b.confirmed = True
        for eid, n in a.entities.items():
            b.entities[eid] = b.entities.get(eid, 0) + n
        for g, n in a.genres.items():
            b.genres[g] = b.genres.get(g, 0) + n
        for l, n in a.languages.items():
            b.languages[l] = b.languages.get(l, 0) + n
        b.alternates = list(dict.fromkeys(b.alternates + [
            x for x in a.alternates if group_key(x) != group_key(b.title)]))[:5]

    # A row no commenter ever typed is the video's own metadata talking.
    # It may still be the right answer, but it is not evidence of demand,
    # and the descriptor phrases that survive the filters all land here.
    for key in [k for k, r in rows.items() if r.commenters < 1]:
        del rows[key]

    ranked = sorted(rows.values(),
                    key=lambda t: (-t.videos, -t.commenters, -t.likes))
    if caches is not None:
        titles = [t for t in ranked if t.confirmed]
        # A name no database knows is still worth showing: it may be new or
        # obscure, which is exactly what a buyer is looking for. An ordinary
        # English word no database knows is not ("Filter", "Reborn").
        unconfirmed = [t for t in ranked if not t.confirmed and not (
            len(group_key(t.title).split()) == 1
            and zipf_frequency(group_key(t.title), "en") >= 3.0)]
    else:
        titles, unconfirmed = ranked, []

    total_videos = len(results)
    total_asked = sum(r.get("asked", 0) for r in results)
    with_demand = [r for r in results if r.get("asked", 0) > 0]
    answered_hi = [r for r in results
                   if (r.get("answers") or [{}])[0].get("conf") == "high"]
    gap = [r for r in with_demand
           if (r.get("answers") or [{}])[0].get("conf") != "high"]

    by_genre = collections.Counter(genre_of(r.get("query", "")) for r in results)
    by_kind = collections.Counter()
    for t in titles:
        by_kind[t.kind] += t.videos
    lang_total: dict = collections.Counter()
    # Raw comment counts make one huge video look like a market. Reach counts
    # how many separate videos carry real volume in a language instead.
    lang_reach: dict = collections.Counter()
    for r in results:
        for k, v in (r.get("langs") or {}).items():
            lang_total[k] += v
            if k != "en/other" and v >= 3:
                lang_reach[k] += 1

    return {
        "videos": total_videos,
        "comments": sum(r.get("n_comments", 0) for r in results),
        "people_asking": total_asked,
        "videos_with_demand": len(with_demand),
        "videos_answered_high": len(answered_hi),
        "demand_gap": len(gap),
        "distinct_titles": len(titles),
        "confirmed": caches is not None,
        "titles": [t.to_dict() for t in titles],
        "unconfirmed": [t.to_dict() for t in unconfirmed],
        "rejected": rejected,
        "by_kind": dict(by_kind.most_common()),
        "by_genre": dict(by_genre.most_common()),
        "by_language": dict(lang_total.most_common(12)),
        "language_reach": dict(lang_reach.most_common(12)),
        "non_english_comments": sum(v for k, v in lang_total.items()
                                    if k != "en/other"),
        "gap_examples": [
            {"title": r["title"], "asked": r["asked"]}
            for r in sorted(gap, key=lambda r: -r.get("asked", 0))[:10]
        ],
    }


def to_text(rep: dict, top: int = 25) -> str:
    L = [f"{rep['videos']} videos | {rep['comments']:,} comments | "
         f"{rep['people_asking']:,} people asking",
         f"{rep['distinct_titles']} distinct titles | "
         f"{rep['videos_with_demand']} videos with demand, "
         f"{rep['demand_gap']} unanswered", ""]
    L.append(f"{'title':<38}{'vids':>5}{'ppl':>6}{'demand':>8}  languages")
    L.append("-" * 84)
    for t in rep["titles"][:top]:
        langs = ",".join(k for k, _ in sorted(t["languages"].items(),
                                              key=lambda kv: -kv[1])[:3])
        L.append(f"{t['title'][:36]:<38}{t['videos']:>5}{t['commenters']:>6}"
                 f"{t['demand']:>8}  {langs}")
    return "\n".join(L)


def extend_from_metadata(title: str, video_title: str) -> str:
    """Recover a numbered title the answer lost: "School" -> "School 2021".

    Commenters and the frames that read them drop the year off titles like
    "School 2021" and "School 2017", leaving an ordinary word that no
    database can confirm and that merges two different shows. The video's
    own title or hashtag usually still carries it, glued or not.
    """
    t = fold(title)
    if not t or len(t.split()) > 3:
        return title
    m = re.search(r"(?:^|\s)" + re.escape(t) + r"\s?(\d{1,4})(?:\s|$)",
                  fold(video_title or ""))
    if not m or t.endswith(m.group(1)):
        return title
    return f"{title} {m.group(1)}"


# ------------------------------------------------------ database confirmation
#
# The sweep's search term and the video's own title say what kind of thing
# the answer should be. A telenovela clip is not answered by a Japanese
# manga, however many commenters typed the word "monster".

_SPANISH = {"MX", "ES", "CO", "AR", "VE", "CL", "PE"}
_COUNTRY_FITS = {
    "Korean": lambda e: e.country == "KR",
    "Chinese": lambda e: e.country in ("CN", "TW", "HK"),
    "Chinese animation": lambda e: e.country == "CN",
    "Thai": lambda e: e.country == "TH",
    "Japanese": lambda e: e.country == "JP",
    "Indian": lambda e: e.country in ("IN", "PK"),
    "Turkish": lambda e: e.country == "TR",
    "Spanish-language": lambda e: e.spanish or e.country in _SPANISH,
}
_FORM_FITS = {
    "Anime": lambda e: e.kind in ("anime", "donghua", "manga", "manhua",
                                  "manhwa", "novel"),
    "Manhwa/Webtoon": lambda e: e.kind in ("manhwa", "manhua", "manga",
                                           "anime", "donghua", "novel")
                                or e.country == "KR",
    "Chinese animation": lambda e: e.kind in ("donghua", "manhua", "novel"),
    "Film": lambda e: e.kind == "film",
}
SEGMENT_FITS = {**_FORM_FITS, **_COUNTRY_FITS}


def _fits(e, segs: set) -> tuple[bool, bool]:
    """(fits this video at all, fits it on a known country).

    The country a video was found under is the strong signal: a telenovela
    clip is not answered by a Japanese manga. The form is weaker, since
    people call a drama a movie, so it only decides when no country does.
    An entity the database gives no country is not held against a country.
    """
    countries = [s for s in segs if s in _COUNTRY_FITS]
    forms = [s for s in segs if s in _FORM_FITS]
    # Nothing ties the entity to the video but its name, so it has to at
    # least be from the era these clips come from. "Marry Me Again" under a
    # 2020s romance Short is not the 1953 film of that name.
    old = e.kind in ("drama", "film") and e.year is not None and e.year < 1995
    if countries:
        if any(_COUNTRY_FITS[s](e) for s in countries):
            return True, True
        drawn = [s for s in forms if s != "Film"]
        # Search results are loose enough that a country-less hit is usually
        # a namesake: searching "Unlocked" for a Korean film finds the 2017
        # British one. Only an exact article title is given the benefit.
        if e.country is None and e.kind in ("drama", "film") and not old \
                and not e.searched and (
                not drawn or any(_FORM_FITS[s](e) for s in drawn)):
            return True, False
        return False, False
    if forms:
        return any(_FORM_FITS[s](e) for s in forms) and not old, False
    return not old, False


_DRAWN = {"Anime", "Manhwa/Webtoon", "Chinese animation"}
_LIVE = {"Korean", "Chinese", "Thai", "Japanese", "Indian", "Turkish",
         "Spanish-language", "Film"}


def segments_of(text: str) -> set:
    """Every segment a video's search term and title point at, not just one."""
    t = (text or "").lower()
    return {v for k, v in GENRE.items() if k in t}


def live_action(segs: set, text: str) -> bool:
    if segs & _DRAWN:
        return False
    return bool(segs & _LIVE) or bool(re.search(r"drama|movie|film|scene|series",
                                                (text or "").lower()))


def choose_entity(title: str, ents: list, segs: set, live: bool):
    """Pick the entity this video's answer names, or say why there is none.

    Returns (entity, status): status is "confirmed", "unconfirmed" (no
    database knows it) or "contradicted" (databases know the name, but only
    as something this video cannot be).
    """
    if not ents:
        return None, "unconfirmed"
    fits = [e for e in ents if _fits(e, segs)[0]]
    if not fits:
        return None, "contradicted"
    from .resolve import _keys
    k = group_key(title)
    src = {"wikipedia": 0, "anilist": 1, "mangadex": 2}
    if segs & {"Anime", "Chinese animation"}:
        want = {"anime", "donghua"}
    elif "Manhwa/Webtoon" in segs:
        want = {"manhwa", "manhua"}
    elif live:
        want = {"drama", "film"}
    else:
        want = set()

    def score(e):
        # A synonym match is weaker than the work's own name: "Onigiri" lists
        # "Demon Slayer" as a synonym and is not Demon Slayer.
        primary = k in _keys(e.name, group_key)
        return (primary, _fits(e, segs)[1], e.kind in want,
                -src[e.source] if live else src[e.source] == 1,
                e.popularity if e.source == "anilist" else -e.rank)
    return max(fits, key=score), "confirmed"


_LABEL_DRAMA = {"KR": "K-drama", "CN": "C-drama", "TW": "Taiwanese drama",
                "HK": "Hong Kong drama", "TH": "Thai drama", "JP": "J-drama",
                "IN": "Indian series", "PK": "Pakistani drama",
                "TR": "Turkish series", "PH": "Filipino series"}
_LABEL_FILM = {"KR": "Korean film", "CN": "Chinese film", "JP": "Japanese film",
               "IN": "Indian film", "TH": "Thai film", "US": "American film"}


def kind_label(e) -> str:
    if e.kind == "drama":
        if e.spanish or e.country in _SPANISH:
            return "Telenovela"
        return _LABEL_DRAMA.get(e.country, "TV series")
    if e.kind == "film":
        return _LABEL_FILM.get(e.country, "Film")
    return {"anime": "Anime", "donghua": "Donghua", "manga": "Manga",
            "manhwa": "Manhwa", "manhua": "Manhua", "novel": "Novel"}[e.kind]
