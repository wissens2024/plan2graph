"""데이터셋 조합 단일학습 (combine) — **전이학습과 분리된 별도 모듈**.

'기본 모델을 버전별로 만드는' 단계: 여러 데이터셋 소스를 **합쳐 한 번에** 학습(전이학습 아님).
어떤 데이터 조합이 동결 균형 test에서 최고인지 비교(소버린 ablation).

- 전이학습(pretrain→finetune)은 `train_gen.py`, 그 평가는 `eval_gen.evaluate_version` — 본 모듈과 별개.
- 모델 구조·featurize·평가지표(_metrics)는 공유(같은 set-transformer·같은 잣대 = 공정 비교).
- 학습 루프는 단일 단계로 본 모듈이 자체 보유(train_gen의 2단계 루프와 섞지 않음).

CLI: python -m plan2graph.train_combine --combine 'v2:aihub,global_rplan:rplan' --label v3 --seed 42 --eval
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from plan2graph import experiments as exp  # noqa: E402
from plan2graph import model_baseline as mb  # noqa: E402
from plan2graph.model_baseline import _load_split  # noqa: E402
# 공유 저수준(모델 구조·특징·태그) — 학습 루프는 공유하지 않음(분리 원칙).
from plan2graph.train_gen import _build_model, featurize, TYPES, VIAS, ARCH, MODELS_DIR  # noqa: E402


def load_combine_train(combine: list) -> list:
    """combine 스펙 [(release, source), …] → 각 소스의 train 레코드를 meta.source 필터로 합침."""
    recs = []
    for rel, src in combine:
        recs += [r for r in _load_split(rel, "train")
                 if (r.get("meta") or {}).get("source") == src]
    return recs


def train(combine: list, label: str, epochs: int = 100, lr: float = 1e-3,
          seed: int = 42, batch_size: int = 64):
    """소스들을 합쳐 단일 단계로 epochs 학습. run_id·data_version = label(vN)."""
    import torch
    exp.seed_everything(seed)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = _build_model().to(dev)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    bce = torch.nn.BCEWithLogitsLoss()
    ce = torch.nn.CrossEntropyLoss()
    records = load_combine_train(combine)
    print(f"[combine] {label}: " + " + ".join(f"{s}({rel})" for rel, s in combine)
          + f"  → {len(records)} graphs", flush=True)

    def _prep(records):
        data = []
        for rec in records:
            tids, pos = featurize(rec)
            n = len(tids)
            if n < 2:
                continue
            pi, pj, ys, vs = [], [], [], []
            for i in range(n):
                for j in range(i + 1, n):
                    pi.append(i); pj.append(j)
                    if (i, j) in pos:
                        ys.append(1.0); vs.append(pos[(i, j)])
                    else:
                        ys.append(0.0); vs.append(-1)
            data.append((tids, pi, pj, ys, vs))
        data.sort(key=lambda d: len(d[0]))
        return data

    def epoch_pass(data):
        batches = [data[k:k + batch_size] for k in range(0, len(data), batch_size)]
        order = torch.randperm(len(batches)).tolist()
        tot, nb = 0.0, 0
        for bi in order:
            batch = batches[bi]
            B = len(batch); lens = [len(d[0]) for d in batch]; maxN = max(lens)
            tt = torch.zeros(B, maxN, dtype=torch.long, device=dev)
            pad = torch.ones(B, maxN, dtype=torch.bool, device=dev)
            for b, d in enumerate(batch):
                tt[b, :lens[b]] = torch.tensor(d[0], device=dev)
                pad[b, :lens[b]] = False
            e = model.emb(tt)
            h = model.enc(e, src_key_padding_mask=pad)
            valid = (~pad).unsqueeze(-1).float()
            g = (h * valid).sum(1) / valid.sum(1)
            bidx, iidx, jidx, ys, vs = [], [], [], [], []
            for b, d in enumerate(batch):
                _, pi, pj, py, pv = d
                bidx += [b] * len(pi); iidx += pi; jidx += pj; ys += py; vs += pv
            bidx = torch.tensor(bidx, device=dev)
            iidx = torch.tensor(iidx, device=dev); jidx = torch.tensor(jidx, device=dev)
            hi = h[bidx, iidx]; hj = h[bidx, jidx]
            feat = torch.cat([hi + hj, (hi - hj).abs(), g[bidx]], dim=1)
            elog = model.edge(feat).squeeze(-1); vlog = model.via(feat)
            y = torch.tensor(ys, device=dev)
            via_y = torch.tensor(vs, device=dev, dtype=torch.long)
            loss = bce(elog, y)
            m = via_y >= 0
            if m.any():
                loss = loss + ce(vlog[m], via_y[m])
            opt.zero_grad(); loss.backward(); opt.step()
            tot += float(loss); nb += 1
        return tot / max(nb, 1)

    data = _prep(records)
    print(f"  {len(data)} graphs, {epochs} epochs, batch={batch_size}", flush=True)
    loss_curve = []
    for ep in range(epochs):
        ls = epoch_pass(data)
        if ep % 5 == 0 or ep == epochs - 1:
            print(f"  ep{ep} loss={ls:.4f}", flush=True)
            loss_curve.append([ep, round(ls, 4)])

    wt, wh = Counter(), Counter()
    for rec in records:
        for nd in rec["layout"]["nodes"]:
            if isinstance(nd["id"], int):
                wt[nd["type"]] += 1
                if nd.get("n_windows", 0) >= 1:
                    wh[nd["type"]] += 1
    p_window = {t: wh[t] / wt[t] for t in wt if wt[t]}

    run_id = exp.make_run_id("neural", label, None, seed, ARCH)
    condition = {"task": "generator", "generator": "neural", "arch": ARCH,
                 "data_version": label, "pretrain": None, "finetune": label,
                 "epochs": epochs, "lr": lr, "seed": seed, "batch_size": batch_size,
                 "mode": "combine", "combine": combine}
    payload = {"state": model.state_dict(), "types": TYPES, "vias": VIAS,
               "p_window": p_window, "run_id": run_id, "condition": condition}
    run_dir = exp.start_run(run_id)
    torch.save(payload, run_dir / "checkpoint.pt")
    exp.write_meta(run_dir, {"run_id": run_id, "kind": "train", "condition": condition,
                             "loss_curve": {"combine": loss_curve},
                             "data": {"combine": combine}, "checkpoint": "checkpoint.pt"})
    (run_dir / "train.log").write_text(
        "[combine] " + " ".join(f"ep{e}={l}" for e, l in loss_curve) + "\n", encoding="utf-8")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    seeded = MODELS_DIR / f"gen_{label}_seed{seed}.pt"
    torch.save(payload, seeded)
    torch.save(payload, MODELS_DIR / f"gen_{label}.pt")
    print(f"저장(시드): {seeded}  실험보존: {run_dir}  (run_id={run_id})", flush=True)
    return seeded


def evaluate(label: str, combine: list, seed: int, temperature: float = 0.85):
    """combine 모델을 **동결 균형 AI-Hub test**(전 버전 공유)로 평가 →
    eval(off/on)·generalization(seen/unseen)·dwelling(APT/DEH/ROW) 원장 기록.
    평가지표는 eval_gen 공유(_metrics) — 잣대 동일."""
    from plan2graph.train_gen import NeuralGenerator
    from plan2graph import eval_gen
    train_recs = load_combine_train(combine)
    test = mb._load_split("v2", "test")          # 동결 균형 test(전 버전 공유)
    if not train_recs or not test:
        print(f"  [데이터 없음] {label}"); return
    tsigs = mb.fit(train_recs).get("train_sigs", set())
    ckpt = MODELS_DIR / f"gen_{label}_seed{seed}.pt"
    if not ckpt.exists():
        print(f"  [체크포인트 없음] {ckpt}"); return
    ng = NeuralGenerator(str(ckpt))
    run_id = ng.run_id or exp.make_run_id("neural", label, None, seed, ARCH)
    sha = exp.git_commit()
    for loop in (False, True):
        m = eval_gen._metrics(ng.generate, test, tsigs, loop, None)
        exp.append_index({"kind": "eval", "run_id": run_id, "generator": "neural",
                          "version": label, "pretrain": None,
                          "reg_loop": "on" if loop else "off", "git_commit": sha, **m})
    tprogs = {eval_gen._prog_sig(r) for r in train_recs}
    for sub, subset in (("seen", [r for r in test if eval_gen._prog_sig(r) in tprogs]),
                        ("unseen", [r for r in test if eval_gen._prog_sig(r) not in tprogs])):
        if not subset:
            continue
        m = eval_gen._metrics(eval_gen._neural_gen(ng, temperature), subset, tsigs, False, None)
        exp.append_index({"kind": "generalization", "run_id": run_id, "subset": sub,
                          "n": len(subset), "generator": "neural", "version": label,
                          "git_commit": sha, **m})
    groups: dict = {}
    for r in test:
        groups.setdefault((r.get("meta", {}) or {}).get("house_type") or "?", []).append(r)
    for ht, sub in groups.items():
        m = eval_gen._metrics(eval_gen._neural_gen(ng, temperature), sub, tsigs, False, None)
        exp.append_index({"kind": "dwelling", "run_id": run_id, "house_type": ht,
                          "n": len(sub), "generator": "neural", "version": label,
                          "git_commit": sha, **m})
    print(f"  [평가완료] {label} seed{seed} (run_id={run_id})", flush=True)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--combine", required=True,
                    help="'release:source,release:source' (예: 'v2:aihub,global_rplan:rplan')")
    ap.add_argument("--label", required=True, help="버전 라벨(vN)")
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--eval", action="store_true", help="학습 후 동결 test 평가까지")
    a = ap.parse_args()
    spec = [tuple(x.split(":")) for x in a.combine.split(",") if x.strip()]
    train(spec, a.label, epochs=a.epochs, seed=a.seed)
    if a.eval:
        evaluate(a.label, spec, a.seed)
