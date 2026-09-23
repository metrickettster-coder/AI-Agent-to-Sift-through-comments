"""One answer per video, from everything available.

Three independent readings of the same video:

  metadata   - the title bar, the description, the hashtags
  comments   - people stating the title outright, in any of 18 languages
  catalogue  - known titles matched in the comments

None is reliable alone. The video title of a clip is full of channel names and
broadcasters ("| MBC | Tiki Tiki"); comments are full of dialogue; the
catalogue only knows what someone put in it. But a candidate that shows up in
*two* of them is almost always right, and that is cheap to check.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .aliases import catalogue_aliases, stated_aliases
from .discover import demand, discover
from .extract import Extractor
from .gazetteer import fold


@dataclass
class Answer:
    title: str
    confidence: str                     # high | medium | low
    sources: list[str] = field(default_factory=list)
    commenters: int = 0
    likes: int = 0
    alternates: list[tuple] = field(default_factory=list)   # (name, kind)

    def __repr__(self) -> str:
        alt = ("  aka " + ", ".join(f"{n} [{k}]" for n, k in self.alternates)
               if self.alternates else "")
        return (f"Answer({self.title!r}, {self.confidence}, "
                f"{self.commenters}p/{self.likes}l, {'+'.join(self.sources)}){alt}")


def _same(a: str, b: str) -> bool:
    fa, fb = fold(a), fold(b)
    if not fa or not fb:
        return False
    return fa == fb or fuzz.ratio(fa, fb) >= 88


def analyze(meta: dict, comments: list[dict],
            extractor: Extractor | None = None) -> tuple[list[Answer], int]:
    """Return (ranked answers, how many people asked what this is)."""
    from .metadata import candidates as meta_candidates

    metas = meta_candidates(meta) if meta else []
    spoken = discover(comments)
    asked, _ = demand(comments)

    catalogue: dict[str, int] = {}
    if extractor is not None:
        seen_by: dict[str, set] = {}
        for c in comments:
            for m in extractor.from_comment(c):
                seen_by.setdefault(m.canonical, set()).add(c.get("author", ""))
        catalogue = {k: len(v) for k, v in seen_by.items()}

    answers: list[Answer] = []
    used_meta: set[str] = set()

    # Anything people said out loud, checked against the other two readings.
    for d in spoken:
        srcs = [f"comments:{d.commenters}"]
        hit = next((m for m in metas if _same(m.text, d.title)), None)
        if hit:
            srcs.append(f"metadata:{hit.source}")
            used_meta.add(fold(hit.text))
        cat = next((k for k in catalogue if _same(k, d.title)), None)
        if cat:
            srcs.append("catalogue")
        conf = ("high" if (hit or cat or d.commenters >= 5)
                else "medium" if d.commenters >= 3 else "low")
        answers.append(Answer(d.title, conf, srcs, d.commenters, d.likes))

    # Catalogue hits nobody stated outright still count - but weakly. A short
    # alias collides easily ("mia" -> Made in Abyss), and an uncorroborated
    # catalogue hit was outranking the answer sitting in the video's own
    # title on videos like "TenPuru: No One Can Live On Loneliness".
    for name, n in sorted(catalogue.items(), key=lambda kv: -kv[1]):
        if any(_same(name, a.title) for a in answers):
            continue
        hit = next((m for m in metas if _same(m.text, name)), None)
        srcs = ["catalogue"] + ([f"metadata:{hit.source}"] if hit else [])
        if hit:
            used_meta.add(fold(hit.text))
            conf = "high"
        else:
            conf = "medium" if n >= 3 else "low"
        answers.append(Answer(name, conf, srcs, n, 0))

    # The video's own title bar is right often enough that it should compete
    # whenever nothing is high-confidence, rather than only when there is
    # nothing at all.
    strong = [m for m in metas
              if m.source in ("title", "hashtag") and fold(m.text) not in used_meta]
    if strong and not any(a.confidence == "high" for a in answers):
        best = strong[0]
        if not any(_same(best.text, a.title) for a in answers):
            answers.append(Answer(best.text, "medium" if best.confidence >= 0.7
                                  else "low", [f"metadata:{best.source}"]))
    if not answers and metas:
        best = metas[0]
        answers.append(Answer(best.text, "low", [f"metadata:{best.source}"]))

    rank = {"high": 0, "medium": 1, "low": 2}
    answers.sort(key=lambda a: (rank[a.confidence], -a.commenters, -a.likes))

    # Alternate names belong to the answer, not beside it. Without this the
    # Korean title of a show competes with its English one and splits the vote.
    if answers:
        primary = answers[0]
        alts: list[tuple] = []
        # "in Korean the title is X" only means X is *this video's* work when
        # the video has one subject. On a recommendation video listing 40
        # titles the implicit referent is whatever that commenter was talking
        # about, not the top answer - which had ORV absorbing "The Boxer".
        # Count only answers worth believing. Letting single-commenter
        # answers into this count pushed real single-subject clips over the
        # threshold and silently dropped their alternate names.
        single_subject = sum(1 for a in answers
                             if a.confidence in ("high", "medium")) <= 3
        if single_subject:
            for al in stated_aliases(comments):
                if _same(al.name, primary.title):
                    continue
                alts.append((al.name, al.kind))
        if extractor is not None:
            for al in catalogue_aliases(primary.title, extractor.gaz):
                if not any(_same(al.name, n) for n, _k in alts):
                    alts.append((al.name, al.kind))
        primary.alternates = alts

        # Anything that turns out to be an alias of the primary is folded in
        # rather than listed as a rival answer.
        merged, folded = [primary], 0
        for a in answers[1:]:
            if any(_same(a.title, n) for n, _k in primary.alternates):
                primary.commenters += a.commenters
                primary.likes += a.likes
                folded += 1
                continue
            merged.append(a)
        if folded:
            primary.sources.append(f"merged:{folded}")
        answers = merged
    return answers, asked
