"""KorPlan-AR 배치 기하 평가 — 고정 N 무편향 (EXPERIMENTS §6 표 재현 프로토콜).

생성 N → decode → rectify → 지표:
  decoded / single(strict pieces==1) / single(wall2%) / selfint=0 / overlap<0.25 / span<8 / clean(strict)
조기종료 없음(고정 N). clean 일부 montage.
"""
from __future__ import annotations
import argparse, io, json, math, os, sys
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
import torch
from PIL import Image
from shapely.geometry import Polygon
from shapely.ops import unary_union
from plan2graph import cadrender, wallcycle_codec as wc
from plan2graph.generators.wall_cycle import WallCycleLM, make_constraint_mask
import diag_placement as DP
from survey_outline import footprint_metrics


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
        tol = tol_frac * span
        polys = [p.buffer(tol) for p in polys]
    u = unary_union(polys)
    return len(u.geoms) if u.geom_type == "MultiPolygon" else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--vocab", required=True)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--render", type=int, default=12)
    ap.add_argument("--max-new", type=int, default=650)
    ap.add_argument("--constrained", action="store_true")
    ap.add_argument("--orthogonal", action="store_true")
    ap.add_argument("--country", type=int, default=1)
    ap.add_argument("--out", default="docs/runs/ar_geom.png")
    ap.add_argument("--seed", type=int, default=0,
                    help="재현성: >0이면 시드 고정 → 같은 모델+같은 시드 = 같은 생성·같은 clean율. 0=미설정.")
    args = ap.parse_args()

    if args.seed > 0:
        torch.manual_seed(args.seed)
        torch.cuda.manual_seed_all(args.seed)

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    vocab = json.load(open(args.vocab, encoding="utf-8"))
    ck = torch.load(args.ckpt, map_location=dev); a = ck["args"]
    model = WallCycleLM(vocab["size"], d_model=a["d_model"], n_layer=a["n_layer"],
                        n_head=a.get("n_head", 8), max_len=a["max_len"], dim_ff=a.get("dim_ff")).to(dev)
    model.load_state_dict(ck["model"]); model.eval()
    print(f"[ckpt] {args.ckpt} (epoch {ck.get('epoch')}) params={sum(p.numel() for p in model.parameters())/1e6:.1f}M on {dev}", flush=True)

    mask_fn = make_constraint_mask(vocab, orthogonal=args.orthogonal) if args.constrained else None
    bos, eos = wc.V.BOS, wc.V.EOS
    pre = [bos, vocab["meta"] + args.country, vocab["meta"] + len(wc.COUNTRIES) + 0,
           vocab["meta"] + len(wc.COUNTRIES) + len(wc.HOUSING) + 0,
           vocab["scope"] + 0, vocab["units"] + 1]

    tally = dict(decoded=0, single_strict=0, single_2pct=0, selfint0=0, overlap_ok=0, span_ok=0, clean=0)
    nrooms = []; clean_imgs = []
    ch = 4
    for i in range(0, args.n, ch):
        k = min(ch, args.n - i)
        prefix = torch.tensor([pre] * k, device=dev)
        out = model.generate(prefix, max_new=args.max_new, eos=eos, temperature=1.0, top_k=40, mask_fn=mask_fn)
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
            tally["decoded"] += 1
            m = DP.metrics(g)
            nrooms.append(m["n_rooms"])
            p1 = single_pieces(g, 0.0); p2 = single_pieces(g, 0.02)
            if p1 == 1: tally["single_strict"] += 1
            if p2 == 1: tally["single_2pct"] += 1
            if m["selfint_rooms"] == 0: tally["selfint0"] += 1
            if m["overlap_frac"] < 0.25: tally["overlap_ok"] += 1
            if m["span_ratio"] < 8: tally["span_ok"] += 1
            clean = m["selfint_rooms"] == 0 and m["overlap_frac"] < 0.25 and m["span_ratio"] < 8
            if clean:
                tally["clean"] += 1
                if len(clean_imgs) < args.render:
                    try:
                        geom = cadrender.autocorrect(cadrender.from_geomgraph(g))
                        png = cadrender.render_png(geom)
                        clean_imgs.append(Image.open(io.BytesIO(png)).convert("RGB"))
                    except Exception:
                        pass
    N = args.n
    def pct(x): return f"{x}/{N} ({100*x/N:.0f}%)"
    print("="*56, flush=True)
    print(f"생성 N={N}  country={args.country}  constrained={args.constrained} orthogonal={args.orthogonal}", flush=True)
    print(f"  decoded(rooms>=2)   : {pct(tally['decoded'])}", flush=True)
    print(f"  single(strict)      : {pct(tally['single_strict'])}", flush=True)
    print(f"  single(wall 2%)     : {pct(tally['single_2pct'])}", flush=True)
    print(f"  selfint=0           : {pct(tally['selfint0'])}", flush=True)
    print(f"  overlap<0.25        : {pct(tally['overlap_ok'])}", flush=True)
    print(f"  span<8              : {pct(tally['span_ok'])}", flush=True)
    print(f"  ★ clean(strict)     : {pct(tally['clean'])}", flush=True)
    if nrooms:
        nrooms.sort(); print(f"  rooms~ median {nrooms[len(nrooms)//2]} mean {sum(nrooms)/len(nrooms):.1f}", flush=True)
    if clean_imgs:
        cell, cols = 256, 4
        rows = max(1, math.ceil(len(clean_imgs)/cols))
        grid = Image.new("RGB", (cols*cell, rows*cell), (255,255,255))
        for idx, im in enumerate(clean_imgs):
            im = im.copy(); im.thumbnail((cell, cell))
            grid.paste(im, ((idx%cols)*cell, (idx//cols)*cell))
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        grid.save(args.out)
        print(f"montage {len(clean_imgs)} -> {args.out}", flush=True)


if __name__ == "__main__":
    main()
