"""Aggregate mentions into a ranked title list."""
from __future__ import annotations

from dataclasses import dataclass, asdict

from .extract import Mention


@dataclass
class RankedTitle:
    canonical: str
    kind: str
    commenters: int          # distinct people who named it - the headline number
    mentions: int            # raw occurrences
    confidence: float        # mean resolution score
    methods: dict[str, int]
    example_comment: str
    example_author: str
    variants: list[str]      # how people actually spelled it

    def to_dict(self) -> dict:
        return asdict(self)


def rank(mentions: list[Mention]) -> list[RankedTitle]:
    buckets: dict[str, list[Mention]] = {}
    for m in mentions:
        buckets.setdefault(m.canonical, []).append(m)

    out: list[RankedTitle] = []
    for canonical, group in buckets.items():
        authors = {m.author for m in group if m.author}
        methods: dict[str, int] = {}
        for m in group:
            methods[m.method] = methods.get(m.method, 0) + 1

        # Prefer a short, readable comment as the example.
        example = min(group, key=lambda m: (len(m.comment_text) < 25, len(m.comment_text)))
        variants = sorted({m.raw.strip() for m in group if m.raw.strip()}, key=str.lower)

        out.append(
            RankedTitle(
                canonical=canonical,
                kind=group[0].kind,
                commenters=len(authors) or len(group),
                mentions=len(group),
                confidence=round(sum(m.score for m in group) / len(group), 1),
                methods=methods,
                example_comment=example.comment_text,
                example_author=example.author,
                variants=variants[:6],
            )
        )

    out.sort(key=lambda r: (-r.commenters, -r.mentions, r.canonical))
    return out


def format_table(rows: list[RankedTitle], limit: int = 25) -> str:
    if not rows:
        return "No titles found."
    lines = [f"{'#':>2}  {'title':<34} {'kind':<8} {'ppl':>4} {'conf':>5}  example"]
    lines.append("-" * 110)
    for i, r in enumerate(rows[:limit], 1):
        ex = r.example_comment.replace("\n", " ")
        ex = (ex[:47] + "...") if len(ex) > 50 else ex
        lines.append(
            f"{i:>2}  {r.canonical[:34]:<34} {r.kind:<8} {r.commenters:>4} {r.confidence:>5.1f}  {ex}"
        )
    return "\n".join(lines)
