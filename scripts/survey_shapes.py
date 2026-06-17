"""방 폴리곤 모양 조사 — 알고리즘 짜기 전 '실제 모양'부터 파악.

가설(사용자): 직각(rectilinear) 폴리곤은 코너가 항상 *짝수*(4=직사각형·6=L자/복도귀퉁이·8···).
홀수 코너(3·5·7)는 대각선 변이 끼었다는 뜻 = 퇴화 아티팩트.

각 방: 공선점 제거 → 코너수 nc · 대각선 변 수 n_diag(dx≠0 ∧ dy≠0) · rectilinear(n_diag==0).
집계: nc 분포, nc별 직각/대각 구성, 홀짝 ↔ 직각성 교차검증.

실행(GPU0):
  CUDA_VISIBLE_DEVICES=0 PYTHONPATH=src python scripts/survey_shapes.py \
    --ckpt ckpts/korplan_ar_r.pt --vocab data/staging/tokens_rplan/vocab.json --n 128 --orthogonal
"""
from __future__ import annotations

import argparse
import collections
import json
import sys

sys.path.insert(0, "src")
import torch  # noqa: E402

from plan2graph import wallcycle_codec as wc  # noqa: E402
from plan2graph.generators.wall_cycle import WallCycleLM, make_constraint_mask  # noqa: E402

TOL = 0.5   # grid 양자화 좌표 — 0.5px 이하는 같은 선/점 취급


def _dedup_close(pts):
    pts = [tuple(p) for p in pts]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts


def _corners(pts):
    """공선점 제거 → 방향 전환점(진짜 코너)만."""
    n = len(pts)
    if n < 3:
        return pts
    out = []
    for i in range(n):
        a, b, c = pts[(i - 1) % n], pts[i], pts[(i + 1) % n]
        cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if abs(cross) > TOL:
            out.append(b)
    return out


def _n_diag(corners):
    """대각선 변 수 = dx≠0 ∧ dy≠0인 변."""
    n = len(corners)
    d = 0
    for i in range(n):
        a, b = corners[i], corners[(i + 1) % n]
        if abs(b[0] - a[0]) > TOL and abs(b[1] - a[1]) > TOL:
            d += 1
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--vocab", required=True)
    ap.add_argument("--n", type=int, default=128)
    ap.add_argument("--orthogonal", action="store_true")
    ap.add_argument("--rectify", action="store_true", help="측정 전 rectify_diagonals 적용(수리 효과 검증)")
    ap.add_argument("--country", type=int, default=0)
    args = ap.parse_args()

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    vocab = json.load(open(args.vocab, encoding="utf-8"))
    ck = torch.load(args.ckpt, map_location=dev)
    a = ck["args"]
    model = WallCycleLM(vocab["size"], d_model=a["d_model"], n_layer=a["n_layer"],
                        n_head=a.get("n_head", 8), max_len=a["max_len"]).to(dev)
    model.load_state_dict(ck["model"]); model.eval()
    mask_fn = make_constraint_mask(vocab, orthogonal=args.orthogonal)
    bos, eos = wc.V.BOS, wc.V.EOS
    pre = [bos, vocab["meta"] + args.country, vocab["meta"] + len(wc.COUNTRIES) + 0,
           vocab["meta"] + len(wc.COUNTRIES) + len(wc.HOUSING) + 0,
           vocab["scope"] + 0, vocab["units"] + 1]

    hist = collections.Counter()              # nc -> count
    rect_by_nc = collections.Counter()        # nc -> rectilinear count
    diaghist = collections.Counter()          # n_diag -> count
    nrooms = nplans = 0
    n_rect = n_oddrect = n_oddtotal = n_eventotal = n_evenrect = 0

    ch = 4
    for i in range(0, args.n, ch):
        k = min(ch, args.n - i)
        out = model.generate(torch.tensor([pre] * k, device=dev), max_new=650,
                             eos=eos, temperature=1.0, top_k=40, mask_fn=mask_fn)
        for row in out.tolist():
            row = row[:row.index(eos) + 1] if eos in row else row
            try:
                g = wc.canon_to_graph(wc.decode(row, vocab))
            except Exception:  # noqa: BLE001
                continue
            nplans += 1
            for r in g["rooms"].values():
                poly = r.get("polygon") or []
                if args.rectify and poly:
                    poly = wc.rectify_diagonals(poly)
                pts = _dedup_close(poly)
                if len(pts) < 3:
                    continue
                cs = _corners(pts)
                nc = len(cs)
                nd = _n_diag(cs)
                rect = (nd == 0)
                hist[nc] += 1
                diaghist[nd] += 1
                if rect:
                    rect_by_nc[nc] += 1; n_rect += 1
                nrooms += 1
                if nc % 2 == 1:
                    n_oddtotal += 1; n_oddrect += rect
                else:
                    n_eventotal += 1; n_evenrect += rect

    print(f"plans={nplans}  rooms={nrooms}")
    print(f"\n코너수(nc) 분포 — [직각 / 대각포함]:")
    for nc in sorted(hist):
        tot = hist[nc]; rc = rect_by_nc[nc]
        print(f"  nc={nc:2d}: {tot:4d}  [직각 {rc:4d} / 대각 {tot-rc:3d}]"
              f"{'  ← 홀수(대각 의심)' if nc % 2 == 1 else ''}")
    print(f"\n대각선 변 수(n_diag) 분포: {dict(sorted(diaghist.items()))}")
    print(f"\n=== 가설 검증 ===")
    print(f"  전체 직각방(대각0)   : {n_rect}/{nrooms} ({100*n_rect/max(nrooms,1):.1f}%)")
    print(f"  홀수 코너 방         : {n_oddtotal}/{nrooms} ({100*n_oddtotal/max(nrooms,1):.1f}%)  "
          f"그중 직각 {n_oddrect} ({100*n_oddrect/max(n_oddtotal,1):.0f}%)")
    print(f"  짝수 코너 방         : {n_eventotal}/{nrooms}  그중 직각 {n_evenrect} "
          f"({100*n_evenrect/max(n_eventotal,1):.0f}%)")


if __name__ == "__main__":
    main()
