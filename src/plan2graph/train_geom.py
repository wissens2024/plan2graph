"""기하 생성 모델 (Graph-to-Geometry) — 제약(방집합·역할·면적·창·인접) → 외곽 내 방 박스 배치.

train_gen(위상=링크예측)과 별개. 출력이 **좌표**라 별도 모델·학습.
2단계: 글로벌 사전학습(--pretrain) → g0 파인튜닝(--finetune). train_gen과 동일 패턴.
v1: TransformerEncoder(방 토큰) → 방별 정규화 박스[cx,cy,w,h] 회귀(+인접 평균 보조손실).
  실제 g0(21,613세대) 박스에서 배운다. (이쁜 도면의 토대 — 이후 개선)

사용: python -m plan2graph.train_geom --finetune g0 --epochs 30
      python -m plan2graph.train_geom --pretrain g_rplan --finetune g0
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402

# 역할 어휘(topoedit.ROLES와 동일 순서) — 모델 입력 임베딩 인덱스
ROLES = ("거실", "주방", "현관", "침실", "안방", "화장실", "욕실", "전용화장실",
         "전용욕실", "드레스룸", "파우더룸", "발코니", "실외기실", "다목적공간",
         "복도", "전실", "기타", "구조물", "실외", "엘리베이터홀", "계단실", "엘리베이터")
ROLE_IX = {r: i for i, r in enumerate(ROLES)}
PAD = len(ROLES)
MAXR = 40                      # 세대당 최대 방 수(패딩)
RUNS = ROOT / "runs"


def _unit_example(g: dict):
    """geom record → (feats[R,3], boxes[R,4] 정규화, adj[(i,j)]) 또는 None."""
    rooms = g.get("rooms", {})
    ids = [nid for nid, r in rooms.items() if r.get("polygon")]
    if not (2 <= len(ids) <= MAXR):
        return None
    xs = [p[0] for nid in ids for p in rooms[nid]["polygon"]]
    ys = [p[1] for nid in ids for p in rooms[nid]["polygon"]]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    W, H = max(maxx - minx, 1.0), max(maxy - miny, 1.0)
    amax = max((rooms[nid].get("area_px", 0) or 0) for nid in ids) or 1.0
    feats, boxes = [], []
    for nid in ids:
        r = rooms[nid]
        px = [p[0] for p in r["polygon"]]
        py = [p[1] for p in r["polygon"]]
        cx = ((min(px) + max(px)) / 2 - minx) / W
        cy = ((min(py) + max(py)) / 2 - miny) / H
        bw = (max(px) - min(px)) / W
        bh = (max(py) - min(py)) / H
        feats.append([ROLE_IX.get(r.get("role"), PAD),
                      (r.get("area_px", 0) or 0) / amax,
                      float(r.get("n_windows", 0) or 0)])
        boxes.append([cx, cy, bw, bh])
    idmap = {nid: i for i, nid in enumerate(ids)}
    adj = [(idmap[e["from"]], idmap[e["to"]]) for e in g.get("edges", [])
           if e.get("from") in idmap and e.get("to") in idmap]
    return feats, boxes, adj


def load_units(version: str):
    f = config.release_dir(version) / "geom.jsonl"
    out = []
    if not f.exists():
        return out
    for ln in f.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        ex = _unit_example(json.loads(ln))
        if ex:
            out.append(ex)
    return out


def _model(emb=32, hid=96, layers=3, heads=4):
    import torch.nn as nn

    class GeomNet(nn.Module):
        def __init__(self):
            super().__init__()
            self.role = nn.Embedding(PAD + 1, emb)
            self.inp = nn.Linear(emb + 2, hid)
            enc = nn.TransformerEncoderLayer(hid, heads, hid * 2, batch_first=True)
            self.enc = nn.TransformerEncoder(enc, layers)
            self.head = nn.Sequential(nn.Linear(hid, hid), nn.ReLU(), nn.Linear(hid, 4))

        def forward(self, role_ids, scal, mask):
            import torch
            h = self.inp(torch.cat([self.role(role_ids), scal], -1))
            h = self.enc(h, src_key_padding_mask=~mask)
            return self.head(h).sigmoid()        # [B,R,4] in [0,1]
    return GeomNet()


def _batchify(units, torch):
    B = len(units)
    role = torch.full((B, MAXR), PAD, dtype=torch.long)
    scal = torch.zeros(B, MAXR, 2)
    box = torch.zeros(B, MAXR, 4)
    mask = torch.zeros(B, MAXR, dtype=torch.bool)
    adjs = []
    for b, (feats, boxes, adj) in enumerate(units):
        for i, (f, bx) in enumerate(zip(feats, boxes)):
            role[b, i] = int(f[0]); scal[b, i, 0] = f[1]; scal[b, i, 1] = f[2]
            box[b, i] = torch.tensor(bx); mask[b, i] = True
        adjs.append(adj)
    return role, scal, box, mask, adjs


def _adj_loss(pred, adjs, torch):
    """인접한 방은 중심거리가 가까워야(보조손실) — 동선/배치 자연스럽게."""
    tot, n = 0.0, 0
    for b, adj in enumerate(adjs):
        for i, j in adj:
            tot = tot + ((pred[b, i, :2] - pred[b, j, :2]) ** 2).sum()
            n += 1
    return tot / n if n else torch.tensor(0.0)


def train(pretrain, finetune, epochs=30, lr=1e-3, batch_size=64, seed=42):
    import torch
    torch.manual_seed(seed)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    net = _model().to(dev)
    opt = torch.optim.Adam(net.parameters(), lr=lr)
    mse = torch.nn.MSELoss()

    stages = ([("pretrain", pretrain, max(1, epochs))] if pretrain else []) + \
             [("finetune", finetune, epochs)]
    last = {}
    for name, ver, ne in stages:
        units = load_units(ver)
        if not units:
            print(f"[{name}] {ver}: 데이터 없음 — 스킵")
            continue
        print(f"[{name}] {ver}: {len(units):,}세대, {ne}ep, dev={dev}")
        for ep in range(ne):
            import random
            random.Random(seed + ep).shuffle(units)
            tl, nb = 0.0, 0
            for k in range(0, len(units), batch_size):
                bu = units[k:k + batch_size]
                role, scal, box, mask, adjs = _batchify(bu, torch)
                role, scal, box, mask = [t.to(dev) for t in (role, scal, box, mask)]
                pred = net(role, scal, mask)
                loss = mse(pred[mask], box[mask]) + 0.1 * _adj_loss(pred, adjs, torch)
                opt.zero_grad(); loss.backward(); opt.step()
                tl += loss.item(); nb += 1
            if ep == 0 or ep == ne - 1 or ep % 10 == 0:
                print(f"  {name} ep{ep} loss={tl/max(nb,1):.4f}")
            last = {"stage": name, "ep": ep, "loss": tl / max(nb, 1)}

    rid = f"geom_{finetune}" + (f"_pre-{pretrain}" if pretrain else "")
    out = config.run_write_dir(rid)
    out.mkdir(parents=True, exist_ok=True)
    torch.save({"state": net.state_dict(), "roles": ROLES, "maxr": MAXR}, out / "checkpoint.pt")
    (out / "run.json").write_text(json.dumps(
        {"run_id": rid, "schema": "geometry", "pretrain": pretrain, "finetune": finetune,
         "epochs": epochs, "seed": seed, **last}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved → {out}/checkpoint.pt  ({last})")
    return rid


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pretrain", default=None)
    ap.add_argument("--finetune", default="g0")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--batch-size", type=int, default=64)
    a = ap.parse_args()
    t0 = time.time()
    train(a.pretrain, a.finetune, a.epochs, seed=a.seed, batch_size=a.batch_size)
    print(f"({(time.time()-t0)/60:.1f}min)")
