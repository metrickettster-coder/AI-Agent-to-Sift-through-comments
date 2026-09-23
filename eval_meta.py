"""How well does the metadata layer pick the right span?

Label: on videos where comments alone produced an answer several independent
commenters agreed on, that answer is treated as the truth. It is a silver
label, not a gold one, but it is the only one available at this volume and it
was produced without looking at the metadata being scored.
"""
import json, sys
sys.path.insert(0, '/home/claude/titlesift')
from titlesift.metadata import candidates
from titlesift.report import group_key

S = "/tmp/claude-0/-home-claude/da0491c9-0416-55fd-aaa6-8c619fed5d4c/scratchpad"
res = json.load(open(S + "/sweep2_results.json"))
metas = json.load(open(S + "/metas.json"))

MIN_COMMENTERS = 3


def label(r):
    for a in r.get("answers") or []:
        if a.get("commenters", 0) >= MIN_COMMENTERS and \
           all(s.startswith("comments") or s == "catalogue" for s in a["sources"]):
            return a["title"]
    return None


def same(a, b):
    ka, kb = group_key(a), group_key(b)
    if not ka or not kb:
        return False
    return ka == kb or ka in kb or kb in ka


top1 = present = total = 0
misses = []
for r in res:
    lab = label(r)
    m = metas.get(r["vid"])
    if not lab or not m:
        continue
    cands = candidates(m)
    if not cands:
        continue
    total += 1
    names = [c.text for c in cands]
    if same(names[0], lab):
        top1 += 1
    if any(same(n, lab) for n in names):
        present += 1
    else:
        misses.append((lab, names[:4], m["title"][:60]))
    if not same(names[0], lab) and any(same(n, lab) for n in names):
        misses.append(("RANK " + lab, names[:5], m["title"][:60]))

print(f"labelled videos: {total}")
print(f"  top-1 correct : {top1} ({100*top1/max(total,1):.0f}%)")
print(f"  label present : {present} ({100*present/max(total,1):.0f}%)  <- ceiling")
print()
for lab, names, t in misses[:22]:
    print(f"  {lab[:30]:32} | {names} | {t}")
