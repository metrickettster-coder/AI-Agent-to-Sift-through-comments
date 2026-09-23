"""Fill the title-database caches for every title the report counts.

    python3 fetch_titledb.py anilist      # ~2s per title
    python3 fetch_titledb.py wikipedia    # exact article titles, 50 per request
    python3 fetch_titledb.py search       # Wikipedia search + MangaDex, leftovers only

Each stage only fetches what its cache lacks, so it can be stopped and
re-run. Caches live in dbcache/ and make the report build fully offline.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from titlesift import resolve
from titlesift.report import answers_of, choose_entity, group_key, live_action, segments_of

CACHE_DIR = os.path.join(HERE, "dbcache")


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def main(stage):
    results = json.load(open(os.path.join(HERE, "sweep_530_videos.json")))
    caches = resolve.open_caches(CACHE_DIR)
    items = list(answers_of(results))
    titles = list(dict.fromkeys(shown for _r, _t, shown in items))
    log(f"{len(titles)} distinct titles from {len(items)} answers")

    if stage == "anilist":
        for i, t in enumerate(titles):
            resolve.anilist_raw(t, caches["anilist"])
            if i % 25 == 0:
                log(f"anilist {i}/{len(titles)}")
        caches["anilist"].save()

    elif stage == "wikipedia":
        resolve.wikipedia_bulk(titles, caches["wikipedia"], log=log)

    elif stage == "search":
        # Only titles that nothing confirmed yet. Live-action videos go to
        # Wikipedia search; drawn ones go to MangaDex.
        # Search is rate-limited hardest, so only titles with enough people
        # behind them to reach the page, strongest first.
        done = set()
        items.sort(key=lambda x: -x[1].get("commenters", 0))
        for r, top, t in items:
            if t in done or top.get("commenters", 0) < 4:
                continue
            done.add(t)
            segs = segments_of(r.get("query", "") + " " + r.get("title", ""))
            ctx = r.get("query", "") + " " + r.get("title", "")
            ents = resolve.lookup(t, caches, group_key, offline=True)
            if choose_entity(t, ents, segs, live_action(segs, ctx))[1] == "confirmed":
                continue
            if live_action(segs, r.get("query", "") + " " + r.get("title", "")):
                resolve.wikipedia_raw(t, caches["wikipedia"])
                log(f"searched wikipedia: {t}")
            else:
                resolve.mangadex_raw(t, caches["mangadex"])
                log(f"searched mangadex: {t}")
        for c in caches.values():
            c.save()


if __name__ == "__main__":
    main(sys.argv[1])
