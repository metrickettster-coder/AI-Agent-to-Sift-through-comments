"""Open-vocabulary title discovery - finding titles with no catalogue.

The gazetteer path can only find titles it already knows, which is fine for
recommendation videos but useless on a clip or edit, where the whole comment
section is one question ("what's this called?") and the answer is a title
nobody has ever put in a database you own.

But people answer that question in a small number of recognisable shapes:

    Drama Name :Teach You A Lesson
    Anyone wondering it's called TEACH YOU A LESSON on Netflix
    Anyone asking for the title, "May I Help You" available on Netflix
    May I Help You kdrama 2022
    In Korean, the title is "Get Schooled"

So instead of matching against known titles, match against the *frames* people
use to state one, take whatever sits in the slot, and let agreement between
commenters decide what is real. Likes matter here in a way they do not for
recommendation videos: the right answer gets voted up hard, because everyone
in the thread wanted it.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from rapidfuzz import fuzz

from .extract import clean
from .disambiguate import ambiguity
from .gazetteer import fold

MEDIA = r"(?:k-?drama|drama|show|movie|film|series|manhwa|manhua|webtoon|manga|anime|book|novel)"

# ---------------------------------------------------------------- multilingual
#
# "What is this called?" is asked in every language on YouTube, and answered in
# the asker's. Every pattern below was taken from real comments in an 8,342
# comment corpus, not translated by guesswork. 97% of that corpus was Latin
# script, so romanised Hindi and Turkish matter as much as Cyrillic or Arabic.
#
# Each frame captures the slot after a "it is called / the name is" phrase.
LANG_FRAMES: list[tuple[str, str]] = [
    # Spanish - "La serie se llama Estamos muertos"
    ("es", r"se\s+llama[n]?\s*:?\s*([^\n.!?¡¿]{2,60})"),
    ("es", r"(?:el\s+)?(?:nombre|t[ií]tulo)\s+(?:es|de\s+la\s+serie\s+es)\s*:?\s*([^\n.!?¡¿]{2,60})"),
    ("es", r"(?:nombre|t[ií]tulo)\s*:\s*([^\n.!?¡¿]{2,60})"),
    # Portuguese - "o nome é X" / "se chama X"
    ("pt", r"se\s+chama\s*:?\s*([^\n.!?]{2,60})"),
    ("pt", r"(?:o\s+)?nome\s+(?:é|eh|e)\s*:?\s*([^\n.!?]{2,60})"),
    # Russian - "Как называется" is the question; "называется X" the answer
    ("ru", r"называется\s*:?\s*([^\n.!?]{2,60})"),
    ("ru", r"назван[ие]{2}\s*[:\-]\s*([^\n.!?]{2,60})"),
    # Turkish - "adı X" / "ismi X"
    ("tr", r"(?:dizinin|filmin|animenin|dizi|film)\s+(?:ad[ıi]|ismi)\s*:?\s*([^\n.!?]{2,60})"),
    ("tr", r"(?:ad[ıi]|ismi)\s*:\s*([^\n.!?]{2,60})"),
    # Romanised Hindi/Urdu - "movie ka naam X hai"
    ("hi", r"na+m\s*[:\-]\s*([^\n.!?]{2,60})"),
    ("hi", r"([^\n.!?]{2,50}?)\s+na+m\s+ha[iy]\b"),
    ("hi", r"na+m\s+([^\n.!?]{2,50}?)\s+ha[iy]\b"),
    # Arabic - "اسم المسلسل X"
    ("ar", r"اسم\s+(?:المسلسل|الفيلم|الانمي|الأنمي)\s*:?\s*([^\n.!?]{2,60})"),
    # Indonesian/Malay - "judulnya X"
    ("id", r"judul(?:nya)?\s*:?\s*([^\n.!?]{2,60})"),
    # French / German / Italian
    ("fr", r"(?:ça|ca|cela)\s+s'appelle\s*:?\s*([^\n.!?]{2,60})"),
    ("fr", r"le\s+(?:nom|titre)\s+est\s*:?\s*([^\n.!?]{2,60})"),
    ("de", r"hei(?:ß|ss)t\s*:?\s*([^\n.!?]{2,60})"),
    ("it", r"si\s+chiama\s*:?\s*([^\n.!?]{2,60})"),
    # Vietnamese / Thai / Korean / Japanese / Chinese / Tagalog / Polish
    ("vi", r"t[êe]n\s+(?:phim|truy[eệ]n)\s+l[àa]\s*:?\s*([^\n.!?]{2,60})"),
    ("th", r"ชื่อเรื่อง\s*:?\s*([^\n.!?]{2,60})"),
    ("ko", r"제목\s*(?:은|는)?\s*:?\s*([^\n.!?]{2,60})"),
    ("ja", r"タイトルは\s*:?\s*([^\n.!?]{2,60})"),
    ("zh", r"(?:名字|片名)\s*(?:是|叫)?\s*:?\s*([^\n.!?]{2,60})"),
    ("tl", r"pamagat\s*(?:ay)?\s*:?\s*([^\n.!?]{2,60})"),
    ("pl", r"tytu[łl]\s*:?\s*([^\n.!?]{2,60})"),
]

# Asking, not answering. Used to size demand, and to reject a slot that is
# actually part of a question ("Movie ka naam kya hai").
QUESTION_WORDS = {
    "kya", "kaunsi", "konsi", "kon", "qué", "que", "cual", "cuál", "como",
    "cómo", "qual", "quel", "quelle", "was", "wie", "cosa", "come", "что",
    "как", "какой", "ما", "وش", "ايش", "أيش", "apa", "apakah", "ne", "nedir",
    "hangi", "gì", "อะไร", "뭐", "뭐야", "何", "什么", "ano", "jaki", "naam?",
    "name?", "title?",
}

QUESTION_PATTERNS = re.compile(
    r"what(?:'?s| is)\s+(?:the\s+)?(?:name|title|drama|show|movie|anime)"
    r"|name\s*\?|title\s*\?"
    r"|c[óo]mo\s+se\s+llama|cu[áa]l\s+es\s+el\s+nombre|nombre\s*(?:\?|plis|pls|porfa|por\s+favor)"
    r"|qual\s+[ée]\s+o\s+nome|nome\s*\?"
    r"|как\s+(?:\w+\s+){0,2}называется|назван[ие]{2}\s*(?:\?|плиз|плз|пожалуйста)"
    r"|ad[ıi]\s+ne|ismi\s+ne"
    r"|na+m\s+kya|kya\s+na+m|na+m\s+(?:bta|batao|bolo)"
    r"|(?:وش|ما)\s*اسم|اسم\s*(?:المسلسل|الانمي)\s*\?"
    r"|judul(?:nya)?\s+(?:apa|apakah)\b|judul(?:nya)?\s*\?"
    r"|t[êe]n\s+phim\s+(?:l[àa]\s+)?g[ìi]"
    r"|제목\s*(?:이)?\s*뭐|タイトル\s*(?:は)?\s*\?|叫什么"
    r"|anong\s+(?:title|pamagat)",
    re.IGNORECASE,
)

# Each frame captures the slot where a title goes.
FRAMES: list[tuple[str, re.Pattern, int]] = [
    *[(f"lang:{lang}", re.compile(pat, re.IGNORECASE), 3) for lang, pat in LANG_FRAMES],
    # "name is X" alone is not enough: "His name is Adi" and "His full name is
    # Paul Allen" are characters, and they outranked the real answers. A
    # lookbehind could not fix it, because \s* lets the match start on the
    # space, moving the lookbehind past the possessive. Every genuine hit in
    # the corpus carried a media word or "the" ("movie name:All of us are
    # dead", "Drama name : May I help you", "the title is Get Schooled"), so
    # require that anchor instead.
    ("named",   re.compile(rf"\b(?:{MEDIA}|the)\s+(?:full\s+)?(?:name|title)\s*(?:is|:|-|=)\s*"
                           rf"[\"“']?([^\n\"“”']{{2,60}})", re.I), 3),
    ("named",   re.compile(r"(?:^|\n)\s*(?:name|title)\s*[:\-=]\s*[\"“']?([^\n\"“”']{2,60})", re.I), 3),
    ("called",  re.compile(r"\b(?:it'?s|its|it is|this is)\s+called\s+[\"“']?([^\n\"“”'.!?]{2,60})", re.I), 3),
    ("quoted",  re.compile(r"[\"“]([^\"“”]{2,60})[\"”]", 0), 2),
    # NOTE: no re.I on the anchor - under re.I, [A-Z] matches lowercase too,
    # which made this frame start mid-word ("it'S called ..." -> "s called ...").
    ("onplat",  re.compile(r"\b([A-Z][^\n\"“”'.!?]{1,58}?)\s+(?:is\s+)?(?:available\s+)?on\s+(?i:netflix|viki|disney|prime|crunchyroll|webtoon|tapas)\b"), 3),
    ("premedia", re.compile(rf"\b([A-Z][A-Za-z0-9'’!?:\- ]{{2,58}}?)\s+{MEDIA}\b"), 2),
    ("watchrec", re.compile(rf"\b(?:watch|read|search|look\s+up)\s+[\"“']?([A-Z][^\n\"“”'.!?]{{2,58}})", 0), 1),
]

# Where a captured slot should be cut short.
TRAIL = re.compile(
    r"(?:\s+|\s*\()(?:on\s+)?(?:netflix|viki|disney\+?|prime|crunchyroll|tapas|webtoons?"
    r"|available|and\s|but\s|its\s|it'?s\s|thanks?\s|you\s+can\s|from\s+\d{4}"
    r"|\(?\d{4}\)?|season\s*\d|ep\s*\d|episode\s*\d|y\s|e\s|et\s|und\s|и\s|ve\s|dan\s|v[àa]\s|و\s|aur\s|por\s+favor|plis|pls|gracias)\b.*$",
    re.I,
)
_EMOJI = re.compile("[" "\U0001F000-\U0001FAFF" "☀-➿" "️" "​" "]+")

# A slot holding only these is chatter, not a title.
JUNK = {
    "the", "this", "that", "it", "he", "she", "they", "what", "who", "why",
    "name", "title", "drama", "show", "movie", "series", "manhwa", "webtoon",
    "manga", "anime", "book", "novel", "kdrama", "k drama", "same", "me too",
    "yes", "no", "lol", "omg", "bro", "please", "pls", "thanks", "thank you",
    "anyone", "someone", "everyone", "nothing", "something", "idk", "good",
    "best", "peak", "fire", "trash", "mid", "season", "episode", "part one",
    "i love this", "so true", "real", "facts", "watch", "read", "netflix",
    # non-English chatter that lands in the same slots
    "la serie", "el nombre", "o nome", "la pelicula", "la película", "el anime",
    "название", "фильм", "сериал", "аниме", "манга", "дорама", "мультик",
    "manhwa ka", "movie ka", "dizi", "مسلسل", "المسلسل", "انمي", "فيلم",
    "pelicula", "película", "serie", "novela", "drama coreano",
    "dorama", "doramas", "dizisi", "telenovela", "seriya", "сериала",
    "film", "anime", "judul", "pamagat", "tytul", "tytuł", "제목", "タイトル",
    "por favor", "plis", "bhai", "please tell", "tell me", "sana", "ang",
}


# Frames differ enormously in how much they mean. "Drama name : X" is someone
# answering the question. A quoted span is usually dialogue, a lyric, or one
# commenter quoting another - across 14 random clip videos the quoted frame
# fired 86 times and produced "Yes it is" (60,628 likes), "Chill Out" and
# "Disclaimer: This". So a quote supports an answer but can never be one.
STRONG_FRAMES = {"named", "called", "onplat", "replyto"}  # + every lang:*
WEAK_FRAMES = {"quoted", "premedia", "watchrec"}


def _strength(frames: dict[str, int]) -> int:
    return sum(n for f, n in frames.items()
               if f in STRONG_FRAMES or f.startswith("lang:"))


@dataclass
class Discovery:
    title: str              # the most-liked spelling people used
    commenters: int         # distinct people who stated it
    likes: int              # total likes on the comments stating it
    frames: dict[str, int]  # which shapes it was found in
    example: str
    variants: list[str]


def _clean_slot(raw: str) -> str:
    s = _EMOJI.sub(" ", raw)
    s = s.lstrip("#@ ")          # "#f4 Thailand" -> "f4 Thailand"
    s = TRAIL.sub("", s)
    s = s.strip(" \t-–—:;,.!?\"'“”‘’()[]")
    s = re.sub(r"\s+", " ", s)
    if len(s.split()) > 8:
        return ""
    return s


def _is_title_case(s: str) -> bool:
    """Every significant word capitalised, or the whole thing shouted."""
    words = [w for w in s.split() if w]
    if not words:
        return False
    if s.isupper():
        return True
    minor = {"a", "an", "the", "of", "in", "on", "to", "and", "or", "for",
             "at", "by", "is", "i"}
    significant = [w for w in words if w.lower() not in minor]
    if not significant:
        return False
    # A title does not trail off in an article or preposition: "What is the"
    # sits in the same shape as "May I Help You" but is plainly a fragment.
    if words[-1].lower().strip(".,!?") in minor:
        return False
    return all(w[:1].isupper() for w in significant)


# Determiners in several languages. "la novela" and "esta novela" are "the
# soap opera" and "this soap opera" - media words wearing an article, and they
# came back as high-confidence titles on Spanish-language clips.
DETERMINERS = {
    "the", "a", "an", "this", "that",
    "la", "el", "las", "los", "un", "una", "esta", "este", "esa", "ese",
    "o", "as", "os", "um", "uma", "essa", "esse",
    "le", "les", "des", "du", "cette", "ce",
    "der", "die", "das", "ein", "eine", "il", "lo", "gli", "questa", "questo",
    "ye", "yeh", "bu", "şu", "ini", "itu",
}


def _strip_determiners(s: str) -> str:
    words = s.split()
    while words and words[0] in DETERMINERS:
        words.pop(0)
    return " ".join(words)


def _is_junk(s: str) -> bool:
    f = fold(s)
    # Check again with leading determiners removed, so "la novela" is caught
    # by the same rule that already catches "novela".
    bare = _strip_determiners(f)
    if bare and bare != f:
        if bare in JUNK or all(w in JUNK for w in bare.split()):
            return True
    if not f or len(f) < 3:
        return True
    if f in JUNK:
        return True
    # A slot holding a question word is the asker, not the answer.
    if any(w in QUESTION_WORDS for w in f.split()):
        return True
    # A slot of only very common words with no capitalised anchor is chatter.
    return all(w in JUNK for w in f.split())


# YouTube's plainText keeps a zero-width space where the @ mention was, so a
# plain "^@handle" strip left "landt41still going" behind as a candidate.
_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
_REPLY_HANDLE = re.compile(r"^(?:\s*@?[\w.\-]*\d[\w.\-]*[,:]?\s+|\s*@[\w.\-]+[,:]?\s*)+")
_REPLY_MAX_WORDS = 7
# Replies under a question are mostly conversation. These mark the ones that
# are talking rather than naming.
_CHATTER = re.compile(
    r"\b(?:thanks?|thank|thx|ty|welcome|sorry|np|yw|please|pls|lol|lmao|"
    r"i'?m|you'?re|i'?ve|we'?re|it'?s\s+not|agree|exactly|same|me\s+too|"
    r"idk|dunno|nope|yeah|yep|nah)\b", re.I)


def _thread_answers(comments: list[dict]) -> list[tuple[str, str, dict]]:
    """Read the reply tree, which every other frame here ignores.

    On a clip the answer is rarely a sentence anyone writes in the open. It
    is a bare reply under the person who asked - "What is the drama name
    please?" answered with "Lily fever" - and a flat pass over the comments
    cannot see it at all. Measured on 92 held-out videos, 22 of the 31 that
    had demand and no answer were carrying one of these.
    """
    by_id = {c.get("id"): c for c in comments}
    out: list[tuple[str, str, dict]] = []
    for c in comments:
        cid = c.get("id") or ""
        if "." not in cid:
            continue
        parent = by_id.get(cid.split(".", 1)[0])
        if parent is None:
            continue
        if not QUESTION_PATTERNS.search(clean(parent.get("text", ""))):
            continue
        body = _REPLY_HANDLE.sub("", _ZERO_WIDTH.sub("", c.get("text", "") or ""))
        # A reply that asks something back, thanks someone or argues is not
        # an answer; only a short bare naming is.
        if QUESTION_PATTERNS.search(clean(body)) or "?" in body:
            continue
        slot = _clean_slot(body)
        words = slot.split()
        if not (1 <= len(words) <= _REPLY_MAX_WORDS):
            continue
        if _is_junk(slot) or not re.search(r"[^\W\d_]{2}", slot):
            continue
        if "," in slot or _CHATTER.search(slot):
            continue
        # A stray single letter means the handle strip cut mid-word
        # ("@speeding_up3don't worry" -> "t worry").
        if len(words[0]) == 1 and words[0].lower() not in ("a", "i"):
            continue
        # "I hear you" and "I Saved You" have the same shape and only one is
        # a title. Ordinary wording needs the capitalisation to carry it.
        if ambiguity(slot) >= 4.0 and not _is_title_case(slot):
            continue
        out.append((slot, "replyto", c))
    return out


def demand(comments: list[dict]) -> tuple[int, int]:
    """How many people asked what this is, and how many likes those asks drew.

    On a clip the asking is the market signal: it is the unmet demand the
    answer would serve.
    """
    asks = [c for c in comments
            if QUESTION_PATTERNS.search(clean(c.get("text", "")))]
    return len(asks), sum(c.get("likes", 0) for c in asks)


def discover(comments: list[dict], min_commenters: int = 2,
             min_strong: int = 1) -> list[Discovery]:
    """Find titles stated in the comments without consulting any catalogue."""
    hits: list[tuple[str, str, dict]] = []   # (slot, frame_name, comment)
    for c in comments:
        text = clean(c.get("text", ""))
        if not text:
            continue
        for name, pat, _w in FRAMES:
            for m in pat.finditer(text):
                slot = _clean_slot(m.group(1))
                if not slot or _is_junk(slot):
                    continue
                # "Such a good K drama" / "This was such a good series" sit in
                # the same shape as "May I Help You kdrama", so this frame only
                # counts when the slot is actually written like a title.
                if name == "premedia" and not _is_title_case(slot):
                    continue
                hits.append((slot, name, c))
    hits.extend(_thread_answers(comments))

    # Cluster spellings of the same title: "Teach You A Lesson", "teach you a
    # lesson", "TEACH YOU A LESSON" are one answer.
    clusters: dict[str, list[tuple[str, str, dict]]] = defaultdict(list)
    keys: list[str] = []
    for slot, name, c in hits:
        f = fold(slot)
        for k in keys:
            if fuzz.ratio(f, k) >= 88:
                clusters[k].append((slot, name, c))
                break
        else:
            keys.append(f)
            clusters[f].append((slot, name, c))

    out: list[Discovery] = []
    for _k, group in clusters.items():
        authors = {g[2].get("author", "") for g in group}
        by_comment = {g[2].get("id"): g[2] for g in group}
        likes = sum(c.get("likes", 0) for c in by_comment.values())
        frames: dict[str, int] = {}
        for _s, name, _c in group:
            frames[name] = frames.get(name, 0) + 1
        best = max(group, key=lambda g: g[2].get("likes", 0))
        spellings = sorted({g[0] for g in group}, key=lambda s: -sum(
            g[2].get("likes", 0) for g in group if g[0] == s))
        # Someone must have actually stated this as a title, not merely
        # quoted it. Without that, a popular line of dialogue outranks the
        # real answer every time.
        if _strength(frames) < min_strong:
            continue
        # The commenter threshold guards weak evidence. One person writing
        # "Drama name : from zero to one" is not weak evidence - it is the
        # answer, and it should beat the video's own clickbait title.
        if len(authors) < min_commenters and _strength(frames) < 1:
            continue
        out.append(Discovery(
            title=spellings[0], commenters=len(authors), likes=likes,
            frames=frames, example=best[2].get("text", "")[:110],
            variants=spellings[:5],
        ))
    # How many people stated it outright, then agreement, then upvotes.
    out.sort(key=lambda d: (-_strength(d.frames), -d.commenters, -d.likes))
    return out
