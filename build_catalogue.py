"""Build a learned catalogue from a sweep.

Only comment-confirmed titles go in, so the metadata layer is never taught by
the metadata layer - that would launder its own mistakes into a catalogue and
then score itself against them.
"""
import json, sys, collections
sys.path.insert(0,'/home/claude/titlesift')
from rapidfuzz import fuzz
from titlesift.report import group_key, display_form, clean, alt_is_clean, variant_of
from titlesift.gazetteer import SEED, fold
from titlesift.disambiguate import ambiguity
from wordfreq import zipf_frequency

def build(results):
    seeded={fold(t.canonical) for t in SEED} | {fold(a) for t in SEED for a in t.aliases}
    names=collections.defaultdict(collections.Counter)
    alts=collections.defaultdict(collections.Counter)
    for r in results:
        for a in (r.get("answers") or []):
            commented = any(s.startswith("comments") for s in a["sources"])
            meta_too  = any(s.startswith("metadata") for s in a["sources"])
            if not commented: continue
            if not (a.get("commenters",0) >= 3 or (meta_too and a.get("commenters",0) >= 2)):
                continue
            t=clean(a["title"]); k=group_key(t)
            if not k or len(k)<3 or len(k.split())>7: continue
            # A catalogue entry that fires on ordinary English is worse than
            # no entry: "Love" and "Run" are real shows, and learning them
            # pulled five unrelated videos onto the wrong answer. This is a
            # looser bar than the one the matcher uses on a span, because an
            # exact hit on a multi-word phrase is rarely an accident: "My
            # Demon" is safe to learn, "Love" is not.
            words = k.split()
            if len(words) == 1 and zipf_frequency(words[0], "en") >= 3.0:
                continue
            if ambiguity(t) >= 5.0:
                continue
            names[k][t]+=1
            for x in a.get("alternates",[]):
                x = x[0] if isinstance(x,(list,tuple)) else x
                if alt_is_clean(clean(x)): alts[k][clean(x)]+=1

    # A typo spelling seen once becomes its own catalogue entry otherwise,
    # and then teaches the matcher that the typo is a real title.
    order=sorted(names, key=lambda k: -sum(names[k].values()))
    for i,k in enumerate(order):
        if k not in names: continue
        for o in order[i+1:]:
            if o in names and len(o)>=6 and fuzz.ratio(k,o)>=90:
                names[k].update(names.pop(o)); alts[k].update(alts.pop(o,{}))

    rows=[]
    for k,c in names.items():
        if k in seeded: continue
        canon=display_form(c)
        al=[a for a,n in alts[k].most_common()
            if group_key(a)!=k and (n>=2 or variant_of(a,canon))][:6]
        al += [v for v in c if v!=canon][:3]
        rows.append({"canonical":canon,"kind":"unknown",
                     "aliases":sorted({a for a in al if group_key(a)!=group_key(canon)})})
    return rows

if __name__ == "__main__":
    S="/tmp/claude-0/-home-claude/da0491c9-0416-55fd-aaa6-8c619fed5d4c/scratchpad"
    rows=build(json.load(open(sys.argv[1] if len(sys.argv)>1 else S+"/sweep2_results.json")))
    json.dump(rows,open(S+"/learned.json","w"),indent=1,ensure_ascii=False)
    print(f"learned {len(rows)} titles beyond {len(SEED)} seeded")
