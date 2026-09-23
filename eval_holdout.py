"""Before/after on videos no tuning ever saw.

Three configurations, same code, same videos:
  baseline  - seed catalogue, hashtag and actor fixes off
  fixes     - seed catalogue, fixes on
  learned   - seed + the 144 titles the first sweep taught itself, fixes on
"""
import json, sys, collections
sys.path.insert(0,'/home/claude/titlesift')
import titlesift.metadata as M
from titlesift.gazetteer import Gazetteer, Title, SEED
from titlesift.extract import Extractor
from titlesift.analyze import analyze

S="/tmp/claude-0/-home-claude/da0491c9-0416-55fd-aaa6-8c619fed5d4c/scratchpad"
metas=json.load(open(S+"/holdout_metas.json"))
comments=json.load(open(S+"/holdout_comments.json"))
learned=[Title(**r) for r in json.load(open(S+"/learned.json"))]

def run(name, gaz, person, glued):
    M.PERSON_PENALTY = 0.45 if person else 0.0
    ex = Extractor(gaz)                 # this sets the despaced index
    if not glued:
        M._KNOWN_GLUED.clear()
    rows=[]
    for vid, cs in comments.items():
        m = metas.get(vid)
        if not m: continue
        ans, asked = analyze(m, cs, ex)
        rows.append({"vid":vid,"title":m["title"],"asked":asked,
                     "answers":[{"title":a.title,"conf":a.confidence,
                                 "sources":a.sources,"commenters":a.commenters}
                                for a in ans[:4]]})
    dem=[r for r in rows if r["asked"]>0]
    hi=[r for r in dem if (r["answers"] or [{}])[0].get("conf")=="high"]
    print(f"{name:10} videos={len(rows)} with demand={len(dem)} "
          f"high-confidence={len(hi)} ({100*len(hi)/max(len(dem),1):.0f}%)")
    return rows

base = run("baseline", Gazetteer(), person=False, glued=False)
fix  = run("fixes",    Gazetteer(), person=True,  glued=True)
full = run("learned",  Gazetteer(SEED+learned), person=True, glued=True)
json.dump({"baseline":base,"fixes":fix,"learned":full},
          open(S+"/holdout_eval.json","w"), ensure_ascii=False)

# what changed, so the audit only has to look at the differences
bi={r["vid"]:r for r in base}
changed=[]
for r in full:
    b=bi[r["vid"]]
    ba=(b["answers"] or [{}])[0]; fa=(r["answers"] or [{}])[0]
    if r["asked"]>0 and (ba.get("title")!=fa.get("title") or ba.get("conf")!=fa.get("conf")):
        changed.append((b["title"][:58], ba.get("title"), ba.get("conf"),
                        fa.get("title"), fa.get("conf")))
print(f"\nchanged answers: {len(changed)}")
for t,bt,bc,ft,fc in changed[:40]:
    print(f"  {t}\n      was: {bt!r} [{bc}]\n      now: {ft!r} [{fc}]")
