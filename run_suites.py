"""Run the three regression suites in testdata/ and print one line each.

    python3 run_suites.py

fixture + adversarial: catalogue extraction, scored per expected mention.
multilingual: open-vocabulary discovery on a single comment, where a
question must yield nothing.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from titlesift.discover import discover
from titlesift.extract import Extractor
from titlesift.gazetteer import Gazetteer, fold


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def extraction(path, ex):
    tp = fp = fn = 0
    for c in json.load(open(path)):
        got = {m.canonical for m in ex.from_comment(c) if m.canonical}
        want = set(c["expect"])
        tp += len(got & want)
        fp += len(got - want)
        fn += len(want - got)
    return tp, fp, fn


def multilingual(path):
    ok = 0
    cases = json.load(open(path))
    for i, c in enumerate(cases):
        found = discover([{"id": f"m{i}", "author": "u", "text": c["text"],
                           "likes": 0}], min_commenters=1)
        got = fold(found[0].title) if found else None
        want = fold(c["expect"]) if c["expect"] else None
        ok += got == want
    return ok, len(cases)


def main():
    ex = Extractor(Gazetteer())
    total = [0, 0, 0]
    for name in ("fixture", "adversarial"):
        tp, fp, fn = extraction(os.path.join(HERE, "testdata", name + ".json"), ex)
        total = [a + b for a, b in zip(total, (tp, fp, fn))]
        print(f"{name:<13} p={prf(tp, fp, fn)[0]:.3f} r={prf(tp, fp, fn)[1]:.3f} "
              f"f1={prf(tp, fp, fn)[2]:.3f}")
    p, r, f = prf(*total)
    print(f"{'combined':<13} p={p:.3f} r={r:.3f} f1={f:.3f}")
    ok, n = multilingual(os.path.join(HERE, "testdata", "multilingual.json"))
    print(f"{'multilingual':<13} {ok}/{n} correct")


if __name__ == "__main__":
    main()
