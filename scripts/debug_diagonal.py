"""채택 샘플에서 사선이 어디서 생기나 — 필터(g) vs 렌더(autocorrect+벽) 분리 계측.

가설: is_clean은 g(rectify 후 방 폴리곤)를 검사하지만, 렌더는 autocorrect(from_geomgraph(g)).
autocorrect가 기하를 바꾸거나, 벽 세그먼트가 대각선이면 → 필터가 못 잡고 그림엔 사선.

각 채택 샘플에서:
  A) g 방 폴리곤 대각선   (필터가 보는 것)
  B) autocorrect 후 방 폴리곤 대각선 (실제 렌더되는 방)
  C) autocorrect 후 벽 세그먼트 대각선 (실제 렌더되는 벽)
"""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")
import torch  # noqa: E402

from plan2graph import cadrender, wallcycle_codec as wc  # noqa: E402
from plan2graph.generators.wall_cycle import WallCycleLM, make_constraint_mask  # noqa: E402
import render_geomclean as RG  # noqa: E402  (is_clean 재사용 — 동일 채택 기준)

TOL = 0.5


def _poly_diag(pts):
    pts = [tuple(p) for p in pts]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    n = len(pts)
    return sum(1 for i in range(n)
               if abs(pts[(i + 1) % n][0] - pts[i][0]) > TOL
               and abs(pts[(i + 1) % n][1] - pts[i][1]) > TOL)


def _seg_diag(seg):
    (ax, ay), (bx, by) = seg
    return abs(bx - ax) > TOL and abs(by - ay) > TOL


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--vocab", required=True)
    ap.add_argument("--accept", type=int, default=12)
    ap.add_argument("--country", type=int, default=0)
    args = ap.parse_args()

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    vocab = json.load(open(args.vocab, encoding="utf-8"))
    ck = torch.load(args.ckpt, map_location=dev); a = ck["args"]
    model = WallCycleLM(vocab["size"], d_model=a["d_model"], n_layer=a["n_layer"],
                        n_head=a.get("n_head", 8), max_len=a["max_len"]).to(dev)
    model.load_state_dict(ck["model"]); model.eval()
    mask_fn = make_constraint_mask(vocab, orthogonal=True)
    bos, eos = wc.V.BOS, wc.V.EOS
    pre = [bos, vocab["meta"] + args.country, vocab["meta"] + len(wc.COUNTRIES) + 0,
           vocab["meta"] + len(wc.COUNTRIES) + len(wc.HOUSING) + 0,
           vocab["scope"] + 0, vocab["units"] + 1]

    acc = 0
    print(f"{'#':>3} | A:g방대각 | B:렌더방대각 | C:벽대각 | rooms walls")
    while acc < args.accept:
        out = model.generate(torch.tensor([pre] * 4, device=dev), max_new=650,
                             eos=eos, temperature=1.0, top_k=40, mask_fn=mask_fn)
        for row in out.tolist():
            row = row[:row.index(eos) + 1] if eos in row else row
            try:
                g = wc.canon_to_graph(wc.decode(row, vocab))
            except Exception:  # noqa: BLE001
                continue
            for r in g["rooms"].values():            # 파이프라인과 동일: rectify 먼저
                if r.get("polygon"):
                    r["polygon"] = wc.rectify_diagonals(r["polygon"])
            ok, _ = RG.is_clean(g)
            if not ok:
                continue
            acc += 1
            A = sum(_poly_diag(r.get("polygon") or []) for r in g["rooms"].values())
            geom = cadrender.autocorrect(cadrender.from_geomgraph(g))
            B = sum(_poly_diag(rm.polygon) for rm in geom.rooms if rm.polygon)
            C = sum(1 for w in geom.walls if _seg_diag(w.seg))
            flag = "  ← 사선!" if (B > 0 or C > 0) else ""
            print(f"{acc:>3} | {A:>7} | {B:>9} | {C:>6} | {len(geom.rooms):>5} {len(geom.walls):>5}{flag}")
            if acc >= args.accept:
                break


if __name__ == "__main__":
    main()
