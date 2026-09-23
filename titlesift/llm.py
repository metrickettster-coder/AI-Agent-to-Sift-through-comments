"""Optional LLM adjudication for the cases lexical rules cannot settle.

The lexical pipeline is deliberately conservative: when a title is ordinary
English ("Blue Lock", "Monster", "Parasite") and the comment carries no
capitalisation, media word or domain cue, it declines rather than guess.
Those declines are exactly the cases a language model settles easily, because
it reads the sentence rather than matching strings.

This module batches the undecided spans into one call, so the cost scales with
ambiguity rather than comment volume.

NOT EXERCISED: no Anthropic API key is available in the sandbox this was
built in, so the code path below has never been run. Treat it as a draft.
"""
from __future__ import annotations

import json

PROMPT = """You judge whether a phrase in a YouTube comment names a work \
(movie, manhwa, webtoon, manga, book) or is ordinary English.

For each item decide: is the phrase being used as a TITLE here?
Answer only with JSON: [{"i": <index>, "title": true|false}]

Items:
%s"""


def adjudicate(undecided: list[dict], model: str = "claude-sonnet-5") -> dict[int, bool]:
    """undecided: [{"i": int, "phrase": str, "candidate": str, "comment": str}]"""
    if not undecided:
        return {}
    import anthropic

    lines = [
        f'{u["i"]}. phrase={u["phrase"]!r} possible_title={u["candidate"]!r} '
        f'comment={u["comment"]!r}'
        for u in undecided
    ]
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=model,
        max_tokens=1000,
        messages=[{"role": "user", "content": PROMPT % "\n".join(lines)}],
    )
    text = resp.content[0].text.strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        return {}
    return {int(r["i"]): bool(r["title"]) for r in json.loads(text[start:end + 1])}
