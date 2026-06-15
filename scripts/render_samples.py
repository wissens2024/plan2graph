"""ckpt로 도면 생성 → 무조건 렌더 PNG (검증 통과 무관, 눈으로 도면 확인용).

생성 토큰 → decode → g-0.4 → cadrender(autocorrect) → PNG. OOM 회피(배치 분할 + max_new 제한).

사용:
  CUDA_VISIBLE_DEVICES=1 PYTHONPATH=src python scripts/render_samples.py \
    --ckpt ckpt_wallcycle_kr_v2.pt --vocab data/staging/tokens_parsed_apt/vocab.json \
    --n 8 --constrained --out /tmp/v2_samples --max-new 650
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, "src")
import torch  # noqa: E402

from plan2graph import cadrender, wallcycle_codec as wc  # noqa: E402
from plan2graph.generators.wall_cycle import WallCycleLM, make_constraint_mask  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--vocab", required=True)
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--out", default="/tmp/v2_samples")
    ap.add_argument("--max-new", type=int, default=650)
    ap.add_argument("--constrained", action="store_true")
    ap.add_argument("--orthogonal", action="store_true", help="직각 강제(대각선 차단)")
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
    pre = [bos, vocab["meta"] + 0, vocab["meta"] + len(wc.COUNTRIES) + 0,
           vocab["meta"] + len(wc.COUNTRIES) + len(wc.HOUSING) + 0,
           vocab["scope"] + 0, vocab["units"] + 1]
    os.makedirs(args.out, exist_ok=True)

    saved = 0
    ch = 4                                            # OOM 회피 배치 분할
    for i in range(0, args.n, ch):
        k = min(ch, args.n - i)
        prefix = torch.tensor([pre] * k, device=dev)
        out = model.generate(prefix, max_new=args.max_new, eos=eos,
                             temperature=1.0, top_k=40, mask_fn=mask_fn)
        for row in out.tolist():
            row = row[:row.index(eos) + 1] if eos in row else row
            try:
                g = wc.canon_to_graph(wc.decode(row, vocab))
                geom = cadrender.from_geomgraph(g)
                geom = cadrender.autocorrect(geom)
                png = cadrender.render_png(geom)
                open(os.path.join(args.out, f"gen_{saved}.png"), "wb").write(png)
                print(f"gen_{saved}: rooms={len(g['rooms'])} doors={len(g['doors'])} "
                      f"windows={len(g['windows'])}", flush=True)
                saved += 1
            except Exception as e:  # noqa: BLE001
                print(f"render err: {type(e).__name__}: {e}", flush=True)
    print(f"saved {saved} → {args.out}", flush=True)


if __name__ == "__main__":
    main()
