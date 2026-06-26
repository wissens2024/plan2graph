"""repair 효과 측정 (CPU, 고정 코퍼스). config별 strict before/after + 잔여 원인."""
import json, sys
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
import diag_placement as DP
import render_geomclean as RG
from survey_outline import footprint_metrics
from plan2graph.graph_repair import repair_graph

corpus = json.load(open("data/staging/repair_corpus.json", encoding="utf-8"))


def to_g(rec):
    return {"rooms": {k: {"polygon": [list(p) for p in v["polygon"]], "role": v.get("role")}
                      for k, v in rec["rooms"].items()}}


def flags(g):
    try:
        m = DP.metrics(g)
    except Exception:
        return None
    loose = m["selfint_rooms"] == 0 and m["overlap_frac"] < 0.25 and m["span_ratio"] < 8
    fp = footprint_metrics(g)
    return dict(
        loose=loose, selfint0=m["selfint_rooms"] == 0, overlap_ok=m["overlap_frac"] < 0.25,
        span_ok=m["span_ratio"] < 8, verts_ok=RG._min_unique_verts(g) >= 4,
        nodiag=not RG._has_diagonal(g),
        pieces1=bool(fp) and fp["pieces"] == 1,
        fill_ok=bool(fp) and fp["fill"] >= RG.FP_FILL_MIN,
        convex_ok=bool(fp) and fp["convex"] >= RG.FP_CONVEX_MIN)


def is_strict(f):
    return all([f["loose"], f["verts_ok"], f["nodiag"], f["pieces1"], f["fill_ok"], f["convex_ok"]])


def is_strict_noconvex(f):
    return all([f["loose"], f["verts_ok"], f["nodiag"], f["pieces1"], f["fill_ok"]])


N = len(corpus)
b = sum(1 for c in corpus if c["flags"]["strict"])
bl = sum(1 for c in corpus if c["flags"]["loose"])
print("코퍼스 %d | baseline(현행 rectify): loose %.0f%% strict %.0f%%" % (N, 100*bl/N, 100*b/N))
print("=" * 70)

CONFIGS = [("drop_bad (no declash)", dict(drop_bad=True)),
           ("+ declash CLIP", dict(drop_bad=True, declash="clip")),
           ("+ declash WALL", dict(drop_bad=True, declash="wall"))]

for name, kw in CONFIGS:
    s = 0; snc = 0; sub = {k: 0 for k in ["selfint0","overlap_ok","span_ok","pieces1","convex_ok","verts_ok","fill_ok"]}
    for rec in corpus:
        g = to_g(rec)
        g, _ = repair_graph(g, **kw)
        f = flags(g)
        if f is None:
            continue
        if is_strict(f):
            s += 1
        if is_strict_noconvex(f):
            snc += 1
        for k in sub:
            if not f[k]:
                sub[k] += 1
    print("%-26s → strict %d/%d (%.0f%%)  | convex제외 %d (%.0f%%)" % (name, s, N, 100*s/N, snc, 100*snc/N))
    print("   잔여실패: selfint%d overlap%d span%d 비연결%d convex%d 꼭짓점%d fill%d"
          % (sub["selfint0"], sub["overlap_ok"], sub["span_ok"], sub["pieces1"],
             sub["convex_ok"], sub["verts_ok"], sub["fill_ok"]))
