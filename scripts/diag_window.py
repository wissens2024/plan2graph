"""창 부족 원인 진단 — 학습 데이터 vs 생성물의 window 비율·거주방 창보유율 비교.

데이터엔 창 많은데 생성이 적으면 = 생성기 편향(학습/디코딩 문제).
데이터도 적으면 = 토큰화/데이터 문제.

사용(서버 115):
  CUDA_VISIBLE_DEVICES=1 PYTHONPATH=src python scripts/diag_window.py \
    --data data/staging/tokens_parsed_apt/train.jsonl --vocab data/staging/tokens_parsed_apt/vocab.json \
    --ckpt ckpt_wallcycle_kr.pt --n 256
"""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, "src")
import torch  # noqa: E402

from plan2graph import wallcycle_codec as wc  # noqa: E402
from plan2graph.generators.wall_cycle import WallCycleLM, make_constraint_mask  # noqa: E402

HAB = {"거실", "침실", "안방"}   # 채광 의무 거주방


def opening_stats(seqs, vocab, label):
    V = wc.V
    nd = nw = no = 0
    hab_total = hab_win = 0
    reached_open = 0      # OPENINGS 섹션에 도달한 시퀀스(SEC_OPEN 포함)
    for toks in seqs:
        nd += toks.count(V.DOOR)
        nw += toks.count(V.WINDOW)
        no += toks.count(V.OPEN)
        if V.SEC_OPEN in toks:
            reached_open += 1
        try:
            canon = wc.decode(toks, vocab)
            g = wc.canon_to_graph(canon)
        except Exception:  # noqa: BLE001
            continue
        win_rooms = set(w.get("belongs_to") for w in g["windows"])
        for nid, r in g["rooms"].items():
            if r["role"] in HAB:
                hab_total += 1
                rid = int(nid) if str(nid).lstrip("-").isdigit() else nid
                if rid in win_rooms:
                    hab_win += 1
    n = max(1, len(seqs))
    print(f"[{label}] N={len(seqs)}")
    print(f"  OPENINGS 도달    : {reached_open}/{len(seqs)} ({100*reached_open/n:.0f}%)")
    print(f"  도면당 평균       : door {nd/n:.1f} · window {nw/n:.1f} · open {no/n:.1f}")
    print(f"  거주방 창보유율   : {hab_win}/{hab_total} "
          f"({100*hab_win/max(1,hab_total):.1f}%)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--vocab", required=True)
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--n", type=int, default=256)
    args = ap.parse_args()

    vocab = json.load(open(args.vocab, encoding="utf-8"))

    # ── 데이터 ──
    data = []
    for ln in open(args.data, encoding="utf-8"):
        data.append(json.loads(ln)["tokens"])
        if len(data) >= args.n:
            break
    opening_stats(data, vocab, "DATA(학습)")

    # ── 생성 ──
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    ck = torch.load(args.ckpt, map_location=dev)
    a = ck["args"]
    model = WallCycleLM(vocab["size"], d_model=a["d_model"], n_layer=a["n_layer"],
                        n_head=a.get("n_head", 8), max_len=a["max_len"]).to(dev)
    model.load_state_dict(ck["model"]); model.eval()
    mask_fn = make_constraint_mask(vocab)
    bos, eos = wc.V.BOS, wc.V.EOS
    pre = [bos, vocab["meta"] + 0, vocab["meta"] + len(wc.COUNTRIES) + 0,
           vocab["meta"] + len(wc.COUNTRIES) + len(wc.HOUSING) + 0,
           vocab["scope"] + 0, vocab["units"] + 1]
    prefix = torch.tensor([pre] * args.n, device=dev)
    out = model.generate(prefix, max_new=a["max_len"] - len(pre), eos=eos,
                         temperature=1.0, top_k=40, mask_fn=mask_fn)
    gen = []
    for row in out.tolist():
        gen.append(row[:row.index(eos) + 1] if eos in row else row)
    print(f"(ckpt epoch {ck.get('epoch')})")
    opening_stats(gen, vocab, "GEN(생성)")


if __name__ == "__main__":
    main()
