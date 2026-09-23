"""Decide whether a gazetteer hit is really a title mention.

Titles like "Monster", "Dune", "Parasite" and "Sweet Home" are also ordinary
English, so a bare string match is not evidence. Each hit is scored:

  ambiguity  - how ordinary the title's wording is, from English word
               frequency (wordfreq), discounted for multi-word titles because
               a two-word phrase collides with normal speech far less often.
  evidence   - contextual support in the comment: capitalisation, a media
               word, a recommendation cue, quotes, a year, an author.

Ambiguous titles must clear a positive evidence bar. Every title is penalised
when the following word turns it into a compound ("monster energy",
"dune buggy", "sweet home alabama") - but only when that word is a noun-like
content word, not a verb, so "Interstellar made me cry" survives.
"""
from __future__ import annotations

import re

from wordfreq import zipf_frequency

from .gazetteer import MEDIA_WORDS, fold

AMBIGUITY_THRESHOLD = 2.2     # >= this and the title needs positive evidence
EVIDENCE_REQUIRED = 2         # bar for an ambiguous title
EVIDENCE_FLOOR = -2           # a distinctive title fails only on strong negatives

TITLE_STOPWORDS = {"of", "the", "a", "an", "at", "in", "to", "and", "x", "no", "wa"}

# Function words never form a compound with a preceding title.
FUNCTION_WORDS = {
    "a", "an", "the", "is", "was", "are", "were", "be", "been", "being", "am",
    "and", "or", "but", "if", "so", "than", "then", "that", "this", "these",
    "those", "of", "in", "on", "at", "to", "for", "with", "from", "by", "as",
    "it", "its", "i", "me", "my", "you", "your", "he", "she", "they", "them",
    "their", "we", "us", "our", "his", "her", "him", "not", "no", "nor", "too",
    "very", "just", "only", "also", "even", "still", "yet", "about", "after",
    "before", "over", "under", "up", "down", "out", "off", "again", "once",
    "all", "any", "both", "each", "more", "most", "other", "some", "such",
    "one", "two", "when", "where", "who", "what", "why", "how", "which",
    "everything", "nothing", "something", "anything", "everyone", "someone",
    "anyone", "nobody", "else", "both", "here", "there", "now", "ever",
    "never", "always", "really", "actually", "literally", "tbh", "imo", "lol",
}

# Serial markers attach to a title rather than forming a new compound:
# "Tower of God season 2", "jujutsu kaisen ch 236".
SERIAL_WORDS = {
    "season", "seasons", "episode", "episodes", "ep", "eps", "chapter",
    "chapters", "ch", "vol", "volume", "volumes", "part", "pt", "arc", "arcs",
    "s1", "s2", "s3", "manga", "spoilers", "ending", "finale",
}

# Domain vocabulary: its presence says the comment is talking about works,
# which lifts an otherwise ordinary-looking title.
DOMAIN_WORDS = {
    # Deliberately discriminative: words that are rare in ordinary speech but
    # common when people discuss works. Generic words like "plot", "art" or
    # "series" are NOT here - they fire on everyday comments.
    "oscar", "oscars", "director", "directed", "cinematography", "screenplay",
    "soundtrack", "netflix", "theater", "theatre", "rewatch", "rewatched",
    "prequel", "trilogy", "boxoffice", "casting", "subbed", "dubbed",
    "panels", "artstyle", "scanlation", "scanlations", "raws", "protagonist",
    "worldbuilding", "powerscaling", "serialization", "webtoons", "mangaka",
    "paperback", "audiobook", "prose", "scifi", "spoilers", "adaptation",
    "pages", "chapters", "volumes", "arcs", "visuals", "tropes", "isekai",
}

# Common verbs (and inflections) - a title followed by one of these is a
# sentence continuing, not a compound noun.
_VERB_STEMS = {
    "make", "made", "change", "go", "went", "get", "got", "come", "came",
    "take", "took", "give", "gave", "see", "saw", "know", "knew", "think",
    "thought", "look", "want", "use", "find", "found", "tell", "told", "ask",
    "work", "seem", "feel", "felt", "try", "leave", "left", "call", "keep",
    "kept", "let", "begin", "began", "help", "talk", "turn", "start", "show",
    "hear", "heard", "play", "run", "ran", "move", "live", "believe", "hold",
    "held", "bring", "brought", "happen", "write", "wrote", "provide", "sit",
    "sat", "stand", "stood", "lose", "lost", "pay", "paid", "meet", "met",
    "include", "continue", "set", "learn", "lead", "led", "understand",
    "watch", "follow", "stop", "create", "speak", "spoke", "read", "spend",
    "grow", "grew", "open", "walk", "win", "won", "teach", "taught", "offer",
    "remember", "consider", "appear", "buy", "bought", "serve", "die", "send",
    "sent", "build", "built", "stay", "fall", "fell", "cut", "reach", "kill",
    "raise", "pass", "sell", "sold", "decide", "return", "explain", "hope",
    "develop", "carry", "break", "broke", "receive", "agree", "support",
    "hit", "produce", "eat", "ate", "cover", "catch", "caught", "draw", "drew",
    "choose", "chose", "cause", "drive", "drove", "destroy", "destroyed",
    "sweep", "swept", "slap", "hate", "love", "like", "need", "deserve",
    "oblige", "obliged", "plays", "played", "ruin", "ruined", "save", "saved",
}


def _inflect(stems: set[str]) -> set[str]:
    out = set(stems)
    for s in stems:
        out |= {s + "s", s + "ed", s + "ing", s + "es"}
        if s.endswith("e"):
            out |= {s[:-1] + "ing", s + "d"}
        if s.endswith("y"):
            out |= {s[:-1] + "ies", s[:-1] + "ied"}
    return out


VERB_WORDS = _inflect(_VERB_STEMS)

CUE_WORDS = {
    "read", "reading", "watch", "watching", "watched", "try", "trying",
    "recommend", "rec", "recs", "check", "binge", "binged", "bingeing",
    "start", "started", "starting", "pick", "picked", "finish", "finished",
    "reread", "rewatch", "adaptation", "adapted",
}

_YEAR = re.compile(r"\(\s*(19|20)\d{2}\s*\)")
_BY_AUTHOR = re.compile(r"\bby\s+[A-Z][a-z]+", re.UNICODE)


def ambiguity(surface: str) -> float:
    """Higher = the wording the commenter used is more like ordinary English.

    Scored on the surface form, not the canonical title: someone who types
    "hxh" or "TBATE" has been unambiguous even though "Hunter x Hunter" and
    "The Beginning After the End" are built from very common words.
    """
    tokens = [
        t for t in fold(surface).split()
        if t not in TITLE_STOPWORDS
        and t not in FUNCTION_WORDS
        and t not in VERB_WORDS
    ]
    if not tokens:
        # Nothing but function, verb and stop words: "love", "run", "and
        # then", "is it". That is the most ambiguous a span can be, and
        # returning 0.0 here marked it as the least, which is how a learned
        # catalogue entry called "Love" spread across unrelated videos.
        every = fold(surface).split()
        return max((zipf_frequency(t, "en") for t in every), default=0.0)
    rarest = min(zipf_frequency(t, "en") for t in tokens)
    # Each extra content word makes an accidental collision much less likely.
    # Only content words count: "got whiplash" is no less ambiguous than
    # "whiplash", because "got" is not part of any title.
    return rarest - 1.2 * (len(tokens) - 1)


def _is_shouting(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 8:
        return False
    return sum(c.isupper() for c in letters) / len(letters) > 0.7


def _appears_capitalised(span: str, raw: str) -> bool:
    """True if the span occurs in the raw comment with a capital initial."""
    if _is_shouting(raw):
        return False
    pattern = r"\b" + r"[\s\-_'’]*".join(re.escape(w) for w in span.split()) + r"\b"
    for m in re.finditer(pattern, raw, flags=re.IGNORECASE):
        if m.group(0)[:1].isupper():
            return True
    return False


def _neighbour_tokens(span: str, raw: str) -> tuple[str | None, str | None]:
    """The folded words immediately before and after the span."""
    words = fold(raw).split()
    span_words = span.split()
    n = len(span_words)
    for i in range(len(words) - n + 1):
        if words[i:i + n] == span_words:
            prev = words[i - 1] if i > 0 else None
            nxt = words[i + n] if i + n < len(words) else None
            return prev, nxt
    return None, None


def _is_compound_continuation(nxt: str | None) -> bool:
    """A following noun-like content word turns the title into a compound."""
    if not nxt:
        return False
    if nxt in FUNCTION_WORDS or nxt in VERB_WORDS or nxt in MEDIA_WORDS:
        return False
    if nxt in CUE_WORDS or nxt in SERIAL_WORDS or nxt in DOMAIN_WORDS:
        return False
    if nxt.isdigit():
        return False
    # Rare tokens are usually part of a name/compound ("alabama", "oblige").
    return zipf_frequency(nxt, "en") >= 1.5


def _appears_allcaps(span: str, raw: str) -> bool:
    """An acronym the commenter shouted: AOT, TBATE, ORV."""
    if len(span.replace(" ", "")) > 6:
        return False
    pattern = r"\b" + re.escape(span.replace(" ", "")) + r"\b"
    m = re.search(pattern, raw, flags=re.IGNORECASE)
    return bool(m and m.group(0).isupper())


_PAIRED_QUOTE = re.compile(r"[\"“”]([^\"“”]{2,60})[\"“”]|(?<![A-Za-z])['‘]([^'‘’]{2,60})['’](?![A-Za-z])")


def _is_quoted(span: str, raw: str) -> bool:
    """True only for a properly paired quote - not the apostrophe in "there's"."""
    target = fold(span)
    for m in _PAIRED_QUOTE.finditer(raw):
        inner = m.group(1) or m.group(2) or ""
        if target and target in fold(inner):
            return True
    return False


def score_evidence(span: str, raw: str, companion: bool = False) -> tuple[int, list[str]]:
    ev, why = 0, []
    folded_comment = fold(raw)
    comment_words = set(folded_comment.split())

    if _appears_allcaps(span, raw):
        ev += 3
        why.append("acronym-form")
    elif _appears_capitalised(span, raw):
        ev += 3
        why.append("capitalised")
    if comment_words & MEDIA_WORDS:
        ev += 2
        why.append("media-word")
    if comment_words & CUE_WORDS:
        ev += 2
        why.append("cue")
    if _is_quoted(span, raw):
        ev += 3
        why.append("quoted")
    if comment_words & DOMAIN_WORDS:
        ev += 2
        why.append("domain")
    if companion:
        ev += 2
        why.append("companion-title")
    if _YEAR.search(raw):
        ev += 2
        why.append("year")
    if _BY_AUTHOR.search(raw):
        ev += 2
        why.append("author")

    _prev, nxt = _neighbour_tokens(span, raw)
    if _is_compound_continuation(nxt):
        ev -= 3
        why.append(f"compound:{nxt}")

    return ev, why


def accept(span: str, canonical: str, raw: str,
           companion: bool = False) -> tuple[bool, float, list[str]]:
    """Decide whether this hit should be kept."""
    amb = ambiguity(span)
    ev, why = score_evidence(span, raw, companion=companion)
    bar = EVIDENCE_REQUIRED if amb >= AMBIGUITY_THRESHOLD else EVIDENCE_FLOOR
    return ev >= bar, amb, why
