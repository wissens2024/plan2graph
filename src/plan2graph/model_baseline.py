"""최소 생성 모델 (통계적 그래프 생성기) + 평가 하네스.

사업계획서 3단계의 워킹스켈레톤: 제약그래프(program) → 배치그래프(layout) 생성.
신경망 전 단계의 baseline — torch 불필요. v0/v1 데이터로 동일하게 학습·평가해 A/B 비교.

학습(fit): train 그래프에서 방타입쌍 인접확률 P(ta~tb) 추정.
생성(generate): program(방 구성) 주면 학습된 인접확률로 엣지 샘플 + 연결성·현관 보장.
평가(evaluate): test program으로 생성 → 위상 무결성(규제 AI 핵심)·인접 사실성·program 충실도.

비결정 요소(엣지 샘플)는 seed로 재현. 규제 검증은 rules.check_integrity 사용.
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

import networkx as nx

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph.topology import EXTERIOR  # noqa: E402
from plan2graph.rules import check_integrity  # noqa: E402

CONNECT_VIAS = ("door", "open", "balcony")


def _load_split(version: str, split: str) -> list[dict]:
    rel = config.release_dir(version)
    ids = (rel / "splits" / f"{split}.txt").read_text(encoding="utf-8").split()
    out = []
    for gid in ids:
        p = rel / "graphs" / f"{gid}.json"
        if p.exists():
            out.append(json.loads(p.read_text(encoding="utf-8")))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 학습: 방타입쌍 인접확률
# ─────────────────────────────────────────────────────────────────────────────
def _signature(program: dict, pairs) -> str:
    """그래프 구조 지문(program + 정렬된 인접 타입쌍) — 다양성/신규성 비교용."""
    prog = ",".join(f"{k}{v}" for k, v in sorted(program.items()))
    adj = ",".join(f"{a}~{b}" for a, b in sorted(pairs))
    return prog + "|" + adj


def fit(train: list[dict]) -> dict:
    pair_edges = Counter()   # (ta,tb) 정렬 → 엣지 수
    pair_cooc = Counter()    # (ta,tb) → 같은 그래프 내 인스턴스쌍 기회 수
    ent_partner = Counter()  # 현관이 붙는 상대 타입
    win_have = Counter()     # 타입별 창 보유 방 수
    win_total = Counter()    # 타입별 방 수
    programs = []
    train_sigs = set()
    for rec in train:
        nodes = {n["id"]: n["type"] for n in rec["layout"]["nodes"]
                 if isinstance(n["id"], int)}
        types = list(nodes.values())
        programs.append(dict(Counter(types)))
        for n in rec["layout"]["nodes"]:
            if isinstance(n["id"], int):
                win_total[n["type"]] += 1
                if n.get("n_windows", 0) >= 1:
                    win_have[n["type"]] += 1
        sig_pairs = []
        for e in rec["layout"]["edges"]:
            if e.get("via") in CONNECT_VIAS and isinstance(e["source"], int) \
                    and isinstance(e["target"], int):
                ta, tb = nodes.get(e["source"]), nodes.get(e["target"])
                if ta and tb:
                    sig_pairs.append(tuple(sorted((ta, tb))))
        train_sigs.add(_signature(dict(Counter(types)), set(sig_pairs)))
        # 인접 기회(같은 그래프 내 타입쌍 인스턴스 수)
        tc = Counter(types)
        tlist = list(tc)
        for i, ta in enumerate(tlist):
            for tb in tlist[i:]:
                if ta == tb:
                    pair_cooc[(ta, ta)] += tc[ta] * (tc[ta] - 1) // 2
                else:
                    pair_cooc[tuple(sorted((ta, tb)))] += tc[ta] * tc[tb]
        # 실제 엣지
        for e in rec["layout"]["edges"]:
            if e.get("via") not in CONNECT_VIAS:
                continue
            u, v = e["source"], e["target"]
            if not (isinstance(u, int) and isinstance(v, int)):
                continue
            ta, tb = nodes.get(u), nodes.get(v)
            if ta is None or tb is None:
                continue
            pair_edges[tuple(sorted((ta, tb)))] += 1
            if ta == "현관":
                ent_partner[tb] += 1
            if tb == "현관":
                ent_partner[ta] += 1
    # 확률 P(두 인스턴스가 연결) = edges/cooc (clip)
    p_adj = {}
    for pair, cooc in pair_cooc.items():
        if cooc > 0:
            p_adj[pair] = min(1.0, pair_edges.get(pair, 0) / cooc)
    p_window = {t: win_have[t] / win_total[t] for t in win_total if win_total[t]}
    return {"p_adj": p_adj, "ent_partner": dict(ent_partner),
            "p_window": p_window, "programs": programs,
            "train_sigs": train_sigs, "n_train": len(train)}


def _p(model, ta, tb):
    return model["p_adj"].get(tuple(sorted((ta, tb))), 0.0)


# ─────────────────────────────────────────────────────────────────────────────
# 생성: program → 배치 그래프 (연결성·현관 보장)
# ─────────────────────────────────────────────────────────────────────────────
def generate(model: dict, program: dict, rng: random.Random) -> nx.Graph:
    G = nx.Graph()
    nodes = []
    pw = model.get("p_window", {})
    for t, cnt in program.items():
        for _ in range(int(cnt)):
            nid = len(nodes)
            nodes.append(nid)
            # 창 보유 여부도 학습 확률로 샘플(채광 법규 평가용)
            nwin = 1 if rng.random() < pw.get(t, 0.0) else 0
            G.add_node(nid, type=t, hierarchy=config.HIERARCHY.get("공간_" + t),
                       is_entrance=(t == "현관"), centroid=None, n_windows=nwin)
    # 1) 학습 확률로 엣지 샘플
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            if rng.random() < _p(model, G.nodes[i]["type"], G.nodes[j]["type"]):
                G.add_edge(i, j, via="door")
    # 2) 연결성 보장: 컴포넌트들을 가장 그럴듯한 엣지로 연결
    comps = [list(c) for c in nx.connected_components(G)] if G.number_of_nodes() else []
    while len(comps) > 1:
        c0 = comps[0]
        best, bp = None, -1.0
        for k in range(1, len(comps)):
            for a in c0:
                for b in comps[k]:
                    pr = _p(model, G.nodes[a]["type"], G.nodes[b]["type"]) + 1e-3
                    if pr > bp:
                        best, bp = (a, b), pr
        G.add_edge(*best, via="door")
        comps = [list(c) for c in nx.connected_components(G)]
    # 3) 현관 → 외부
    for n in nodes:
        if G.nodes[n]["is_entrance"]:
            G.add_edge(n, EXTERIOR, via="entrance")
    if G.has_node(EXTERIOR):
        G.nodes[EXTERIOR].update(type="exterior", hierarchy=None, is_entrance=False)
    G.graph["graph_id"] = "gen"
    return G


# ─────────────────────────────────────────────────────────────────────────────
# 평가
# ─────────────────────────────────────────────────────────────────────────────
def _adj_dist(graphs_nodes_edges) -> Counter:
    """타입쌍 엣지 빈도 분포(정규화용 카운트)."""
    c = Counter()
    for nodes, edges in graphs_nodes_edges:
        for u, v in edges:
            ta, tb = nodes.get(u), nodes.get(v)
            if ta and tb:
                c[tuple(sorted((ta, tb)))] += 1
    return c


def evaluate(model: dict, test: list[dict], seed: int = 42) -> dict:
    from plan2graph.rules_legal import check_legal
    rng = random.Random(seed)
    n_valid = n_legal = 0
    gen_pairs = Counter()
    real_pairs = Counter()
    gen_sigs = []
    for rec in test:
        program = dict(Counter(n["type"] for n in rec["layout"]["nodes"]
                               if isinstance(n["id"], int)))
        G = generate(model, program, rng)
        n_valid += int(check_integrity(G)["passed"])
        n_legal += int(check_legal(G)["passed"])   # 채광 등 법규(창 생성 반영)
        gnodes = {n: d["type"] for n, d in G.nodes(data=True) if n != EXTERIOR}
        sp = []
        for u, v, d in G.edges(data=True):
            if d.get("via") in CONNECT_VIAS and u != EXTERIOR and v != EXTERIOR:
                pr = tuple(sorted((gnodes[u], gnodes[v])))
                gen_pairs[pr] += 1
                sp.append(pr)
        gen_sigs.append(_signature(program, set(sp)))
        rnodes = {n["id"]: n["type"] for n in rec["layout"]["nodes"]
                  if isinstance(n["id"], int)}
        for e in rec["layout"]["edges"]:
            if e.get("via") in CONNECT_VIAS and isinstance(e["source"], int) \
                    and isinstance(e["target"], int):
                real_pairs[tuple(sorted((rnodes[e["source"]], rnodes[e["target"]])))] += 1
    keys = set(gen_pairs) | set(real_pairs)
    gt = sum(gen_pairs.values()) or 1
    rt = sum(real_pairs.values()) or 1
    l1 = sum(abs(gen_pairs[k] / gt - real_pairs[k] / rt) for k in keys)
    train_sigs = model.get("train_sigs", set())
    novel = sum(1 for s in gen_sigs if s not in train_sigs)
    return {
        "n_test": len(test),
        "integrity_valid_rate": round(n_valid / max(len(test), 1), 4),
        "legal_pass_rate": round(n_legal / max(len(test), 1), 4),  # 채광 법규 통과
        "adjacency_L1_distance": round(l1, 4),     # 0=일치, 2=최악 (사실성)
        "diversity": round(len(set(gen_sigs)) / max(len(gen_sigs), 1), 4),  # 생성 구조 고유율
        "novelty": round(novel / max(len(gen_sigs), 1), 4),  # train에 없는 새 구조 비율
        "program_fidelity": 1.0,
    }


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ver = sys.argv[1] if len(sys.argv) > 1 else "v0"
    train = _load_split(ver, "train")
    test = _load_split(ver, "test")
    print(f"[{ver}] train {len(train)} / test {len(test)}  학습 중...")
    model = fit(train)
    print(f"  인접확률 타입쌍 {len(model['p_adj'])}개 학습")
    # 학습된 상위 인접확률
    top = sorted(model["p_adj"].items(), key=lambda x: -x[1])[:8]
    for (a, b), pr in top:
        print(f"    P({a}~{b}) = {pr:.2f}")
    print("\n생성·평가 중...")
    res = evaluate(model, test)
    res["version"] = ver
    res["top_adjacency"] = [{"pair": f"{a}~{b}", "p": round(p, 3)}
                            for (a, b), p in sorted(model["p_adj"].items(),
                                                    key=lambda x: -x[1])[:12]]
    out = config.release_dir(ver) / "eval.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "top_adjacency"},
                     ensure_ascii=False, indent=2))
    print(f"저장: {out}")
