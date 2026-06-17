"""외곽(footprint) 품질 조사 — 생성물 vs 진짜 RPLAN 데이터 비교.

방은 직각이어도 *전체 외곽*이 들쭉날쭉하면 실제 도면 아님(계단식·삐죽 protrusion).
지표(도면당):
  fill   = footprint 면적 / bbox 면적   (1.0=꽉찬 직사각형, 낮을수록 들쭉)
  ncorner= 외곽 경계 코너수(공선 제거)   (4=직사각, 6=L, 클수록 들쭉)
  pieces = union 조각수                  (1=연결, >1=분리=치명)
  convex = 면적 / 볼록껍질 면적           (1=볼록, 낮을수록 오목/삐죽)

생성: --ckpt    진짜데이터: --data train.jsonl   둘 다 돌려 분포 비교.
실행(GPU0):
  CUDA_VISIBLE_DEVICES=0 PYTHONPATH=src python scripts/survey_outline.py \
    --ckpt ckpts/korplan_ar_r.pt --vocab data/staging/tokens_rplan/vocab.json --n 200 --orthogonal
  PYTHONPATH=src python scripts/survey_outline.py \
    --data data/staging/tokens_rplan/train.jsonl --vocab data/staging/tokens_rplan/vocab.json --n 400
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys

sys.path.insert(0, "src")
from plan2graph import wallcycle_codec as wc  # noqa: E402

from shapely.geometry import Polygon, MultiPolygon  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

TOL = 0.5


def _corners(ext):
    pts = [tuple(p) for p in ext]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    n = len(pts)
    if n < 3:
        return n
    c = 0
    for i in range(n):
        a, b, d = pts[(i - 1) % n], pts[i], pts[(i + 1) % n]
        cross = (b[0] - a[0]) * (d[1] - a[1]) - (b[1] - a[1]) * (d[0] - a[0])
        if abs(cross) > TOL:
            c += 1
    return c


def footprint_metrics(g):
    polys = []
    for r in g["rooms"].values():
        pp = r.get("polygon") or []
        if len(pp) >= 3:
            try:
                q = Polygon(pp)
                if q.is_valid and q.area > 1:
                    polys.append(q)
            except Exception:  # noqa: BLE001
                pass
    if not polys:
        return None
    u = unary_union(polys)
    pieces = len(u.geoms) if isinstance(u, MultiPolygon) else 1
    main = max(u.geoms, key=lambda p: p.area) if isinstance(u, MultiPolygon) else u
    minx, miny, maxx, maxy = u.bounds
    bboxA = max((maxx - minx) * (maxy - miny), 1.0)
    fill = u.area / bboxA
    try:
        convex = u.area / max(u.convex_hull.area, 1.0)
    except Exception:  # noqa: BLE001
        convex = 0.0
    ncorner = _corners(list(main.exterior.coords))
    return dict(fill=fill, ncorner=ncorner, pieces=pieces, convex=convex)


def _iter_real(path, vocab, n):
    for i, line in enumerate(open(path, encoding="utf-8")):
        if i >= n:
            break
        line = line.strip()
        if not line:
            continue
        try:
            t = json.loads(line)["tokens"]
            yield wc.canon_to_graph(wc.decode(t, vocab))
        except Exception:  # noqa: BLE001
            continue


def _iter_gen(args, vocab, n):
    import torch
    from plan2graph.generators.wall_cycle import WallCycleLM, make_constraint_mask
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    ck = torch.load(args.ckpt, map_location=dev); a = ck["args"]
    model = WallCycleLM(vocab["size"], d_model=a["d_model"], n_layer=a["n_layer"],
                        n_head=a.get("n_head", 8), max_len=a["max_len"]).to(dev)
    model.load_state_dict(ck["model"]); model.eval()
    mask_fn = make_constraint_mask(vocab, orthogonal=args.orthogonal)
    bos, eos = wc.V.BOS, wc.V.EOS
    pre = [bos, vocab["meta"] + args.country, vocab["meta"] + len(wc.COUNTRIES) + 0,
           vocab["meta"] + len(wc.COUNTRIES) + len(wc.HOUSING) + 0,
           vocab["scope"] + 0, vocab["units"] + 1]
    done = 0
    while done < n:
        k = min(4, n - done)
        out = model.generate(torch.tensor([pre] * k, device=dev), max_new=650,
                             eos=eos, temperature=1.0, top_k=40, mask_fn=mask_fn)
        for row in out.tolist():
            row = row[:row.index(eos) + 1] if eos in row else row
            done += 1
            try:
                yield wc.canon_to_graph(wc.decode(row, vocab))
            except Exception:  # noqa: BLE001
                continue


def _stat(xs):
    xs = sorted(xs)
    if not xs:
        return "—"
    q = lambda p: xs[min(len(xs) - 1, int(p * len(xs)))]
    return f"med {statistics.median(xs):.2f}  p10 {q(0.1):.2f}  p90 {q(0.9):.2f}  mean {statistics.mean(xs):.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt"); ap.add_argument("--data")
    ap.add_argument("--vocab", required=True)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--orthogonal", action="store_true")
    ap.add_argument("--country", type=int, default=0)
    args = ap.parse_args()
    vocab = json.load(open(args.vocab, encoding="utf-8"))

    src = "REAL(data)" if args.data else "GEN(ckpt)"
    it = _iter_real(args.data, vocab, args.n) if args.data else _iter_gen(args, vocab, args.n)

    fills, ncs, convs = [], [], []
    npieces = nplans = 0
    for g in it:
        m = footprint_metrics(g)
        if not m:
            continue
        nplans += 1
        fills.append(m["fill"]); ncs.append(m["ncorner"]); convs.append(m["convex"])
        npieces += (m["pieces"] > 1)
    print(f"\n[{src}]  plans={nplans}")
    print(f"  fill(채움비)   : {_stat(fills)}")
    print(f"  convex(볼록도) : {_stat(convs)}")
    print(f"  ncorner(외곽)  : {_stat([float(x) for x in ncs])}")
    print(f"  분리(pieces>1) : {npieces}/{nplans} ({100*npieces/max(nplans,1):.1f}%)")
    # 외곽 코너수 히스토그램
    import collections
    h = collections.Counter(ncs)
    print(f"  외곽 코너 분포  : {dict(sorted(h.items()))}")


if __name__ == "__main__":
    main()
