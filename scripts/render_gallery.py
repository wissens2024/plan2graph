"""논문 Figure 2(RPLAN)·3(한국) — 깨끗한 생성도면 갤러리.
생성 N → geom_ok(drawable)·pieces1·holes0 엄선 → fill·convex 상위 K장 grid montage.
대각선 stray 벽 제거. --with-doors면 문 주황·(창 파랑)도 표시(한국).
"""
import argparse, io, json, math, sys
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
import torch
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union
from plan2graph import wallcycle_codec as wc, cadrender
from plan2graph.generators.wall_cycle import WallCycleLM, make_constraint_mask
from plan2graph.gen_verify import verify_plan, estimate_scale
from plan2graph.graph_repair import repair_graph
from survey_outline import footprint_metrics
from render_geomclean import _seg_diag as _wall_diag


def n_holes_of(g):
    ps = [Polygon(r["polygon"]) for r in g["rooms"].values()
          if r.get("polygon") and len(r["polygon"]) >= 3]
    ps = [p for p in ps if p.is_valid and p.area > 1]
    if not ps:
        return 9
    u = unary_union(ps)
    gs = u.geoms if isinstance(u, MultiPolygon) else [u]
    return sum(len(p.interiors) for p in gs)


def render_one(g, with_doors):
    geom = cadrender.autocorrect(cadrender.from_geomgraph(g))
    geom.issues = []
    geom.walls = [w for w in geom.walls if not _wall_diag(w.seg)]
    doors = list(geom.doors)
    if with_doors:
        geom.doors = []
    fig = cadrender.render_fig(geom, dims=False, labels=True)
    if with_doors:
        geom.doors = doors
        ax = fig.axes[0]
        bx = geom.bbox; plan_w = max(bx[2] - bx[0], bx[3] - bx[1], 1.0)
        dw = max(5.0, min(0.06 * plan_w, 10.0))
        for d in doors:
            x, y = d.pos
            if cadrender._nearest_wall_axis(geom, (x, y)) == "h":
                ax.plot([x - dw / 2, x + dw / 2], [y, y], color="#e8703a", lw=3.0, zorder=6, solid_capstyle="butt")
            else:
                ax.plot([x, x], [y - dw / 2, y + dw / 2], color="#e8703a", lw=3.0, zorder=6, solid_capstyle="butt")
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=140, bbox_inches="tight"); plt.close(fig)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True); ap.add_argument("--vocab", required=True)
    ap.add_argument("--n", type=int, default=150); ap.add_argument("--country", type=int, default=0)
    ap.add_argument("--seed", type=int, default=3); ap.add_argument("--k", type=int, default=6)
    ap.add_argument("--cols", type=int, default=3); ap.add_argument("--with-doors", action="store_true")
    ap.add_argument("--out", default="/tmp/gallery.png")
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
    mask_fn = make_constraint_mask(vocab, orthogonal=True)
    bos, eos = wc.V.BOS, wc.V.EOS
    pre = [bos, vocab["meta"] + args.country, vocab["meta"] + len(wc.COUNTRIES) + 0,
           vocab["meta"] + len(wc.COUNTRIES) + len(wc.HOUSING) + 0,
           vocab["scope"] + 0, vocab["units"] + 1]

    cands = []
    ch = 4
    for i in range(0, args.n, ch):
        k = min(ch, args.n - i)
        prefix = torch.tensor([pre] * k, device=dev)
        out = model.generate(prefix, max_new=650, eos=eos, temperature=1.0, top_k=40, mask_fn=mask_fn)
        for row in out.tolist():
            row = row[:row.index(eos) + 1] if eos in row else row
            try:
                g = wc.canon_to_graph(wc.decode(row, vocab))
            except Exception:
                continue
            if len(g.get("rooms") or {}) < 4:
                continue
            try:
                repair_graph(g, drop_bad=True, declash="wall")
            except Exception:
                continue
            estimate_scale(g)
            v = verify_plan(g)
            if not v["geom_ok"]:
                continue
            fp = footprint_metrics(g)
            if not fp or fp["pieces"] != 1 or n_holes_of(g) > 0:
                continue
            score = 10 * fp["fill"] + 6 * fp["convex"] - 0.2 * fp["ncorner"]
            cands.append((score, g))
    cands.sort(key=lambda x: -x[0])
    print(f"[엄선 솔리드 후보: {len(cands)}]  상위 {args.k} 렌더")
    top = cands[:args.k]
    if not top:
        print("NO candidate"); return
    imgs = [render_one(g, args.with_doors) for _, g in top]
    cols = args.cols; rows = math.ceil(len(imgs) / cols)
    cw = max(im.width for im in imgs); chh = max(im.height for im in imgs); pad = 18
    cv = Image.new("RGB", (cols * cw + (cols + 1) * pad, rows * chh + (rows + 1) * pad), "white")
    for idx, im in enumerate(imgs):
        r, c = divmod(idx, cols)
        x = pad + c * (cw + pad) + (cw - im.width) // 2
        y = pad + r * (chh + pad) + (chh - im.height) // 2
        cv.paste(im, (x, y))
    cv.save(args.out)
    print(f"[saved] {args.out}  ({len(imgs)}장 {cols}x{rows})")


if __name__ == "__main__":
    main()
