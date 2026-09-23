"""titlesift - pull the titles people name in a YouTube comment section."""
from __future__ import annotations

import argparse
import json
import sys

from .analyze import analyze
from .discover import discover
from .extract import Extractor
from .fetch import JSONFileSource, YouTubeAPISource, video_id
from .gazetteer import Gazetteer
from .rank import format_table, rank


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="titlesift", description=__doc__)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--video", help="YouTube URL or 11-char video id")
    src.add_argument("--file", help="JSON file of comments (offline mode)")
    p.add_argument("--limit", type=int, default=500, help="max comments to read")
    p.add_argument("--gazetteer", help="JSON title catalogue (defaults to built-in seed)")
    p.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    p.add_argument("--top", type=int, default=25)
    p.add_argument("--no-discover", action="store_true",
                   help="skip open-vocabulary discovery (catalogue matches only)")
    args = p.parse_args(argv)

    gaz = Gazetteer.from_json(args.gazetteer) if args.gazetteer else Gazetteer()
    ex = Extractor(gaz)

    header = ""
    meta: dict = {}
    if args.video:
        try:
            source = YouTubeAPISource()
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        vid = video_id(args.video)
        meta = source.video_meta(vid)
        header = f"{meta['title']} - {meta['channel']} ({meta['comment_count']:,} comments)"
        comments = list(source.comments(vid, limit=args.limit))
    else:
        comments = list(JSONFileSource(args.file).comments())
        header = f"{args.file} ({len(comments)} comments)"

    mentions = [m for c in comments for m in ex.from_comment(c)]
    rows = rank(mentions)
    found = [] if args.no_discover else discover(comments)
    answers, asked = analyze(meta, comments, ex)

    if args.json:
        print(json.dumps(
            {"source": header, "comments_read": len(comments),
             "titles": [r.to_dict() for r in rows[:args.top]],
             "asked_by": asked,
             "answers": [
                 {"title": a.title, "confidence": a.confidence,
                  "sources": a.sources, "commenters": a.commenters,
                  "likes": a.likes,
                  "alternates": [{"name": n, "kind": k} for n, k in a.alternates]}
                 for a in answers[:args.top]
             ],
             "discovered": [
                 {"title": d.title, "commenters": d.commenters,
                  "likes": d.likes, "frames": d.frames, "example": d.example}
                 for d in found[:args.top]
             ]},
            indent=2, ensure_ascii=False))
    else:
        print(header)
        print(f"read {len(comments)} comments, found {len(mentions)} mentions "
              f"of {len(rows)} distinct titles\n")
        print(format_table(rows, limit=args.top))
        if found:
            print(f"\n\nStated outright in the comments "
                  f"(no catalogue needed):\n")
            print(f"{'title':<44}{'ppl':>5}{'likes':>8}")
            print("-" * 58)
            for d in found[:args.top]:
                print(f"{d.title[:44]:<44}{d.commenters:>5}{d.likes:>8}")
        if answers:
            print(f"\n\nBest answer for this video"
                  f"{f' ({asked} people asked what it is)' if asked else ''}:\n")
            for a in answers[:3]:
                print(f"  {a.title}   [{a.confidence}]  "
                      f"{'+'.join(a.sources)}")
                for name, kind in a.alternates[:6]:
                    print(f"       also known as: {name}  ({kind})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
