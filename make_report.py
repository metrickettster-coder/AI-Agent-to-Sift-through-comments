"""Build the Title Demand Report from the sweep and the title-database caches.

    python3 make_report.py [out.html]

Fully offline: run fetch_titledb.py first to fill dbcache/.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from titlesift.page import render
from titlesift.report import build, to_text
from titlesift.resolve import open_caches

results = json.load(open(os.path.join(HERE, "sweep_530_videos.json")))
rep = build(results, caches=open_caches(os.path.join(HERE, "dbcache")))
json.dump(rep, open(os.path.join(HERE, "title_demand_report.json"), "w"),
          indent=1, ensure_ascii=False)
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "title_demand_report.html")
open(out, "w").write(render(rep))
print(to_text(rep, top=15))
print(f"\n{len(rep['unconfirmed'])} unconfirmed, {len(rep['rejected'])} rejected -> {out}")
