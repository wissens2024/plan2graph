"""before/after repair Figure — 엄선된 1장(위상·기하 조화, 구멍·gap 없음).
선택: drawable + footprint(pieces=1·fill≥0.85·convex≥0.80·holes=0·단순외곽) + before 규제위반
+ after 합법 + 창·문 추가 가시. 많이 생성→best 1장. 렌더 render_fig(dims=False, 경고숨김).
"""
import argparse, copy, io, json, sys
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
import torch
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union
from plan2graph import wallcycle_codec as wc, cadrender
from plan2graph.generators.wall_cycle import WallCycleLM, make_constraint_mask
from plan2graph.gen_verify import (verify_plan, legal_repair, egress_repair,
                                   snap_windows, estimate_scale)
from plan2graph.graph_repair import repair_graph
from survey_outline import footprint_metrics
from render_geomclean import _seg_diag as _wall_diag


def render(geom):
    geom.issues = []
    geom.walls = [w for w in geom.walls if not _wall_diag(w.seg)]   # 대각선 stray 벽 제거(몽타주식)
    _doors = list(geom.doors)
    geom.doors = []                # cadrender 기본 회색 문선(과대) 끄고 → 주황만 직접
    fig = cadrender.render_fig(geom, dims=False, labels=True)
    geom.doors = _doors
    ax = fig.axes[0]
    bx = geom.bbox                             # 좌표=그래프 단위 → 문 폭을 평면 크기 비례로
    plan_w = max(bx[2] - bx[0], bx[3] - bx[1], 1.0)
    dw = max(5.0, min(0.06 * plan_w, 10.0))    # 문 개구부 ≈ 평면의 6% (≈0.9m)
    for d in geom.doors:                       # 문을 주황 굵은 개구부로 강조(L3 동선 가시화)
        x, y = d.pos
        if cadrender._nearest_wall_axis(geom, (x, y)) == "h":
            ax.plot([x - dw / 2, x + dw / 2], [y, y], color="#e8703a", lw=3.6, zorder=6, solid_capstyle="butt")
        else:
            ax.plot([x, x], [y - dw / 2, y + dw / 2], color="#e8703a", lw=3.6, zorder=6, solid_capstyle="butt")
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=170, bbox_inches="tight"); plt.close(fig)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def n_holes(g):
    ps = []
    for r in g["rooms"].values():
        pp = r.get("polygon") or []
        if len(pp) >= 3:
            try:
                q = Polygon(pp)
                if q.is_valid and q.area > 1:
                    ps.append(q)
            except Exception:
                pass
    if not ps:
        return 99
    u = unary_union(ps)
    geoms = u.geoms if isinstance(u, MultiPolygon) else [u]
    return sum(len(p.interiors) for p in geoms)


def viol_rules(v):
    return set(x.get("rule", "") for x in v["legal_violations"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--vocab", required=True)
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--country", type=int, default=0)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--out", default="/tmp/before_after")
    ap.add_argument("--from-cache", default="", help="저장된 _graphs.json에서 즉시 재렌더(생성 생략)")
    args = ap.parse_args()

    if args.from_cache:
        data = json.load(open(args.from_cache))
        gB = cadrender.autocorrect(cadrender.from_geomgraph(data["before"]))
        gA = cadrender.autocorrect(cadrender.from_geomgraph(data["after"]))
        imgB = render(gB); imgA = render(gA)
        imgB.save(args.out + "_before.png"); imgA.save(args.out + "_after.png")
        pad, lab = 30, 44
        cv = Image.new("RGB", (imgB.width + imgA.width + pad * 3,
                               max(imgB.height, imgA.height) + lab + pad), "white")
        dr = ImageDraw.Draw(cv)
        dr.text((pad, 12), "(a) AI raw output: habitable rooms without windows, no egress path", fill="black")
        dr.text((imgB.width + pad * 2, 12),
                "(b) After regulatory repair: daylight/vent windows (blue) + egress doors (orange) added", fill="black")
        cv.paste(imgB, (pad, lab)); cv.paste(imgA, (imgB.width + pad * 2, lab))
        cv.save(args.out + "_montage.png")
        print(f"[from-cache] {args.out}_montage.png"); return

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

    best = None; best_score = float("-inf"); n_ok = 0
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
            nr = len(g.get("rooms") or {})
            if nr < 6 or nr > 12:
                continue
            try:
                repair_graph(g, drop_bad=True, declash="wall")
            except Exception:
                continue
            estimate_scale(g)
            vb = verify_plan(g)
            if not vb["geom_ok"] or vb["legal_ok"]:
                continue
            fp = footprint_metrics(g)
            if not fp or fp["pieces"] != 1:        # 분리조각은 제외(최소 게이트)
                continue
            holes = n_holes(g)
            before = copy.deepcopy(g)
            acts = legal_repair(g) or []
            eacts = egress_repair(g) or []
            snap_windows(g)
            estimate_scale(g)
            va = verify_plan(g)
            if len(acts) + len(eacts) == 0:
                continue
            try:
                geomB = cadrender.autocorrect(cadrender.from_geomgraph(before))
                geomA = cadrender.autocorrect(cadrender.from_geomgraph(g))
            except Exception:
                continue
            n_iss = len(geomB.issues) + len(geomA.issues)
            n_ok += 1
            vb_r, va_r = viol_rules(vb), viol_rules(va)
            egress_fixed = any("L3" in r for r in vb_r) and "L3_egress_reachable" not in va_r
            daylight_fixed = (any(r.startswith(("L1_daylight_window", "L2")) for r in vb_r)
                              and not any(r.startswith(("L1_daylight_window", "L2")) for r in va_r))
            score = (10 * int(va["legal_ok"]) + 6 * int(egress_fixed) + 10 * int(daylight_fixed)
                     + 8 * fp["fill"] + 6 * fp["convex"] - 30 * holes - 10 * n_iss
                     - abs(nr - 8) - 0.3 * fp["ncorner"])
            if score > best_score:
                best_score = score
                best = dict(geomB=geomB, geomA=geomA, vb=vb, va=va,
                            acts=acts, eacts=eacts, nr=nr, fp=fp, iss=n_iss, holes=holes,
                            graphB=before, graphA=copy.deepcopy(g))

    print(f"[엄선 후보: {n_ok}]")
    if not best:
        print("NO candidate — 기준 완화 또는 seed 변경 필요"); return
    try:                                  # 선택 도면 캐시(즉시 재렌더용)
        json.dump({"before": best["graphB"], "after": best["graphA"]},
                  open(args.out + "_graphs.json", "w"))
        print(f"[cache] {args.out}_graphs.json")
    except Exception as e:
        print("cache save fail:", e)
    imgB = render(best["geomB"]); imgA = render(best["geomA"])
    imgB.save(args.out + "_before.png"); imgA.save(args.out + "_after.png")
    pad, lab = 30, 44
    H = max(imgB.height, imgA.height)
    cv = Image.new("RGB", (imgB.width + imgA.width + pad * 3, H + lab + pad), "white")
    dr = ImageDraw.Draw(cv)
    dr.text((pad, 12), "(a) AI raw output: habitable rooms without windows, no egress path", fill="black")
    dr.text((imgB.width + pad * 2, 12),
            "(b) After regulatory repair: daylight/vent windows (blue) + egress doors (orange) added", fill="black")
    cv.paste(imgB, (pad, lab)); cv.paste(imgA, (imgB.width + pad * 2, lab))
    cv.save(args.out + "_montage.png")
    fp = best["fp"]
    print(f"[saved] {args.out}_montage.png  rooms={best['nr']} score={best_score:.1f} "
          f"fill={fp['fill']:.2f} convex={fp['convex']:.2f} ncorner={fp['ncorner']} "
          f"holes={best['holes']} issues={best['iss']}")
    print(f"  before viol: {sorted(viol_rules(best['vb']))}")
    print(f"  after  viol: {sorted(viol_rules(best['va']))}")
    print(f"  repair: {len(best['acts'])} daylight/vent + {len(best['eacts'])} egress")


if __name__ == "__main__":
    main()
