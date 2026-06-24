"""wall-cycle LM 학습 + collapse 진단 (ADR-0012 §6).

미니셋(T1~T4) 또는 메인 토큰셋으로 학습 → 생성 샘플의 유효율·다양성으로 붕괴 진단.
판독: T1 붕괴=모델/표현 · T1 OK·T3 실패=한국 위상 · T4=parser noise robustness.

진단 지표:
  valid_rate   = 디코드된 그래프가 방≥2·room cycle 닫힘·문 on-wall 만족 비율
  uniq_rate    = 고유 생성 시퀀스 / 샘플 수 (모드붕괴=낮음)
  mean_rooms   = 생성 방 수 평균 (중앙붕괴=극단)

사용(서버 115, GPU1):
  CUDA_VISIBLE_DEVICES=1 PYTHONPATH=src python scripts/train_wall_cycle.py \
    --data data/staging/miniset/T1_simple.jsonl --vocab data/staging/miniset/vocab.json \
    --epochs 200 --d-model 256 --n-layer 6
"""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, "src")
import torch  # noqa: E402
from torch.utils.data import DataLoader, Dataset  # noqa: E402

from plan2graph import wallcycle_codec as wc  # noqa: E402
from plan2graph.generators.wall_cycle import (  # noqa: E402
    WallCycleLM, causal_lm_loss, make_constraint_mask)


class TokDS(Dataset):
    def __init__(self, path, max_len):
        self.recs = []
        for ln in open(path, encoding="utf-8"):
            r = json.loads(ln)
            if len(r["tokens"]) <= max_len:
                self.recs.append(r["tokens"])

    def __len__(self):
        return len(self.recs)

    def __getitem__(self, i):
        return self.recs[i]


def collate(batch, pad):
    m = max(len(t) for t in batch)
    x = torch.full((len(batch), m), pad, dtype=torch.long)
    for i, t in enumerate(batch):
        x[i, :len(t)] = torch.tensor(t, dtype=torch.long)
    return x


def diagnose(model, vocab, device, n=64, meta_prefix=None, mask_fn=None):
    """META prefix로 생성 → 디코드 → 유효율/다양성/방수. mask_fn=constrained decoding."""
    model.eval()
    bos, eos = wc.V.BOS, wc.V.EOS
    pre = meta_prefix or [bos,
                          vocab["meta"] + 0,                       # KR
                          vocab["meta"] + len(wc.COUNTRIES) + 0,   # apartment
                          vocab["meta"] + len(wc.COUNTRIES) + len(wc.HOUSING) + 0,  # korean_13
                          vocab["scope"] + 0,                      # unit
                          vocab["units"] + 1]                      # 1세대
    prefix = torch.tensor([pre] * n, device=device)
    out = model.generate(prefix, max_new=model.max_len - len(pre), eos=eos,
                         temperature=1.0, top_k=40, mask_fn=mask_fn)
    seqs, valid, rooms = [], 0, []
    for row in out.tolist():
        if eos in row:
            row = row[:row.index(eos) + 1]
        seqs.append(tuple(row))
        try:
            canon = wc.decode(row, vocab)
            g = wc.canon_to_graph(canon)
            nr = len(g["rooms"])
            rooms.append(nr)
            # 유효: 방≥2 + 모든 방 cycle≥3 + 문이 벽 위(on_wall 존재)
            ok = nr >= 2 and all(len(rm["cycle"]) >= 3 for rm in canon.rooms)
            ok = ok and all(d.get("on_wall") for d in g["doors"])
            if ok:
                valid += 1
        except Exception:  # noqa: BLE001
            rooms.append(0)
    uniq = len(set(seqs))
    return {"valid_rate": round(valid / n, 3), "uniq_rate": round(uniq / n, 3),
            "mean_rooms": round(sum(rooms) / max(1, len(rooms)), 1),
            "max_rooms": max(rooms) if rooms else 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--vocab", required=True)
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--d-model", type=int, default=256)
    ap.add_argument("--n-layer", type=int, default=6)
    ap.add_argument("--n-head", type=int, default=8)
    ap.add_argument("--max-len", type=int, default=1152)
    ap.add_argument("--diag-every", type=int, default=25)
    ap.add_argument("--ckpt-every", type=int, default=10, help="N epoch마다 체크포인트 저장")
    ap.add_argument("--constrained", action="store_true",
                    help="생성 시 constrained decoding(ADR-0012 §3) 적용")
    ap.add_argument("--orthogonal", action="store_true", help="직각 강제(대각선 차단)")
    ap.add_argument("--out", default="")
    ap.add_argument("--resume", default="", help="사전학습 체크포인트 로드 (FT용)")
    ap.add_argument("--dim-ff", type=int, default=0, help="0=기본(4*d). 80M 재현은 1408")
    ap.add_argument("--grad-ckpt", action="store_true", help="gradient checkpointing(80M 필수)")
    ap.add_argument("--amp", action="store_true", help="혼합정밀(fp16) 학습")
    ap.add_argument("--country", type=int, default=0,
                    help="diagnose 생성 prefix country (0=KR 1=CN 2=EU). RPLAN 학습=1 (데이터 분포와 일치)")
    args = ap.parse_args()

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    vocab = json.load(open(args.vocab, encoding="utf-8"))
    pad = wc.V.EOS                                   # pad=EOS, loss에서 무시
    ds = TokDS(args.data, args.max_len)
    dl = DataLoader(ds, batch_size=args.batch, shuffle=True,
                    collate_fn=lambda b: collate(b, pad))
    print(f"[data] {len(ds)} seqs, vocab={vocab['size']}, device={dev}")

    # Load/resume from checkpoint
    start_ep = 1
    ckpt = None
    dim_ff = None
    if args.resume:  # Resume only if explicitly specified
        ckpt_path = args.resume if args.resume else args.out
        try:
            ckpt = torch.load(ckpt_path, map_location=dev)
            ckpt_args = ckpt.get("args", {})
            # Use checkpoint config for model
            args.d_model = ckpt_args.get("d_model", args.d_model)
            args.n_layer = ckpt_args.get("n_layer", args.n_layer)
            args.n_head = ckpt_args.get("n_head", args.n_head)
            args.max_len = ckpt_args.get("max_len", args.max_len)
            # Calculate dim_ff from checkpoint mlp.w1.weight shape
            mlp_w1_shape = ckpt["model"]["blocks.0.mlp.w1.weight"].shape
            dim_ff = mlp_w1_shape[0]
            start_ep = ckpt.get("epoch", 0) + 1 if args.resume else 1
            print(f"[CKPT-DEBUG] dim_ff={dim_ff} from mlp_w1_shape={mlp_w1_shape}")
            print(f"[ckpt] {ckpt_path} → d={args.d_model} L={args.n_layer} H={args.n_head} dim_ff={dim_ff}, ep{start_ep}부터")
        except Exception as e:
            print(f"[ERR] {type(e).__name__}: {e}")
            print(f"[new] 처음부터 학습 (d={args.d_model} L={args.n_layer} H={args.n_head})")
            ckpt = None

    if dim_ff is None and args.dim_ff > 0:
        dim_ff = args.dim_ff
    model = WallCycleLM(vocab["size"], d_model=args.d_model, n_layer=args.n_layer,
                        n_head=args.n_head, max_len=args.max_len, dim_ff=dim_ff,
                        grad_ckpt=args.grad_ckpt).to(dev)
    if ckpt:
        model.load_state_dict(ckpt["model"])
    nparam = sum(p.numel() for p in model.parameters())
    print(f"[model] {nparam/1e6:.1f}M params, d={args.d_model} L={args.n_layer} | grad_ckpt={args.grad_ckpt} amp={args.amp} dim_ff={dim_ff}")
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    if ckpt and isinstance(ckpt, dict) and "opt" in ckpt:
        try:
            opt.load_state_dict(ckpt["opt"]); print("[init] 옵티마이저 상태 복원")
        except Exception as e:
            print(f"[warn] opt 복원 실패: {e}")
    scaler = torch.cuda.amp.GradScaler(enabled=args.amp)
    mask_fn = make_constraint_mask(vocab, orthogonal=args.orthogonal) if args.constrained else None
    if mask_fn:
        print("[constrained] decoding 마스크 ON (ADR-0012 §3)")

    print(f"[DEBUG] ep{start_ep}~{args.epochs} 학습 루프 시작")
    for ep in range(start_ep, args.epochs + 1):
        model.train()
        tot = 0.0
        for x in dl:
            x = x.to(dev)
            opt.zero_grad()
            with torch.autocast("cuda", dtype=torch.float16, enabled=args.amp):
                logits = model(x)
                loss = causal_lm_loss(logits, x, ignore_index=-100)
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(opt); scaler.update()
            tot += loss.item() * x.size(0)
        if ep % args.diag_every == 0 or ep == args.epochs:
            dpre = [wc.V.BOS, vocab["meta"] + args.country,
                    vocab["meta"] + len(wc.COUNTRIES) + 0,
                    vocab["meta"] + len(wc.COUNTRIES) + len(wc.HOUSING) + 0,
                    vocab["scope"] + 0, vocab["units"] + 1]   # 데이터 분포 country로 진단(RPLAN=CN)
            d = diagnose(model, vocab, dev, meta_prefix=dpre, mask_fn=mask_fn)
            print(f"ep{ep:4d} loss {tot/len(ds):.4f} | valid {d['valid_rate']} "
                  f"uniq {d['uniq_rate']} rooms~{d['mean_rooms']}(max{d['max_rooms']})",
                  flush=True)
        if args.out and (ep % args.ckpt_every == 0 or ep == args.epochs):
            ep_path = (args.out[:-3] if args.out.endswith(".pt") else args.out) + f"_ep{ep}.pt"
            torch.save({"model": model.state_dict(), "opt": opt.state_dict(), "args": vars(args), "epoch": ep}, ep_path)
            print(f"  [ckpt] ep{ep} → {ep_path}", flush=True)


if __name__ == "__main__":
    main()
