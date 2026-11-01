"""3-seed 최종 평가 — 표현 인식 임계값(종횡비·footprint) 파라미터화 + 민감도 + JSON.

eval_ar_geom.py와 동일 생성·디코드·rectify 경로. 차이:
  - --aspect-max / --fp-fill / --fp-convex 로 drawable-clean 임계값 지정
  - 민감도: overlap {0.10,0.25,0.50} × aspect {6,8,10,12} drawable 변형 카운트
  - --json-out 으로 전체 tally 저장(논문 표·CI 산출용)
"""
from __future__ import annotations
import argparse, json, sys
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
import torch
from shapely.geometry import Polygon
from shapely.ops import unary_union
from plan2graph import wallcycle_codec as wc
from plan2graph.generators.wall_cycle import WallCycleLM, make_constraint_mask
import diag_placement as DP
from survey_outline import footprint_metrics
from render_geomclean import _min_unique_verts, _has_diagonal

OV_GRID = [0.10, 0.25, 0.50]
ASP_GRID = [6.0, 8.0, 10.0, 12.0]


def single_pieces(g, tol_frac=0.0):
    polys = []
    for r in g["rooms"].values():
        pp = r.get("polygon") or []
        if len(pp) >= 3:
            try:
                q = Polygon(pp)
                if q.is_valid and q.area > 1:
                    polys.append(q)
            except Exception:
                pass
    if not polys:
        return 99
    if tol_frac > 0:
        minx = min(p.bounds[0] for p in polys); miny = min(p.bounds[1] for p in polys)
        maxx = max(p.bounds[2] for p in polys); maxy = max(p.bounds[3] for p in polys)
        span = max(maxx - minx, maxy - miny)
        polys = [p.buffer(tol_frac * span) for p in polys]
    u = unary_union(polys)
    return len(u.geoms) if u.geom_type == "MultiPolygon" else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--vocab", required=True)
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--max-new", type=int, default=650)
    ap.add_argument("--constrained", action="store_true")
    ap.add_argument("--orthogonal", action="store_true")
    ap.add_argument("--country", type=int, default=0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--aspect-max", type=float, default=8.0)
    ap.add_argument("--fp-fill", type=float, default=0.60)
    ap.add_argument("--fp-convex", type=float, default=0.75)
    ap.add_argument("--json-out", required=True)
    args = ap.parse_args()

    if args.seed > 0:
        torch.manual_seed(args.seed); torch.cuda.manual_seed_all(args.seed)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    vocab = json.load(open(args.vocab, encoding="utf-8"))
    ck = torch.load(args.ckpt, map_location=dev); a = ck["args"]
    dimff = ck["model"]["blocks.0.mlp.w1.weight"].shape[0]
    model = WallCycleLM(vocab["size"], d_model=a["d_model"], n_layer=a["n_layer"],
                        n_head=a.get("n_head", 8), max_len=a["max_len"], dim_ff=dimff).to(dev)
    model.load_state_dict(ck["model"]); model.eval()
    print(f"[ckpt] {args.ckpt} (epoch {ck.get('epoch')}) on {dev}", flush=True)

    mask_fn = make_constraint_mask(vocab, orthogonal=args.orthogonal) if args.constrained else None
    bos, eos = wc.V.BOS, wc.V.EOS
    pre = [bos, vocab["meta"] + args.country, vocab["meta"] + len(wc.COUNTRIES) + 0,
           vocab["meta"] + len(wc.COUNTRIES) + len(wc.HOUSING) + 0,
           vocab["scope"] + 0, vocab["units"] + 1]

    T = dict(decoded=0, single_strict=0, single_2pct=0, selfint0=0,
             overlap_ok=0, span_ok=0, geo_clean=0, drawable=0)
    sens = {f"ov{o}_asp{int(s)}": 0 for o in OV_GRID for s in ASP_GRID}
    ch = 4
    for i in range(0, args.n, ch):
        k = min(ch, args.n - i)
        prefix = torch.tensor([pre] * k, device=dev)
        out = model.generate(prefix, max_new=args.max_new, eos=eos,
                             temperature=1.0, top_k=40, mask_fn=mask_fn)
        for row in out.tolist():
            row = row[:row.index(eos) + 1] if eos in row else row
            try:
                g = wc.canon_to_graph(wc.decode(row, vocab))
            except Exception:
                continue
            if len(g.get("rooms") or {}) < 2:
                continue
            for r in g["rooms"].values():
                if r.get("polygon"):
                    r["polygon"] = wc.rectify_diagonals(r["polygon"])
            T["decoded"] += 1
            m = DP.metrics(g)
            ov, sp = m["overlap_frac"], m["span_ratio"]
            if single_pieces(g, 0.0) == 1: T["single_strict"] += 1
            if single_pieces(g, 0.02) == 1: T["single_2pct"] += 1
            if m["selfint_rooms"] == 0: T["selfint0"] += 1
            if ov < 0.25: T["overlap_ok"] += 1
            if sp < args.aspect_max: T["span_ok"] += 1
            if m["selfint_rooms"] == 0 and ov < 0.25 and sp < args.aspect_max:
                T["geo_clean"] += 1
            # 형태·외곽 요건 (drawable 공통부)
            fp = footprint_metrics(g)
            shape_ok = (m["selfint_rooms"] == 0 and _min_unique_verts(g) >= 4
                        and not _has_diagonal(g) and fp and fp["pieces"] == 1
                        and fp["fill"] >= args.fp_fill and fp["convex"] >= args.fp_convex)
            if shape_ok and ov < 0.25 and sp < args.aspect_max:
                T["drawable"] += 1
            if shape_ok:
                for o in OV_GRID:
                    for s in ASP_GRID:
                        if ov < o and sp < s:
                            sens[f"ov{o}_asp{int(s)}"] += 1
        if (i + ch) % 100 < ch:
            print(f"  ...{min(i + ch, args.n)}/{args.n} drawable={T['drawable']}", flush=True)

    N = args.n
    out = {"ckpt": args.ckpt, "epoch": ck.get("epoch"), "n": N,
           "eval_seed": args.seed, "country": args.country,
           "thresholds": {"aspect_max": args.aspect_max, "fp_fill": args.fp_fill,
                          "fp_convex": args.fp_convex, "overlap": 0.25},
           "tally": T, "sensitivity_shapeok": sens}
    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("=" * 56, flush=True)
    for k2, v in T.items():
        print(f"  {k2:14s}: {v}/{N} ({100 * v / N:.1f}%)", flush=True)
    print(f"[json] {args.json_out}", flush=True)


if __name__ == "__main__":
    main()
