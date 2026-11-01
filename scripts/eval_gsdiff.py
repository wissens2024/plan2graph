"""GSDiff 공개 모델 drawable-clean 평가 — RPLAN 프로토콜(종횡비8·fill0.60·convex0.75), 고정 N.

gsdiff_to_cadrender.reconstruct_samples()(4장/호출, 공개 ckpt structure-1/2)로 생성한
방 폴리곤에 eval_3seed와 동일한 지표·후처리(rectify_diagonals)를 적용한다.
실패 배치·미구성 샘플도 분모 N에 포함(고정 N 무편향).
"""
from __future__ import annotations
import argparse, json, os, sys

sys.path.append(os.path.expanduser("~/plan2graph/scripts"))
sys.path.append(os.path.expanduser("~/plan2graph/src"))
sys.path.append(os.path.expanduser("~/gsdiff_work/scripts"))

from plan2graph import wallcycle_codec as wc            # noqa: E402
import diag_placement as DP                              # noqa: E402
from survey_outline import footprint_metrics             # noqa: E402
from render_geomclean import _min_unique_verts, _has_diagonal  # noqa: E402
import gsdiff_to_cadrender as G                          # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--aspect-max", type=float, default=8.0)
    ap.add_argument("--fp-fill", type=float, default=0.60)
    ap.add_argument("--fp-convex", type=float, default=0.75)
    ap.add_argument("--json-out", required=True)
    args = ap.parse_args()

    N = args.n
    T = dict(decoded=0, single_strict=0, selfint0=0, overlap_ok=0, span_ok=0,
             geo_clean=0, drawable=0)
    attempts = 0
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    while attempts < N:
        try:
            samples = G.reconstruct_samples()
        except Exception as e:  # noqa: BLE001
            print(f"batch ERR: {type(e).__name__}: {e}", flush=True)
            attempts += 4
            continue
        for (polys, sems, edges_xy, ncorners) in samples:
            if attempts >= N:
                break
            attempts += 1
            g = {"rooms": {}}
            for i, (poly, sem) in enumerate(zip(polys, sems)):
                if len(poly) < 3:
                    continue
                pp = [[float(p[0]), float(p[1])] for p in poly]
                g["rooms"][str(i)] = {"polygon": wc.rectify_diagonals(pp),
                                      "role": G.GSDIFF_SEM_TO_ROLE.get(sem, "기타")}
            if len(g["rooms"]) < 2:
                continue
            try:
                m = DP.metrics(g)
            except Exception:
                continue
            if not m:
                continue
            T["decoded"] += 1
            # zero-gap 연결(단일 union 조각)
            ps = []
            for r in g["rooms"].values():
                try:
                    q = Polygon(r["polygon"])
                    if q.is_valid and q.area > 1:
                        ps.append(q)
                except Exception:
                    pass
            if ps:
                u = unary_union(ps)
                if u.geom_type != "MultiPolygon":
                    T["single_strict"] += 1
            ov, sp = m["overlap_frac"], m["span_ratio"]
            if m["selfint_rooms"] == 0: T["selfint0"] += 1
            if ov < 0.25: T["overlap_ok"] += 1
            if sp < args.aspect_max: T["span_ok"] += 1
            if m["selfint_rooms"] == 0 and ov < 0.25 and sp < args.aspect_max:
                T["geo_clean"] += 1
            fp = footprint_metrics(g)
            if (m["selfint_rooms"] == 0 and ov < 0.25 and sp < args.aspect_max
                    and _min_unique_verts(g) >= 4 and not _has_diagonal(g)
                    and fp and fp["pieces"] == 1 and fp["fill"] >= args.fp_fill
                    and fp["convex"] >= args.fp_convex):
                T["drawable"] += 1
        if attempts % 40 < 4:
            print(f"  ...{attempts}/{N} drawable={T['drawable']}", flush=True)

    out = {"engine": "GSDiff public ckpt (structure-1/2), unconditional", "n": N,
           "thresholds": {"aspect_max": args.aspect_max, "fp_fill": args.fp_fill,
                          "fp_convex": args.fp_convex, "overlap": 0.25},
           "tally": T,
           "rates_pct": {k: round(100 * v / N, 1) for k, v in T.items()}}
    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1), flush=True)


if __name__ == "__main__":
    main()
