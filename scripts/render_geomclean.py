"""기하-only 깨끗본 렌더 — 법규 게이트 없이 *배치 품질만* 본다.

RPLAN엔 창이 없어 법규(채광/환기 창 보유) 게이트는 부적합(전부 탈락) → 배치만 평가.
clean 기준 = EXPERIMENTS clean 정의 (diag_placement.metrics):
  selfint_rooms==0  ∧  overlap_frac<0.25(실폴리곤 겹침)  ∧  span_ratio<8
생성 N → clean 필터 → 깨끗본만 몽타주 1장.

사용(GPU0):
  CUDA_VISIBLE_DEVICES=0 PYTHONPATH=src python scripts/render_geomclean.py \
    --ckpt ckpts/korplan_ar_r.pt --vocab data/staging/tokens_rplan/vocab.json \
    --n 64 --render 12 --constrained --orthogonal --out docs/runs/ar_ep200_geomclean.png
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import sys

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")
import torch  # noqa: E402
from PIL import Image  # noqa: E402

from plan2graph import cadrender, wallcycle_codec as wc  # noqa: E402
from plan2graph.generators.wall_cycle import WallCycleLM, make_constraint_mask  # noqa: E402
import diag_placement as DP  # noqa: E402
from survey_outline import footprint_metrics  # noqa: E402  (외곽 품질 — 진짜 RPLAN 기준)

# footprint 거절 임계 (진짜 RPLAN 분포 기준: 분리 1%·fill p10 0.68·convex p10 0.82)
FP_FILL_MIN, FP_CONVEX_MIN = 0.60, 0.75


def _min_unique_verts(g):
    """가장 적은 방의 고유 꼭짓점 수 (삼각형=3 → 직각형태 불가, 거절)."""
    mn = 99
    for r in g["rooms"].values():
        pts = [tuple(p) for p in (r.get("polygon") or [])]
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        if pts:
            mn = min(mn, len(set(pts)))
    return mn


def _seg_diag(seg, tol=0.5):
    """벽 세그먼트가 대각선(dx≠0 ∧ dy≠0)인가 — 잉여 대각선 벽 드롭용."""
    (ax, ay), (bx, by) = seg
    return abs(bx - ax) > tol and abs(by - ay) > tol


def _has_diagonal(g, tol=0.5):
    """방 폴리곤에 대각선 변(dx≠0 ∧ dy≠0)이 하나라도 있나 — 수리 못 한 잔여 거절용."""
    for r in g["rooms"].values():
        pts = [tuple(p) for p in (r.get("polygon") or [])]
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        n = len(pts)
        for i in range(n):
            a, b = pts[i], pts[(i + 1) % n]
            if abs(b[0] - a[0]) > tol and abs(b[1] - a[1]) > tol:
                return True
    return False


def is_clean(g):
    try:
        m = DP.metrics(g)
    except Exception:
        return False, None
    if not m:
        return False, None
    # 방-level: 배치 clean + 대각선 0 + 꼭짓점≥4
    ok = (m["selfint_rooms"] == 0 and m["overlap_frac"] < 0.25
          and m["span_ratio"] < 8 and _min_unique_verts(g) >= 4
          and not _has_diagonal(g))
    if not ok:
        return False, m
    # footprint-level: ★외곽 품질(진짜 RPLAN 기준) — 연결 1덩어리·채움·볼록
    fp = footprint_metrics(g)
    if not fp or fp["pieces"] != 1 or fp["fill"] < FP_FILL_MIN or fp["convex"] < FP_CONVEX_MIN:
        return False, m
    return True, m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--vocab", required=True)
    ap.add_argument("--n", type=int, default=64)
    ap.add_argument("--render", type=int, default=12)
    ap.add_argument("--max-new", type=int, default=650)
    ap.add_argument("--constrained", action="store_true")
    ap.add_argument("--orthogonal", action="store_true")
    ap.add_argument("--no-rectilinear", dest="rectilinear", action="store_false",
                    help="끄면 대각선 그대로(기본=켬: 잔여 대각선을 H/V로 스냅)")
    ap.add_argument("--country", type=int, default=0)
    ap.add_argument("--out", default="docs/runs/ar_ep200_geomclean.png")
    args = ap.parse_args()

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    vocab = json.load(open(args.vocab, encoding="utf-8"))
    ck = torch.load(args.ckpt, map_location=dev)
    a = ck["args"]
    model = WallCycleLM(vocab["size"], d_model=a["d_model"], n_layer=a["n_layer"],
                        n_head=a.get("n_head", 8), max_len=a["max_len"]).to(dev)
    model.load_state_dict(ck["model"]); model.eval()
    print(f"[ckpt] {args.ckpt} (epoch {ck.get('epoch')}) on {dev}", flush=True)

    mask_fn = make_constraint_mask(vocab, orthogonal=args.orthogonal) if args.constrained else None
    bos, eos = wc.V.BOS, wc.V.EOS
    pre = [bos, vocab["meta"] + args.country, vocab["meta"] + len(wc.COUNTRIES) + 0,
           vocab["meta"] + len(wc.COUNTRIES) + len(wc.HOUSING) + 0,
           vocab["scope"] + 0, vocab["units"] + 1]

    clean_imgs = []
    n_total = n_clean = 0
    ch = 4
    for i in range(0, args.n, ch):
        k = min(ch, args.n - i)
        prefix = torch.tensor([pre] * k, device=dev)
        out = model.generate(prefix, max_new=args.max_new, eos=eos,
                             temperature=1.0, top_k=40, mask_fn=mask_fn)
        for row in out.tolist():
            row = row[:row.index(eos) + 1] if eos in row else row
            n_total += 1
            try:
                g = wc.canon_to_graph(wc.decode(row, vocab))
            except Exception:
                continue
            if args.rectilinear:                              # 대각선 → L-코너 삽입 직각화(보존)
                for r in g["rooms"].values():
                    if r.get("polygon"):
                        r["polygon"] = wc.rectify_diagonals(r["polygon"])
            ok, m = is_clean(g)
            if not ok:
                continue
            try:
                geom = cadrender.autocorrect(cadrender.from_geomgraph(g))
                geom.walls = [w for w in geom.walls if not _seg_diag(w.seg)]  # 잉여 대각선 벽 제거(방경계=진짜 벽)
                png = cadrender.render_png(geom)
                clean_imgs.append(Image.open(io.BytesIO(png)).convert("RGB"))
                n_clean += 1
                print(f"clean_{n_clean}: rooms={m['n_rooms']} selfint={m['selfint_rooms']} "
                      f"overlap={m['overlap_frac']} span={m['span_ratio']}", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"render err: {type(e).__name__}: {e}", flush=True)
            if n_clean >= args.render:
                break
        if n_clean >= args.render:
            break

    rate = 100 * n_clean / max(n_total, 1)
    print(f"clean {n_clean}/{n_total} ({rate:.0f}% — 법규 제외, 배치 기준)", flush=True)
    if not clean_imgs:
        print("no clean samples", flush=True)
        return
    cell, cols = 256, 4
    rows = max(1, math.ceil(len(clean_imgs) / cols))
    grid = Image.new("RGB", (cols * cell, rows * cell), (255, 255, 255))
    for idx, im in enumerate(clean_imgs):
        im = im.copy(); im.thumbnail((cell, cell))
        grid.paste(im, ((idx % cols) * cell, (idx // cols) * cell))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    grid.save(args.out)
    print(f"montage {len(clean_imgs)} -> {args.out}", flush=True)


if __name__ == "__main__":
    main()
