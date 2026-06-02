"""P3-c Neuro-Symbolic 자기수정 루프 — 생성 ↔ 규제 AI 감독 (프로젝트 핵심).

사업계획서 3-c Self-Correction(방식 A 사후검증):
  제약그래프 → 생성 → 규제 AI 검증(무결성+법규) → 위반 시 국소수정/재생성 → 무결 그래프.

규제 AI = rules.check_integrity + rules_legal.check_legal(법규엔진). 위반을 보상페널티가 아니라
즉시 교정/재시도로 닫는 루프. (방식 B Constrained RL은 train_gen에서 보상에 통합.)

baseline 생성기로 노트북 시연. 신경망(train_gen) 후엔 같은 루프를 그 생성기로 사용.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import networkx as nx

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph.topology import EXTERIOR, _space_nodes  # noqa: E402
from plan2graph.rules import check_integrity  # noqa: E402
from plan2graph.rules_legal import check_legal  # noqa: E402

HABITABLE = ("거실", "침실")


def verify(G: nx.Graph) -> dict:
    """규제 AI 검증: 위상 무결성 + 법규. 위반 목록 통합."""
    integ = check_integrity(G)
    legal = check_legal(G)
    v = [{"kind": "integrity", **x} for x in integ["violations"]]
    v += [{"kind": "legal", **x} for x in legal["violations"]]
    return {"passed": len(v) == 0, "n": len(v), "violations": v,
            "integrity_ok": integ["passed"], "legal_ok": legal["passed"]}


def local_repair(G: nx.Graph, model: dict, rng: random.Random) -> list[str]:
    """규제 위반을 결정론적으로 국소 수정. 수정 내역 반환."""
    from plan2graph.model_baseline import _p
    fixes = []
    sub = G.subgraph(_space_nodes(G))
    # 1) 채광: 거실·침실에 창 없으면 창 부여(grounded correction)
    for n, d in G.nodes(data=True):
        if d.get("type") in HABITABLE and d.get("n_windows", 0) < 1:
            G.nodes[n]["n_windows"] = 1
            fixes.append(f"채광:{d['type']}#{n} 창 추가")
    # 2) 고립/문없는 방: 가장 그럴듯한 이웃과 연결(학습 인접확률)
    comps = list(nx.connected_components(sub))
    if len(comps) > 1:
        comps.sort(key=len, reverse=True)
        main = comps[0]
        for comp in comps[1:]:
            best, bp = None, -1.0
            for a in comp:
                for b in main:
                    pr = _p(model, G.nodes[a].get("type"), G.nodes[b].get("type"))
                    if pr > bp:
                        best, bp = (a, b), pr
            if best:
                G.add_edge(best[0], best[1], via="open", door_type=None)
                fixes.append(f"연결:{best[0]}-{best[1]} (고립 해소)")
            main |= comp
    return fixes


def generate_compliant(model: dict, program: dict, max_tries: int = 5,
                       repair: bool = True, seed: int = 0):
    """규제 통과 그래프 생성: 생성→검증→(수정/재생성) 루프. (G, history) 반환."""
    from plan2graph.model_baseline import generate
    rng = random.Random(seed)
    best, best_v, history = None, 1e9, []
    for attempt in range(max_tries):
        G = generate(model, program, rng)
        v0 = verify(G)
        fixes = local_repair(G, model, rng) if (repair and not v0["passed"]) else []
        v1 = verify(G)
        history.append({"attempt": attempt, "violations_before": v0["n"],
                        "fixes": fixes, "violations_after": v1["n"],
                        "passed": v1["passed"]})
        if v1["n"] < best_v:
            best, best_v = G, v1["n"]
        if v1["passed"]:
            return G, history
    return best, history


def evaluate_loop(model: dict, programs: list[dict], max_tries: int = 5) -> dict:
    """규제 루프 효과 측정: 루프 없음 vs 있음 통과율."""
    base_pass = loop_pass = 0
    tries_sum = 0
    for i, prog in enumerate(programs):
        G0, _ = generate_compliant(model, prog, max_tries=1, repair=False, seed=i)
        if verify(G0)["passed"]:
            base_pass += 1
        G1, hist = generate_compliant(model, prog, max_tries=max_tries, repair=True, seed=i)
        if verify(G1)["passed"]:
            loop_pass += 1
        tries_sum += len(hist)
    n = len(programs)
    return {"n": n,
            "compliance_no_loop": round(base_pass / n, 3),
            "compliance_with_loop": round(loop_pass / n, 3),
            "avg_attempts": round(tries_sum / n, 2)}


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import json
    from plan2graph import model_baseline as mb, text2graph
    ver = sys.argv[1] if len(sys.argv) > 1 else "v0"
    model = mb.fit(mb._load_split(ver, "train"))

    # 1) 단건 시연: 자연어 → 제약 → 생성 → 규제수정
    cg = text2graph.parse("4인 가족 84㎡ 침실3 욕실2 LDK 안방 드레스룸")
    G, hist = generate_compliant(model, cg["program"], max_tries=5, seed=1)
    print("=== 자연어→제약→생성→규제 자기수정 ===")
    print("program:", cg["program"])
    for h in hist:
        print(f"  시도{h['attempt']}: 위반 {h['violations_before']}→{h['violations_after']} "
              f"수정={h['fixes']} 통과={h['passed']}")
    print("최종 규제 통과:", verify(G)["passed"])

    # 2) 루프 효과(test programs)
    test = mb._load_split(ver, "test")[:120]
    from collections import Counter
    progs = [dict(Counter(n["type"] for n in r["layout"]["nodes"]
                          if isinstance(n["id"], int))) for r in test]
    print("\n=== 규제 루프 효과 (test 120) ===")
    print(json.dumps(evaluate_loop(model, progs), ensure_ascii=False, indent=2))
