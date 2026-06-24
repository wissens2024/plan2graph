"""FID 하네스 — KorPlan-AR(RPLAN) vs 실제 RPLAN. SOTA(FMLM) 비교 기준선.

실제(test.jsonl 디코드)와 생성(ckpt)을 **동일 semantic raster**(raster_build.render)로 렌더한 뒤
clean-fid로 FID 계산. unconditional 생성 = FMLM의 unconditional FID(7.22)와 같은 설정.

⚠️ 우리 렌더링/레퍼런스라 FMLM의 정확한 프로토콜과 1:1 동일하진 않음(내부 일관 baseline).

사용:
  CUDA_VISIBLE_DEVICES=1 PYTHONPATH=src:scripts python scripts/fid_rplan.py \
    --ckpt ckpts/korplan_ar_r_fmlm80m.pt --vocab data/staging/tokens_rplan/vocab.json \
    --real data/staging/tokens_rplan/test.jsonl --n-real 4000 --n-gen 2048 --size 256
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")
import numpy as np  # noqa: E402
import torch  # noqa: E402
from PIL import Image  # noqa: E402

from plan2graph import wallcycle_codec as wc  # noqa: E402
from plan2graph.generators.wall_cycle import WallCycleLM, make_constraint_mask  # noqa: E402
from raster_build import render  # noqa: E402  (geomgraph → role-color semantic raster)


def g_from_tokens(tokens, vocab):
    g = wc.canon_to_graph(wc.decode(tokens, vocab))
    for r in g["rooms"].values():
        if r.get("polygon"):
            r["polygon"] = wc.rectify_diagonals(r["polygon"])   # 양쪽 동일 후처리
    return g


def save_img(g, path, size):
    im = render(g, size=size)
    if im is None:
        return False
    Image.fromarray(im).save(path)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--vocab", required=True)
    ap.add_argument("--real", required=True)
    ap.add_argument("--country", type=int, default=1)       # 1=CN(RPLAN)
    ap.add_argument("--n-gen", type=int, default=2048)
    ap.add_argument("--n-real", type=int, default=4000)
    ap.add_argument("--size", type=int, default=256)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--real-dir", default="/tmp/fid_real")
    ap.add_argument("--gen-dir", default="/tmp/fid_gen")
    args = ap.parse_args()

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    vocab = json.load(open(args.vocab, encoding="utf-8"))

    # ── 1. 실제 RPLAN 레퍼런스 ──
    os.makedirs(args.real_dir, exist_ok=True)
    nr = 0
    for i, ln in enumerate(open(args.real, encoding="utf-8")):
        if nr >= args.n_real:
            break
        try:
            g = g_from_tokens(json.loads(ln)["tokens"], vocab)
            if save_img(g, os.path.join(args.real_dir, f"{nr:05d}.png"), args.size):
                nr += 1
        except Exception:  # noqa: BLE001
            pass
    print(f"[real] rendered {nr} → {args.real_dir}", flush=True)

    # ── 2. 생성 ──
    ck = torch.load(args.ckpt, map_location=dev)
    a = ck["args"]
    m = WallCycleLM(vocab["size"], d_model=a["d_model"], n_layer=a["n_layer"],
                    n_head=a.get("n_head", 8), max_len=a["max_len"], dim_ff=a.get("dim_ff")).to(dev)
    m.load_state_dict(ck["model"]); m.eval()
    print(f"[gen] ckpt epoch {ck.get('epoch')} (누적 ep{30 + (ck.get('epoch') or 0)})", flush=True)
    mask = make_constraint_mask(vocab, orthogonal=True)
    C = args.country
    pre = [wc.V.BOS, vocab["meta"] + C, vocab["meta"] + len(wc.COUNTRIES) + 0,
           vocab["meta"] + len(wc.COUNTRIES) + len(wc.HOUSING) + 0,
           vocab["scope"] + 0, vocab["units"] + 1]
    os.makedirs(args.gen_dir, exist_ok=True)
    ng = 0
    while ng < args.n_gen:
        k = min(args.batch, args.n_gen - ng)
        out = m.generate(torch.tensor([pre] * k, device=dev), max_new=650, eos=wc.V.EOS,
                         temperature=1.0, top_k=40, mask_fn=mask)
        for row in out.tolist():
            row = row[:row.index(wc.V.EOS) + 1] if wc.V.EOS in row else row
            try:
                g = g_from_tokens(row, vocab)
                if save_img(g, os.path.join(args.gen_dir, f"{ng:05d}.png"), args.size):
                    ng += 1
            except Exception:  # noqa: BLE001
                pass
        print(f"[gen] {ng}/{args.n_gen}", flush=True)

    # ── 3. FID ──
    from cleanfid import fid
    score = fid.compute_fid(args.real_dir, args.gen_dir)
    print(f"=== FID(real,gen) = {score:.2f} ===  (real {nr}, gen {ng}, size {args.size}, uncond)", flush=True)


if __name__ == "__main__":
    main()
