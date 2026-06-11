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
from plan2graph import experiments as exp  # noqa: E402
from plan2graph.topology import EXTERIOR  # noqa: E402

TYPES = list(config.SPACE_CLASSES)
TYPE_ID = {t.replace("공간_", ""): i for i, t in enumerate(TYPES)}
VIAS = ["door", "open", "balcony"]
MODELS_DIR = ROOT / "models"
ARCH = "set-transformer-v2"   # 모델 아키텍처 태그. v2=배치학습 레짐(v1=미배치, git에 보존)


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
                   rng: random.Random, thresh: float = 0.5, sample: bool = False,
                   p_window: dict = None) -> nx.Graph:
    """edge_prob[(i,j)]∈[0,1], via_pred[(i,j)]∈{0,1,2} → nx.Graph.
    sample=True: 엣지를 Bernoulli(edge_prob)로 추출(다양성·marginal 사실성↑).
                 False면 고정 임계 절단(결정론적, self-test용).
    p_window: {type:prob} 있으면 방별 창 보유를 학습확률로 샘플(채광 법규 평가용)."""
    inv = {v: k for k, v in TYPE_ID.items()}
    pw = p_window or {}
    G = nx.Graph()
    for i, tid in enumerate(type_ids):
        t = program_types[i] if i < len(program_types) else inv.get(tid, "기타")
        nwin = 1 if rng.random() < pw.get(t, 0.0) else 0
        G.add_node(i, type=t, hierarchy=config.HIERARCHY.get("공간_" + t),
                   is_entrance=(t == "현관"), n_windows=nwin, centroid=None)
    n = len(type_ids)
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    for (i, j) in pairs:
        p = edge_prob.get((i, j), 0.0)
        on = (rng.random() < p) if sample else (p >= thresh)
        if on:
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
#   Set-Transformer 인코더: 룸 집합 self-attention으로 각 방 표현을 전체 program
#   구성에 조건화(전역 평균보다 풍부한 맥락 = 메시지패싱) → 쌍별 엣지·via 예측.
def _build_model(emb=48, hid=96, layers=2, heads=4):
    import torch
    import torch.nn as nn

    class EdgeModel(nn.Module):
        def __init__(self):
            super().__init__()
            self.emb = nn.Embedding(len(TYPES), emb)
            enc_layer = nn.TransformerEncoderLayer(
                d_model=emb, nhead=heads, dim_feedforward=hid,
                dropout=0.0, batch_first=True)
            self.enc = nn.TransformerEncoder(enc_layer, num_layers=layers)
            self.edge = nn.Sequential(nn.Linear(emb * 3, hid), nn.ReLU(),
                                      nn.Linear(hid, 1))
            self.via = nn.Sequential(nn.Linear(emb * 3, hid), nn.ReLU(),
                                     nn.Linear(hid, len(VIAS)))

        def forward(self, type_ids):
            e = self.emb(type_ids)                      # [N,emb]
            h = self.enc(e.unsqueeze(0)).squeeze(0)     # 집합 self-attention → [N,emb]
            g = h.mean(0, keepdim=True)                 # 전역 program 맥락
            n = h.size(0)
            ii, jj = torch.triu_indices(n, n, offset=1)
            feat = torch.cat([h[ii] + h[jj], (h[ii] - h[jj]).abs(),
                              g.expand(ii.size(0), -1)], dim=1)
            return self.edge(feat).squeeze(-1), self.via(feat), ii, jj

    return EdgeModel()


def _load_state_compat(model, state):
    """체크포인트 state_dict를 현재 모델에 로드 — **어휘 append 백워드 호환**.

    SPACE_CLASSES가 끝에 append되어(13→16, config 주석: append-only·재정렬 금지) 임베딩이
    [old,dim]→[new,dim](old<new)로 커진 경우, 앞 old행만 복사하고 추가 행은 초기값으로 둔다.
    추가 클래스(복도·전실·파우더룸)는 검출 라벨에 없어 T 생성엔 안 쓰이므로 결과 동일.
    반환: (적응된 항목 설명 리스트). dim0만 커진 케이스만 적응, 그 외 mismatch는 strict=False가 처리.
    """
    own = model.state_dict()
    fixed, adapted = {}, []
    for k, v in state.items():
        ov = own.get(k)
        if ov is not None and ov.shape != v.shape:
            if (v.dim() == ov.dim() and v.shape[1:] == ov.shape[1:]
                    and v.shape[0] < ov.shape[0]):              # 어휘 append(앞쪽 정렬)
                nw = ov.clone()
                nw[:v.shape[0]] = v
                fixed[k] = nw
                adapted.append(f"{k} {tuple(v.shape)}→{tuple(ov.shape)} (앞 {v.shape[0]}행 복사)")
                continue
            adapted.append(f"{k} 건너뜀 {tuple(v.shape)}≠{tuple(ov.shape)}")
            continue
        fixed[k] = v
    model.load_state_dict(fixed, strict=False)
    return adapted


def train(pretrain: str | None, finetune: str, epochs: int = 30, lr: float = 1e-3,
          neg_ratio: int = 3, out: Path = None, seed: int = 42,
          pretrain_epochs: int = None, batch_size: int = 64):
    """링크예측 학습(+via). pretrain(글로벌)→finetune(한국형) 이어학습.

    epochs=finetune 에폭(noPretrain과 동일 예산으로 공정 비교), pretrain_epochs=글로벌 에폭.
    배치학습(인코더·헤드를 패딩+마스크로 한 번에) + 1회 사전계산(에폭마다 재featurize 제거)
    → 미배치 대비 수배 가속(다중시드·매트릭스 현실화). seed 고정 + runs/ 보존(재현)."""
    import torch
    exp.seed_everything(seed)
    pretrain_epochs = pretrain_epochs if pretrain_epochs is not None else epochs
    from plan2graph.model_baseline import _load_split
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = _build_model().to(dev)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    bce = torch.nn.BCEWithLogitsLoss()
    ce = torch.nn.CrossEntropyLoss()

    def _prep(records):
        """레코드 → (type_ids, pair_i, pair_j, y, via) 1회 사전계산(에폭마다 재featurize 방지).
        크기로 정렬(버킷팅) → 배치 내 패딩 낭비 최소화."""
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

    def epoch_pass(data, train=True):
        # 비슷한 크기 버킷 배치 → 인코더(패딩+key_padding_mask)·헤드를 GPU에서 한 번에.
        batches = [data[k:k + batch_size] for k in range(0, len(data), batch_size)]
        order = torch.randperm(len(batches)).tolist() if train else list(range(len(batches)))
        tot, nb = 0.0, 0
        for bi in order:
            batch = batches[bi]
            B = len(batch); lens = [len(d[0]) for d in batch]; maxN = max(lens)
            tt = torch.zeros(B, maxN, dtype=torch.long, device=dev)
            pad = torch.ones(B, maxN, dtype=torch.bool, device=dev)
            for b, d in enumerate(batch):
                tt[b, :lens[b]] = torch.tensor(d[0], device=dev)
                pad[b, :lens[b]] = False
            e = model.emb(tt)                                   # [B,maxN,emb]
            h = model.enc(e, src_key_padding_mask=pad)          # 배치 self-attention
            valid = (~pad).unsqueeze(-1).float()
            g = (h * valid).sum(1) / valid.sum(1)               # 마스킹 전역평균 [B,emb]
            bidx, iidx, jidx, ys, vs = [], [], [], [], []
            for b, d in enumerate(batch):
                _, pi, pj, py, pv = d
                bidx += [b] * len(pi); iidx += pi; jidx += pj; ys += py; vs += pv
            bidx = torch.tensor(bidx, device=dev)
            iidx = torch.tensor(iidx, device=dev); jidx = torch.tensor(jidx, device=dev)
            hi = h[bidx, iidx]; hj = h[bidx, jidx]
            feat = torch.cat([hi + hj, (hi - hj).abs(), g[bidx]], dim=1)  # [P,3emb]
            elog = model.edge(feat).squeeze(-1)
            vlog = model.via(feat)
            y = torch.tensor(ys, device=dev)
            via_y = torch.tensor(vs, device=dev, dtype=torch.long)
            loss = bce(elog, y)
            m = via_y >= 0
            if m.any():
                loss = loss + ce(vlog[m], via_y[m])
            if train:
                opt.zero_grad(); loss.backward(); opt.step()
            tot += float(loss); nb += 1
        return tot / max(nb, 1)

    stages = ([("pretrain", _load_split(pretrain, "train"))] if pretrain else []) \
        + [("finetune", _load_split(finetune, "train"))]
    loss_curve = {}
    for name, recs in stages:
        n_ep = pretrain_epochs if name == "pretrain" else epochs
        data = _prep(recs)
        print(f"[{name}] {len(data)} graphs, {n_ep} epochs, batch={batch_size}")
        loss_curve[name] = []
        for ep in range(n_ep):
            ls = epoch_pass(data, True)
            if ep % 5 == 0 or ep == n_ep - 1:
                print(f"  ep{ep} loss={ls:.4f}")
                loss_curve[name].append([ep, round(ls, 4)])
    # 창 보유 확률(타입별) — 채광 법규 평가용. finetune 학습셋에서 집계해 함께 저장.
    from collections import Counter
    wt, wh = Counter(), Counter()
    for rec in stages[-1][1]:
        for nd in rec["layout"]["nodes"]:
            if isinstance(nd["id"], int):
                wt[nd["type"]] += 1
                if nd.get("n_windows", 0) >= 1:
                    wh[nd["type"]] += 1
    p_window = {t: wh[t] / wt[t] for t in wt if wt[t]}

    # ── 실험 보존: 조건별 runs/<run_id>/ (checkpoint·meta·train.log) ──
    run_id = exp.make_run_id("neural", finetune, pretrain, seed, ARCH)
    condition = {"task": "generator", "generator": "neural", "arch": ARCH,
                 "data_version": finetune, "pretrain": pretrain,
                 "finetune": finetune, "epochs": epochs,
                 "pretrain_epochs": (pretrain_epochs if pretrain else None),
                 "lr": lr, "seed": seed, "batch_size": batch_size}
    payload = {"state": model.state_dict(), "types": TYPES, "vias": VIAS,
               "p_window": p_window, "run_id": run_id, "condition": condition}
    run_dir = exp.start_run(run_id)
    torch.save(payload, run_dir / "checkpoint.pt")            # 조건별 영구 보존
    exp.write_meta(run_dir, {"run_id": run_id, "kind": "train",
                             "condition": condition, "loss_curve": loss_curve,
                             "data": exp.data_provenance(finetune),
                             "checkpoint": "checkpoint.pt"})
    (run_dir / "train.log").write_text(
        "\n".join(f"[{s}] " + " ".join(f"ep{e}={l}" for e, l in c)
                  for s, c in loss_curve.items()) + "\n", encoding="utf-8")
    # 시드별 영구 아티팩트(시드 표기 — 다중시드 매트릭스에서 덮어쓰기 방지) +
    # 'latest' 편의 포인터(eval_gen 기본 조회 경로). 보존본은 run_dir에도 있음.
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    seeded = MODELS_DIR / f"gen_{finetune}_seed{seed}.pt"
    torch.save(payload, seeded)
    out = out or (MODELS_DIR / f"gen_{finetune}.pt")   # 최신 선택본 포인터(seed=payload.condition)
    torch.save(payload, out)
    print(f"저장(시드): {seeded}\n저장(포인터): {out}\n실험보존: {run_dir}  (run_id={run_id})")
    return seeded


class NeuralGenerator:
    """학습된 모델 로더 + generate(program,rng) (gen_loop 호환)."""
    def __init__(self, checkpoint: str):
        import torch
        self.torch = torch
        ckpt = torch.load(checkpoint, map_location="cpu", weights_only=False)
        # 모델 크기 가변 지원: condition.model 있으면 그 크기로 빌드(없으면 기본 48/96/2/4 = 구버전 호환)
        _mc = (ckpt.get("condition", {}) or {}).get("model") or {}
        self.model = _build_model(**_mc)
        # 어휘 append(예: SPACE_CLASSES 13→16) 백워드 호환 — 구버전 체크포인트 그대로 사용
        self.compat_adapted = _load_state_compat(self.model, ckpt["state"])
        if self.compat_adapted:
            print("[NeuralGenerator] 구버전 체크포인트 부분 로드:",
                  "; ".join(self.compat_adapted))
        self.model.eval()
        self.p_window = ckpt.get("p_window", {})
        self.run_id = ckpt.get("run_id")          # 평가 프로비넌스(어느 학습조건인가)
        self.condition = ckpt.get("condition", {})

    def generate(self, program: dict, rng: random.Random, thresh: float = 0.5,
                 sample: bool = True, temperature: float = 1.0) -> nx.Graph:
        """sample=True(기본): 엣지를 모델 확률에서 추출 → 다양성·marginal 사실성↑.
        temperature: 엣지확률 보정 sigmoid(logit/T). T>1 완만(엣지↑)·T<1 날카로움(엣지↓).
                     재학습 없이 marginal↔다양성 trade를 조절(val에서 튜닝).
        창 보유는 학습 p_window로 샘플(채광 평가)."""
        torch = self.torch
        ptypes = []
        for t, c in program.items():
            ptypes += [t] * int(c)
        tids = [TYPE_ID.get(t, TYPE_ID.get("기타")) for t in ptypes]
        with torch.no_grad():
            elog, vlog, ii, jj = self.model(torch.tensor(tids))
            ep = torch.sigmoid(elog / temperature).tolist()
            vp = vlog.argmax(1).tolist()
        edge_prob = {(int(ii[k]), int(jj[k])): ep[k] for k in range(len(ep))}
        via_pred = {(int(ii[k]), int(jj[k])): vp[k] for k in range(len(vp))}
        return assemble_graph(tids, edge_prob, via_pred, ptypes, rng, thresh,
                              sample=sample, p_window=self.p_window)


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
    ap.add_argument("--epochs", type=int, default=30, help="finetune 에폭")
    ap.add_argument("--pretrain-epochs", type=int, default=None, help="글로벌 사전학습 에폭")
    ap.add_argument("--seed", type=int, default=42, help="재현용 시드")
    ap.add_argument("--batch-size", type=int, default=64, help="배치 크기")
    a = ap.parse_args()
    if a.self_test:
        sys.exit(0 if _self_test() else 1)
    train(a.pretrain, a.finetune, a.epochs, seed=a.seed,
          pretrain_epochs=a.pretrain_epochs, batch_size=a.batch_size)
