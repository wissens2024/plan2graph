"""type조건 생성기 plugin (ADR-0001 첫 plugin).

set-transformer + **house_type 임베딩**(선택적: 조건 드롭아웃으로 ANY 학습 → LM이 타입을
줄 수도/안 줄 수도). train_gen 내부 불변 — 부품(TYPES/featurize/assemble_graph)만 재사용.
arch='set-transformer-typed', 모델파일 gen_<v>_typed_seed<s>.pt, run_id에 typed → **v3/v4(set-transformer-v2)와 완전 격리**.
GPU0 권장: CUDA_VISIBLE_DEVICES=0 (v3/v4는 GPU1).

CLI: python -m plan2graph.generators.typed train --finetune v0 [--pretrain global_rplan] --seed 42
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph import experiments as exp  # noqa: E402
from plan2graph.train_gen import TYPES, TYPE_ID, VIAS, featurize, assemble_graph  # noqa: E402
from plan2graph.generators import register  # noqa: E402
from plan2graph.generators.base import Generator  # noqa: E402

ARCH = "set-transformer-typed"
MODELS_DIR = config.PROJECT_ROOT / "models"
HOUSES = ["APT", "DEH", "ROW", "ANY"]   # ANY = 조건없음(드롭아웃·미지정)
HOUSE_ID = {h: i for i, h in enumerate(HOUSES)}
ANY = HOUSE_ID["ANY"]


def _house_of(rec) -> int:
    return HOUSE_ID.get((rec.get("meta") or {}).get("house_type"), ANY)


def _build_model(emb=48, hid=96, layers=2, heads=4):
    import torch
    import torch.nn as nn

    class EdgeModelTyped(nn.Module):
        def __init__(self):
            super().__init__()
            self.emb = nn.Embedding(len(TYPES), emb)
            self.house = nn.Embedding(len(HOUSES), emb)        # ← 주거형태 조건
            enc = nn.TransformerEncoderLayer(d_model=emb, nhead=heads, dim_feedforward=hid,
                                             dropout=0.0, batch_first=True)
            self.enc = nn.TransformerEncoder(enc, num_layers=layers)
            self.edge = nn.Sequential(nn.Linear(emb * 3, hid), nn.ReLU(), nn.Linear(hid, 1))
            self.via = nn.Sequential(nn.Linear(emb * 3, hid), nn.ReLU(), nn.Linear(hid, len(VIAS)))

        def forward(self, type_ids, house_id):
            e = self.emb(type_ids)
            h = self.enc(e.unsqueeze(0)).squeeze(0)
            g = h.mean(0, keepdim=True) + self.house(house_id).reshape(1, -1)   # house 주입
            n = h.size(0)
            ii, jj = torch.triu_indices(n, n, offset=1)
            feat = torch.cat([h[ii] + h[jj], (h[ii] - h[jj]).abs(),
                              g.expand(ii.size(0), -1)], dim=1)
            return self.edge(feat).squeeze(-1), self.via(feat), ii, jj

    return EdgeModelTyped()


def train(pretrain: str | None = None, finetune: str = "v0", epochs: int = 100,
          lr: float = 1e-3, seed: int = 42, pretrain_epochs: int = None,
          batch_size: int = 64, house_drop: float = 0.3, out: Path = None):
    """house조건 링크예측 학습. 조건 드롭아웃(house_drop)으로 ANY도 학습 → 타입 유/무 모두 처리."""
    import torch
    exp.seed_everything(seed)
    pretrain_epochs = pretrain_epochs if pretrain_epochs is not None else epochs
    from plan2graph.model_baseline import _load_split
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = _build_model().to(dev)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    bce, ce = torch.nn.BCEWithLogitsLoss(), torch.nn.CrossEntropyLoss()
    rng = random.Random(seed)

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
            data.append((tids, _house_of(rec), pi, pj, ys, vs))
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
            hh = torch.empty(B, dtype=torch.long, device=dev)
            for b, d in enumerate(batch):
                tt[b, :lens[b]] = torch.tensor(d[0], device=dev)
                pad[b, :lens[b]] = False
                hh[b] = ANY if rng.random() < house_drop else d[1]   # 조건 드롭아웃
            e = model.emb(tt)
            h = model.enc(e, src_key_padding_mask=pad)
            valid = (~pad).unsqueeze(-1).float()
            g = (h * valid).sum(1) / valid.sum(1) + model.house(hh)   # [B,emb] + house
            bidx, iidx, jidx, ys, vs = [], [], [], [], []
            for b, d in enumerate(batch):
                _, _, pi, pj, py, pv = d
                bidx += [b] * len(pi); iidx += pi; jidx += pj; ys += py; vs += pv
            bidx = torch.tensor(bidx, device=dev)
            iidx = torch.tensor(iidx, device=dev); jidx = torch.tensor(jidx, device=dev)
            hi = h[bidx, iidx]; hj = h[bidx, jidx]
            feat = torch.cat([hi + hj, (hi - hj).abs(), g[bidx]], dim=1)
            elog = model.edge(feat).squeeze(-1); vlog = model.via(feat)
            y = torch.tensor(ys, device=dev); via_y = torch.tensor(vs, device=dev, dtype=torch.long)
            loss = bce(elog, y)
            m = via_y >= 0
            if m.any():
                loss = loss + ce(vlog[m], via_y[m])
            opt.zero_grad(); loss.backward(); opt.step()
            tot += float(loss); nb += 1
        return tot / max(nb, 1)

    stages = ([("pretrain", _load_split(pretrain, "train"))] if pretrain else []) \
        + [("finetune", _load_split(finetune, "train"))]
    loss_curve = {}
    for name, recs in stages:
        n_ep = pretrain_epochs if name == "pretrain" else epochs
        data = _prep(recs)
        print(f"[{name}] {len(data)} graphs, {n_ep}ep, house_drop={house_drop}")
        loss_curve[name] = []
        for ep in range(n_ep):
            ls = epoch_pass(data)
            if ep % 5 == 0 or ep == n_ep - 1:
                print(f"  ep{ep} loss={ls:.4f}"); loss_curve[name].append([ep, round(ls, 4)])

    from collections import Counter
    wt, wh = Counter(), Counter()
    for rec in stages[-1][1]:
        for nd in rec["layout"]["nodes"]:
            if isinstance(nd["id"], int):
                wt[nd["type"]] += 1
                if nd.get("n_windows", 0) >= 1:
                    wh[nd["type"]] += 1
    p_window = {t: wh[t] / wt[t] for t in wt if wt[t]}

    run_id = exp.make_run_id("neural", finetune, pretrain, seed, ARCH)
    condition = {"task": "generator", "generator": "neural", "arch": ARCH,
                 "data_version": finetune, "pretrain": pretrain, "finetune": finetune,
                 "epochs": epochs, "pretrain_epochs": (pretrain_epochs if pretrain else None),
                 "lr": lr, "seed": seed, "batch_size": batch_size,
                 "house_drop": house_drop, "houses": HOUSES}
    payload = {"state": model.state_dict(), "types": TYPES, "vias": VIAS, "houses": HOUSES,
               "p_window": p_window, "run_id": run_id, "condition": condition}
    run_dir = exp.start_run(run_id)
    torch.save(payload, run_dir / "checkpoint.pt")
    exp.write_meta(run_dir, {"run_id": run_id, "kind": "train", "condition": condition,
                             "loss_curve": loss_curve, "data": exp.data_provenance(finetune),
                             "checkpoint": "checkpoint.pt"})
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    seeded = MODELS_DIR / f"gen_{finetune}_typed_seed{seed}.pt"   # v3/v4와 다른 파일명
    torch.save(payload, seeded)
    out = out or (MODELS_DIR / f"gen_{finetune}_typed.pt")
    torch.save(payload, out)
    print(f"저장(시드): {seeded}\n실험보존: {run_dir}  (run_id={run_id})")
    return seeded


@register(ARCH)
class TypedSetTransformerGen(Generator):
    """house조건 set-transformer. generate(program, rng, house_type='APT'|'DEH'|'ROW'|None)."""
    def __init__(self, payload):
        import torch
        self.torch = torch
        self.model = _build_model()
        self.model.load_state_dict(payload["state"])
        self.model.eval()
        self.p_window = payload.get("p_window", {})
        self.run_id = payload.get("run_id")
        self.condition = payload.get("condition", {})

    @classmethod
    def from_checkpoint(cls, path) -> "TypedSetTransformerGen":
        import torch
        return cls(torch.load(str(path), map_location="cpu", weights_only=False))

    def generate(self, program: dict, rng, house_type: str | None = None,
                 thresh: float = 0.5, sample: bool = True, temperature: float = 1.0):
        torch = self.torch
        ptypes = []
        for t, c in program.items():
            ptypes += [t] * int(c)
        tids = [TYPE_ID.get(t, TYPE_ID.get("기타")) for t in ptypes]
        hid = HOUSE_ID.get(house_type, ANY)
        with torch.no_grad():
            elog, vlog, ii, jj = self.model(torch.tensor(tids), torch.tensor(hid))
            ep = torch.sigmoid(elog / temperature).tolist()
            vp = vlog.argmax(1).tolist()
        edge_prob = {(int(ii[k]), int(jj[k])): ep[k] for k in range(len(ep))}
        via_pred = {(int(ii[k]), int(jj[k])): vp[k] for k in range(len(vp))}
        return assemble_graph(tids, edge_prob, via_pred, ptypes, rng, thresh,
                              sample=sample, p_window=self.p_window)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="train", choices=["train"])
    ap.add_argument("--pretrain", default=None)
    ap.add_argument("--finetune", default="v0")
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--pretrain-epochs", type=int, default=None)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--house-drop", type=float, default=0.3)
    a = ap.parse_args()
    train(a.pretrain, a.finetune, a.epochs, seed=a.seed, pretrain_epochs=a.pretrain_epochs,
          batch_size=a.batch_size, house_drop=a.house_drop)
