"""논문 그림용 도면 재생성 (미적 큐레이션판) — 여러 시드로 대량 생성 → 엄격 필터 통과분만 저장.

미적 게이트(예쁜 도면):
  strict_clean(직각·꼭짓점>=4·외곽 1덩어리·채움·볼록)
  AND 내부 구멍 0 (흰 빈공간 없음)  ← 가장 큰 미관 킬러
  AND 외곽 볼록충진(hull fill) 높음  AND 겹침 낮음
통과분만 JSON 저장 + beauty score(=fill - overlap) 기록. 나머지는 버림.

사용:
  python gen_fig_plans.py --ckpt .. --vocab .. --train .. --tag korean \
     --seeds 42,7,13,2025,111 --per-seed 50 --out-dir OUT [--doors]
"""
from __future__ import annotations
import argparse, csv, json, os, sys
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
import torch
from shapely.geometry import Polygon
from shapely.ops import unary_union
from plan2graph import wallcycle_codec as wc
from plan2graph.generators.wall_cycle import WallCycleLM, make_constraint_mask
from plan2graph.graph_repair import repair_graph
from render_geomclean import is_clean as _strict_clean


def read_prefix(train_path):
    line = open(train_path, encoding="utf-8").readline()
    o = json.loads(line); toks = o.get("tokens") or o.get("ids") or o
    if isinstance(toks, dict):
        toks = toks.get("tokens") or toks.get("ids")
    return toks[:toks.index(wc.V.SEC_CORNERS)]


def beauty(g):
    """미적 지표 — pieces/holes/fill(볼록충진)/overlap/aspect."""
    polys = []
    for r in (g.get("rooms") or {}).values():
        pp = r.get("polygon") or []
        if len(pp) >= 3:
            try:
                q = Polygon(pp)
                if q.is_valid and q.area > 1:
                    polys.append(q)
            except Exception:
                pass
    if not polys:
        return None
    u = unary_union(polys)
    geoms = list(u.geoms) if u.geom_type == "MultiPolygon" else [u]
    pieces = len(geoms)
    holes = sum(len(p.interiors) for p in geoms)
    hull = u.convex_hull.area
    fill = u.area / hull if hull > 0 else 0.0
    sa = sum(p.area for p in polys)
    overlap = 1 - u.area / sa if sa > 0 else 1.0
    minx, miny, maxx, maxy = u.bounds
    w, h = maxx - minx, maxy - miny
    aspect = max(w, h) / max(1e-6, min(w, h))
    return dict(pieces=pieces, holes=holes, fill=round(fill, 3),
                overlap=round(max(0.0, overlap), 3), aspect=round(aspect, 2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--vocab", required=True)
    ap.add_argument("--train", required=True)
    ap.add_argument("--seeds", default="42", help="쉼표 시드 목록")
    ap.add_argument("--per-seed", type=int, default=50)
    ap.add_argument("--max-new", type=int, default=650)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out-dir", required=True)
    # 미적 게이트 임계 (튜닝 가능)
    ap.add_argument("--min-fill", type=float, default=0.82)
    ap.add_argument("--max-overlap", type=float, default=0.12)
    ap.add_argument("--max-aspect", type=float, default=1.9)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    vocab = json.load(open(args.vocab, encoding="utf-8"))
    ck = torch.load(args.ckpt, map_location=dev); a = ck["args"]
    dimff = ck["model"]["blocks.0.mlp.w1.weight"].shape[0]
    model = WallCycleLM(vocab["size"], d_model=a["d_model"], n_layer=a["n_layer"],
                        n_head=a.get("n_head", 8), max_len=a["max_len"], dim_ff=dimff).to(dev)
    model.load_state_dict(ck["model"]); model.eval()
    pre = read_prefix(args.train)
    mask_fn = make_constraint_mask(vocab, orthogonal=True)
    eos = wc.V.EOS
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    print(f"[ckpt] {os.path.basename(args.ckpt)} ep{ck.get('epoch')} prefix={pre} seeds={seeds}", flush=True)

    rows = []; kept = 0; total = 0; ch = 4
    for sd in seeds:
        torch.manual_seed(sd); torch.cuda.manual_seed_all(sd)
        for i in range(0, args.per_seed, ch):
            k = min(ch, args.per_seed - i)
            prefix = torch.tensor([pre] * k, device=dev)
            out = model.generate(prefix, max_new=args.max_new, eos=eos, temperature=1.0,
                                 top_k=40, mask_fn=mask_fn)
            for row in out.tolist():
                row = row[:row.index(eos) + 1] if eos in row else row
                total += 1
                try:
                    g = wc.canon_to_graph(wc.decode(row, vocab))
                except Exception:
                    continue
                if len(g.get("rooms") or {}) < 2:
                    continue
                repair_graph(g, drop_bad=True, declash="wall")
                try:
                    sclean = bool(_strict_clean(g)[0])
                except Exception:
                    sclean = False
                if not sclean:
                    continue
                b = beauty(g)
                if not b:
                    continue
                # 하드 미적 게이트: 단일 외곽 · 구멍 없음 · 볼록충진 · 저겹침 · 정방형쪽
                if b["pieces"] != 1 or b["holes"] != 0:
                    continue
                if b["fill"] < args.min_fill or b["overlap"] > args.max_overlap or b["aspect"] > args.max_aspect:
                    continue
                score = round(b["fill"] - b["overlap"] - 0.05 * (b["aspect"] - 1), 3)
                name = f"{args.tag}_{kept:03d}"
                json.dump(g, open(os.path.join(args.out_dir, name + ".json"), "w"), ensure_ascii=False)
                rows.append(dict(file=name, seed=sd, rooms=len(g.get("rooms") or {}),
                                 doors=len(g.get("doors") or []), windows=len(g.get("windows") or []),
                                 fill=b["fill"], overlap=b["overlap"], aspect=b["aspect"], score=score))
                kept += 1
    rows.sort(key=lambda r: -r["score"])
    with open(os.path.join(args.out_dir, f"manifest_{args.tag}.csv"), "w", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=["file", "seed", "rooms", "doors", "windows",
                                           "fill", "overlap", "aspect", "score"])
        w.writeheader(); w.writerows(rows)
    print(f"[{args.tag}] 생성 {total} → 미적통과 {kept} 저장. 상위 score: "
          + ", ".join(f"{r['file']}({r['score']})" for r in rows[:6]), flush=True)


if __name__ == "__main__":
    main()
