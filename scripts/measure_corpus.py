"""학습 코퍼스 자체를 모델과 똑같은 자로 채점한다 — 가정 없음.

모델이 흉내내도록 배운 토큰열(train.jsonl)을 생성물과 동일한 경로로 복원해
drawable-clean을 재면, 그 통과율이 곧 '모델이 완벽히 학습했을 때의 상한'이다.
"""
from __future__ import annotations
import argparse, json, sys

sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
from plan2graph import wallcycle_codec as wc
import diag_placement as DP
from survey_outline import footprint_metrics
from render_geomclean import _min_unique_verts, _has_diagonal

KEYS = ["selfint0", "overlap_ok", "span_ok", "verts_ok", "nodiag",
        "fp_pieces_ok", "fp_fill_ok", "fp_convex_ok", "drawable"]


def judge(g, aspect_max, fp_fill, fp_convex, overlap_max=0.25):
    if len(g.get("rooms") or {}) < 2:
        return None
    m = DP.metrics(g)
    fp = footprint_metrics(g)
    verts = _min_unique_verts(g) >= 4
    nodiag = not _has_diagonal(g)
    pieces_ok = bool(fp) and fp["pieces"] == 1
    fill_ok = bool(fp) and fp["fill"] >= fp_fill
    convex_ok = bool(fp) and fp["convex"] >= fp_convex
    ov_ok = m["overlap_frac"] < overlap_max
    sp_ok = m["span_ratio"] < aspect_max
    shape_ok = (m["selfint_rooms"] == 0 and verts and nodiag
                and pieces_ok and fill_ok and convex_ok)
    return {"selfint0": m["selfint_rooms"] == 0, "overlap_ok": ov_ok, "span_ok": sp_ok,
            "verts_ok": verts, "nodiag": nodiag, "fp_pieces_ok": pieces_ok,
            "fp_fill_ok": fill_ok, "fp_convex_ok": convex_ok,
            "drawable": bool(shape_ok and ov_ok and sp_ok),
            "_fill": fp["fill"] if fp else None, "_convex": fp["convex"] if fp else None,
            "_pieces": fp["pieces"] if fp else None, "_ov": m["overlap_frac"],
            "_span": m["span_ratio"], "_selfint": m["selfint_rooms"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--vocab", required=True)
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--aspect-max", type=float, default=10.0)
    ap.add_argument("--fp-fill", type=float, default=0.60)
    ap.add_argument("--fp-convex", type=float, default=0.70)
    ap.add_argument("--json-out", required=True)
    args = ap.parse_args()
    TH = (args.aspect_max, args.fp_fill, args.fp_convex)

    vocab = json.load(open(args.vocab, encoding="utf-8"))
    T = dict((k, 0) for k in KEYS)
    n = err = 0
    pieces_hist = {}
    dist = {"fill": [], "convex": [], "ov": [], "span": [], "selfint": []}

    with open(args.corpus, encoding="utf-8") as f:
        for line in f:
            if n >= args.n:
                break
            row = json.loads(line)
            try:
                g = wc.canon_to_graph(wc.decode(row["tokens"], vocab))
                for r in g["rooms"].values():
                    if r.get("polygon"):
                        r["polygon"] = wc.rectify_diagonals(r["polygon"])
                r = judge(g, *TH)
            except Exception:
                err += 1
                continue
            if r is None:
                err += 1
                continue
            n += 1
            for k in KEYS:
                T[k] += int(r[k])
            p = r["_pieces"]
            pieces_hist[p] = pieces_hist.get(p, 0) + 1
            dist["fill"].append(r["_fill"]); dist["convex"].append(r["_convex"])
            dist["ov"].append(r["_ov"]); dist["span"].append(r["_span"])
            dist["selfint"].append(r["_selfint"])

    def med(xs):
        xs = sorted(x for x in xs if x is not None)
        return xs[len(xs) // 2] if xs else None

    out = {"corpus": args.corpus, "n": n, "errors": err,
           "thresholds": {"aspect_max": args.aspect_max, "fp_fill": args.fp_fill,
                          "fp_convex": args.fp_convex, "overlap": 0.25},
           "tally": T,
           "pct": dict((k, (100.0 * T[k] / n) if n else None) for k in KEYS),
           "footprint_pieces_hist": dict(sorted(pieces_hist.items())),
           "median": {k: med(v) for k, v in dist.items()},
           "selfint_rooms_mean": (sum(dist["selfint"]) / n) if n else None}
    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print("corpus=%s  n=%d err=%d" % (args.corpus, n, err), flush=True)
    for k in KEYS:
        print("  %-14s %6.1f%%  (%d/%d)" % (k, out["pct"][k], T[k], n), flush=True)
    print("  footprint pieces:", out["footprint_pieces_hist"], flush=True)
    print("  medians:", out["median"], flush=True)
    print("[json] %s" % args.json_out, flush=True)


if __name__ == "__main__":
    main()
