"""Mention extraction and canonicalisation.

Pipeline per comment:
  1. clean    - strip urls, timestamps, emoji, collapse noise
  2. generate - propose candidate spans (gazetteer n-grams + surface cues)
  3. resolve  - map each candidate to a canonical title, or park it
  4. dedupe   - one mention per (comment, canonical)
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz, process

from .disambiguate import accept, ambiguity

from .gazetteer import (
    LEADING_ARTICLES,
    MEDIA_WORDS,
    SERIAL_NOISE,
    Gazetteer,
    fold,
)

_URL = re.compile(r"https?://\S+|www\.\S+")
_TIMESTAMP = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_EMOJI = re.compile("[" "\U0001F000-\U0001FAFF" "☀-➿" "️" "]+")
_QUOTED = re.compile(r"[\"“”'‘’]([^\"“”'‘’]{2,60})[\"“”'‘’]")

# "you should read X", "check out X", "X is peak" - the span after/before the cue
_CUE_AFTER = re.compile(
    r"\b(?:read|reading|watch|watching|try|check\s+out|recommend|rec|"
    r"look\s+up|start(?:ed)?(?:\s+with)?|pick\s+up|binge[d]?)\b[:\s]+"
    r"([A-Za-z0-9][\w'’\-\. ]{1,50})",
    re.IGNORECASE,
)

# Tokens that are common English chatter and must never stand alone as titles.
STOPLIKE = {
    "this", "that", "these", "those", "one", "it", "im", "i", "me", "my",
    "you", "your", "he", "she", "they", "we", "us", "them", "what", "who",
    "yes", "no", "yeah", "nah", "ok", "okay", "lol", "lmao", "bro", "fr",
    "peak", "goat", "cinema", "fire", "trash", "mid", "good", "bad", "best",
    "worst", "first", "last", "next", "same", "also", "too", "very", "really",
    "thanks", "thank", "please", "pls", "need", "want", "like", "love", "hate",
    "everyone", "someone", "anyone", "nobody", "everything", "nothing",
    "name", "title", "titles", "list", "video", "comment", "comments",
    "season", "episode", "chapter", "part", "arc", "op", "ed", "sub", "dub",
    "recommendation", "recommendations", "underrated", "overrated", "top",
}
STOPLIKE |= MEDIA_WORDS

# Function words that must never begin or end a candidate span.
_EDGE_FUNCTION = {
    "a", "an", "the", "is", "was", "are", "were", "be", "and", "or", "but",
    "if", "so", "than", "then", "that", "this", "of", "in", "on", "at", "to",
    "for", "with", "from", "by", "as", "it", "its", "i", "me", "my", "you",
    "your", "he", "she", "they", "them", "we", "us", "our", "his", "her",
    "not", "no", "too", "very", "just", "only", "also", "even", "still",
    "about", "after", "before", "all", "any", "both", "some", "such",
}

MIN_FUZZY_LEN = 5          # below this, only exact/acronym matching is allowed
FUZZY_ACCEPT = 88          # rapidfuzz score needed to accept a fuzzy match

# Fuzzy matching uses plain `ratio`, NOT `WRatio`. WRatio scores substrings
# highly, so "killer" hit the alias "kingkiller" at 90 and real titles missing
# from the catalogue ("SSS-Class Suicide Hunter") were dragged onto similar
# known ones ("Hunter x Hunter") at 95. On live comments `ratio` puts every
# genuine misspelling at 93-100 and every one of those false hits below 76.
FUZZY_SCORER = fuzz.ratio

# A near-miss on ordinary English is not evidence of anything: "monsters" is
# not a typo for the manga "Monster". Only distinctive wording may fuzzy-match.
FUZZY_MAX_AMBIGUITY = 2.2


@dataclass
class Mention:
    raw: str                # text as the commenter wrote it
    canonical: str | None   # resolved title, None when unresolved
    kind: str
    method: str             # exact | acronym | fuzzy | unresolved
    score: float
    ambiguity: float = 0.0
    evidence: tuple = ()
    comment_id: str = ""
    author: str = ""
    comment_text: str = ""


def clean(text: str) -> str:
    text = _URL.sub(" ", text)
    text = _TIMESTAMP.sub(" ", text)
    text = _EMOJI.sub(" ", text)
    text = text.replace("<br>", " ").replace("&quot;", '"').replace("&#39;", "'")
    text = text.replace("&amp;", "&")
    return re.sub(r"\s+", " ", text).strip()


def strip_noise(span: str) -> str:
    """Trim serial numbers, media words and articles off a candidate span."""
    s = SERIAL_NOISE.sub(" ", span)
    s = fold(s)
    for art in LEADING_ARTICLES:
        if s.startswith(art):
            s = s[len(art):]
            break
    words = s.split()
    # Trim media words, stray single letters and leading/trailing function
    # words, so a sweep over "there's a parasite" cannot yield "s a parasite".
    def junk(w: str) -> bool:
        return w in MEDIA_WORDS or len(w) == 1 or w in _EDGE_FUNCTION

    while words and junk(words[-1]):
        words.pop()
    while words and junk(words[0]):
        words.pop(0)
    return " ".join(words).strip()


class Extractor:
    def __init__(self, gaz: Gazetteer):
        self.gaz = gaz
        self._alias_keys = list(gaz.alias_to_canonical.keys())
        # The hashtag segmenter needs the same catalogue this extractor has,
        # and it is the only other place a title arrives without its spaces.
        from .metadata import use_catalogue
        use_catalogue(gaz)

    # ------------------------------------------------------------- resolve
    def resolve(self, span: str) -> tuple[str | None, str, float]:
        key = strip_noise(span)
        if not key or key in STOPLIKE:
            return None, "unresolved", 0.0

        hit = self.gaz.exact(key)
        if hit:
            return hit, "exact", 100.0

        compact = key.replace(" ", "")
        hit = self.gaz.by_acronym(compact)
        if hit and 2 <= len(compact) <= 6:
            return hit, "acronym", 95.0

        # Fuzzy is only safe on spans long enough that a near-miss is meaningful,
        # and only when the span itself is not ordinary English.
        if len(key) >= MIN_FUZZY_LEN and ambiguity(key) < FUZZY_MAX_AMBIGUITY:
            pool = self.gaz.block(key)
            if pool:
                match = process.extractOne(
                    key, pool, scorer=FUZZY_SCORER, score_cutoff=FUZZY_ACCEPT
                )
                if match:
                    alias, score, _ = match
                    # Guard against short aliases matching long spans and vice
                    # versa, on both characters and word count.
                    if (abs(len(alias) - len(key)) <= max(4, 0.4 * len(alias))
                            and abs(len(alias.split()) - len(key.split())) <= 1):
                        return self.gaz.alias_to_canonical[alias], "fuzzy", float(score)
        return None, "unresolved", 0.0

    # ------------------------------------------------------------ generate
    def candidates(self, text: str) -> list[str]:
        spans: list[str] = []

        for m in _QUOTED.finditer(text):
            spans.append(m.group(1))
        for m in _CUE_AFTER.finditer(text):
            spans.append(m.group(1))

        # Gazetteer n-gram sweep over the whole comment - catches titles written
        # inline with no punctuation cue at all, which is the common case.
        words = fold(text).split()
        n = min(self.gaz.max_ngram, len(words))
        while n >= 1:
            for i in range(len(words) - n + 1):
                gram = " ".join(words[i:i + n])
                if gram in self.gaz.alias_to_canonical:
                    spans.append(gram)
            n -= 1

        # Bare uppercase acronyms (AOT, JJK) survive folding, so read them raw.
        for tok in re.findall(r"\b[A-Z]{2,6}\b", text):
            spans.append(tok)

        # Misspelled titles produce no exact n-gram hit, so sweep again and keep
        # any n-gram that blocks against a plausible alias. resolve() re-scores
        # it properly; this pass only decides what is worth scoring.
        for n in range(1, min(self.gaz.max_ngram, len(words)) + 1):
            for i in range(len(words) - n + 1):
                gram = " ".join(words[i:i + n])
                if len(gram) < MIN_FUZZY_LEN:
                    continue
                if all(w in STOPLIKE for w in gram.split()):
                    continue
                if gram in self.gaz.alias_to_canonical:
                    continue          # already caught by the exact sweep
                if self.gaz.block(gram, min_shared=3):
                    spans.append(gram)

        return spans

    # --------------------------------------------------------------- comment
    def from_comment(self, comment: dict) -> list[Mention]:
        text = clean(comment.get("text", ""))
        if not text:
            return []
        # Resolve every candidate once, then judge. A title that clears the bar
        # on its own makes the comment a "people naming works" context, which
        # is itself evidence for a second, more ambiguous title beside it.
        resolved = []
        for span in self.candidates(text):
            canonical, method, score = self.resolve(span)
            if canonical is None:
                continue
            key = strip_noise(span)
            resolved.append((key, canonical, method, score))

        anchored = False
        for k, c, m, _s in resolved:
            if m not in ("exact", "acronym"):
                continue
            ok, amb, _why = accept(k, c, text)
            if ok and amb < 2.2:
                anchored = True
                break

        seen: set[str] = set()
        out: list[Mention] = []
        for key, canonical, method, score in resolved:
            if canonical in seen:
                continue
            ok, amb, why = accept(key, canonical, text, companion=anchored)
            if not ok:
                continue
            span = key
            seen.add(canonical)
            out.append(
                Mention(
                    raw=span.strip(),
                    canonical=canonical,
                    kind=self.gaz.kind_of(canonical),
                    method=method,
                    score=score,
                    ambiguity=round(amb, 2),
                    evidence=tuple(why),
                    comment_id=comment.get("id", ""),
                    author=comment.get("author", ""),
                    comment_text=text,
                )
            )
        return out
