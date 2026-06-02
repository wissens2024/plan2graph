"""P3-b 신경망 생성기 — Relation-Aware 엣지 모델 (글로벌 사전학습→한국형 파인튜닝).

제약그래프(program=방 타입 집합) 조건으로 배치그래프(엣지·via) 생성.
- 노드=방 타입 임베딩 + 전역 program 맥락 → 각 방쌍의 P(엣지)·P(via) 예측(관계 인식).
- 학습=실그래프 링크예측(positive 실엣지 vs negative 비엣지) + via 분류.
- 생성=전 쌍 점수 → 샘플 + 연결성·현관 보장(baseline과 동일 조립) → nx.Graph.
- 전이학습: --pretrain global 학습 후 --finetune v0/v2 한국형 이어학습.

torch는 서버(GPU)에서. 조립·featurize는 노트북서 mock으로 검증(--self-test).
gen_loop가 baseline 대신 generate(model,program,rng)로 그대로 사용.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import networkx as nx

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph.topology import EXTERIOR  # noqa: E402

TYPES = list(config.SPACE_CLASSES)
TYPE_ID = {t.replace("공간_", ""): i for i, t in enumerate(TYPES)}
VIAS = ["door", "open", "balcony"]
MODELS_DIR = ROOT / "models"


# ── 데이터: 레코드 → (노드 타입 id, 양성 엣지) ──
def featurize(record: dict):
    nt = {n["id"]: n["type"] for n in record["layout"]["nodes"]
          if isinstance(n["id"], int)}
    ids = {n: TYPE_ID.get(t, TYPE_ID.get("기타")) for n, t in nt.items()}
    order = sorted(ids)
    idx = {n: k for k, n in enumerate(order)}
    type_ids = [ids[n] for n in order]
    pos = {}
    for e in record["layout"]["edges"]:
        if e["via"] in VIAS and isinstance(e["source"], int) and isinstance(e["target"], int):
            if e["source"] in idx and e["target"] in idx:
                a, b = sorted((idx[e["source"]], idx[e["target"]]))
                pos[(a, b)] = VIAS.index(e["via"])
    return type_ids, pos


def program_type_ids(program: dict) -> list[int]:
    ids = []
    for t, c in program.items():
        for _ in range(int(c)):
            ids.append(TYPE_ID.get(t, TYPE_ID.get("기타")))
    return ids


# ── 조립: 쌍 점수 → 그래프(연결성·현관 보장) — torch 불요, 검증 대상 ──
def assemble_graph(type_ids: list[int], edge_prob, via_pred, program_types: list[str],
                   rng: random.Random, thresh: float = 0.5) -> nx.Graph:
    """edge_prob[(i,j)]∈[0,1], via_pred[(i,j)]∈{0,1,2} → nx.Graph."""
    inv = {v: k for k, v in TYPE_ID.items()}
    G = nx.Graph()
    for i, tid in enumerate(type_ids):
        t = program_types[i] if i < len(program_types) else inv.get(tid, "기타")
        G.add_node(i, type=t, hierarchy=config.HIERARCHY.get("공간_" + t),
                   is_entrance=(t == "현관"), n_windows=0, centroid=None)
    n = len(type_ids)
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    for (i, j) in pairs:
        if edge_prob.get((i, j), 0.0) >= thresh:
            G.add_edge(i, j, via=VIAS[via_pred.get((i, j), 1)], door_type=None)
    # 연결성 보장: 컴포넌트를 최고확률 엣지로 잇기
    comps = [list(c) for c in nx.connected_components(G)] if n else []
    while len(comps) > 1:
        c0 = comps[0]; best, bp = None, -1.0
        for k in range(1, len(comps)):
            for a in c0:
                for b in comps[k]:
                    pr = edge_prob.get(tuple(sorted((a, b))), 0.0) + 1e-3
                    if pr > bp:
                        best, bp = (a, b), pr
        G.add_edge(*best, via=VIAS[via_pred.get(tuple(sorted(best)), 1)], door_type=None)
        comps = [list(c) for c in nx.connected_components(G)]
    for i in range(n):              # 현관→외부
        if G.nodes[i]["is_entrance"]:
            G.add_edge(i, EXTERIOR, via="entrance", door_type=None)
    if G.has_node(EXTERIOR):
        G.nodes[EXTERIOR].update(type="exterior", hierarchy=None, is_entrance=False)
    G.graph["graph_id"] = "gen_nn"
    return G


# ── torch 모델 (서버 학습) ──
def _build_model(emb=32, hid=64):
    import torch
    import torch.nn as nn

    class EdgeModel(nn.Module):
        def __init__(self):
            super().__init__()
            self.emb = nn.Embedding(len(TYPES), emb)
            self.edge = nn.Sequential(nn.Linear(emb * 3, hid), nn.ReLU(),
                                      nn.Linear(hid, 1))
            self.via = nn.Sequential(nn.Linear(emb * 3, hid), nn.ReLU(),
                                     nn.Linear(hid, len(VIAS)))

        def forward(self, type_ids):
            e = self.emb(type_ids)                  # [N,emb]
            g = e.mean(0, keepdim=True)             # 전역 program 맥락
            n = e.size(0)
            ii, jj = torch.triu_indices(n, n, offset=1)
            feat = torch.cat([e[ii] + e[jj], (e[ii] - e[jj]).abs(),
                              g.expand(ii.size(0), -1)], dim=1)
            return self.edge(feat).squeeze(-1), self.via(feat), ii, jj

    return EdgeModel()


def train(pretrain: str | None, finetune: str, epochs: int = 30, lr: float = 1e-3,
          neg_ratio: int = 3, out: Path = None):
    """링크예측 학습(+via). pretrain(글로벌)→finetune(한국형) 이어학습."""
    import torch
    from plan2graph.model_baseline import _load_split
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = _build_model().to(dev)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    bce = torch.nn.BCEWithLogitsLoss()
    ce = torch.nn.CrossEntropyLoss()

    def epoch_pass(records, train=True):
        tot = 0.0
        for rec in records:
            tids, pos = featurize(rec)
            if len(tids) < 2:
                continue
            t = torch.tensor(tids, device=dev)
            elog, vlog, ii, jj = model(t)
            y = torch.zeros(ii.size(0), device=dev)
            via_y = torch.full((ii.size(0),), -1, device=dev, dtype=torch.long)
            for k in range(ii.size(0)):
                key = (int(ii[k]), int(jj[k]))
                if key in pos:
                    y[k] = 1.0; via_y[k] = pos[key]
            loss = bce(elog, y)
            m = via_y >= 0
            if m.any():
                loss = loss + ce(vlog[m], via_y[m])
            if train:
                opt.zero_grad(); loss.backward(); opt.step()
            tot += float(loss)
        return tot / max(len(records), 1)

    stages = ([("pretrain", _load_split(pretrain, "train"))] if pretrain else []) \
        + [("finetune", _load_split(finetune, "train"))]
    for name, recs in stages:
        print(f"[{name}] {len(recs)} graphs")
        for ep in range(epochs):
            ls = epoch_pass(recs, True)
            if ep % 5 == 0 or ep == epochs - 1:
                print(f"  ep{ep} loss={ls:.4f}")
    out = out or (MODELS_DIR / f"gen_{finetune}.pt")
    out.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"state": model.state_dict(), "types": TYPES, "vias": VIAS}, out)
    print("저장:", out)
    return out


class NeuralGenerator:
    """학습된 모델 로더 + generate(program,rng) (gen_loop 호환)."""
    def __init__(self, checkpoint: str):
        import torch
        self.torch = torch
        self.model = _build_model()
        self.model.load_state_dict(torch.load(checkpoint, map_location="cpu")["state"])
        self.model.eval()

    def generate(self, program: dict, rng: random.Random, thresh: float = 0.5) -> nx.Graph:
        torch = self.torch
        ptypes = []
        for t, c in program.items():
            ptypes += [t] * int(c)
        tids = [TYPE_ID.get(t, TYPE_ID.get("기타")) for t in ptypes]
        with torch.no_grad():
            elog, vlog, ii, jj = self.model(torch.tensor(tids))
            ep = torch.sigmoid(elog).tolist()
            vp = vlog.argmax(1).tolist()
        edge_prob = {(int(ii[k]), int(jj[k])): ep[k] for k in range(len(ep))}
        via_pred = {(int(ii[k]), int(jj[k])): vp[k] for k in range(len(vp))}
        return assemble_graph(tids, edge_prob, via_pred, ptypes, rng, thresh)


def _self_test() -> bool:
    """torch 없이 조립 검증: mock 점수 → 연결성·현관 보장 그래프."""
    from plan2graph.rules import check_integrity
    ptypes = ["현관", "거실", "침실", "침실", "주방", "화장실"]
    tids = [TYPE_ID[t] for t in ptypes]
    rng = random.Random(0)
    n = len(tids)
    # mock: 거실 허브 위주 확률
    ep, vp = {}, {}
    for i in range(n):
        for j in range(i + 1, n):
            hub = "거실" in (ptypes[i], ptypes[j])
            ep[(i, j)] = 0.8 if hub else 0.1
            vp[(i, j)] = 0
    G = assemble_graph(tids, ep, vp, ptypes, rng)
    ok = check_integrity(G)["passed"] and G.number_of_nodes() == n + 1  # +exterior
    print(f"assemble self-test: 노드{G.number_of_nodes()} 엣지{G.number_of_edges()} "
          f"무결성{check_integrity(G)['passed']} → {'OK' if ok else 'FAIL'}")
    return ok


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="train", choices=["train"])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--pretrain", default=None, help="글로벌 사전학습 버전(global_*)")
    ap.add_argument("--finetune", default="v0", help="한국형 파인튜닝 버전")
    ap.add_argument("--epochs", type=int, default=30)
    a = ap.parse_args()
    if a.self_test:
        sys.exit(0 if _self_test() else 1)
    train(a.pretrain, a.finetune, a.epochs)
