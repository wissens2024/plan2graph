"""Task 1-5 — 무결성/법규 규칙 엔진 (1차: networkx 순수 파이썬).

픽셀만으로 검증 가능한 위상 무결성을 1차 필수로 구현한다(scale 불요):
  R1 고립 공간 없음        — exterior 제외 그래프가 단일 연결요소.
  R2 문 없는 방 없음        — 모든 공간 노드 degree ≥ 1.
  R3 현관에서 전 공간 도달   — 현관(또는 exterior)에서 모든 공간 reachable.
  R4 현관 존재             — 진입점이 최소 1개.
  R5 미해소 문 없음         — build_graph가 못 푼 문 = 0 (그래프 신뢰도).

면적 의존 법규(침실 최소면적·채광·피난거리)는 scale 확보 후 → check_legal() 스텁.

출력: validation_report dict (passed, violations[]). 위반은 근거(노드/사유) 포함.
"""
from __future__ import annotations

import sys
from pathlib import Path

import networkx as nx

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph.topology import EXTERIOR  # noqa: E402


def _space_nodes(G: nx.Graph) -> list:
    return [n for n in G.nodes if n != EXTERIOR]


def _entrances(G: nx.Graph) -> list:
    return [n for n, d in G.nodes(data=True)
            if n != EXTERIOR and d.get("is_entrance")]


def check_integrity(G: nx.Graph) -> dict:
    """위상 무결성 검사. 픽셀만으로 동작."""
    violations: list[dict] = []
    spaces = _space_nodes(G)
    sub = G.subgraph(spaces)  # exterior 제외(외부 경유 연결은 진짜 연결 아님)

    # R2 문 없는 방
    for n in spaces:
        if sub.degree(n) == 0:
            violations.append({
                "rule": "R2_doorless_room", "node": n,
                "type": G.nodes[n].get("type"),
                "msg": "공간이 어떤 문과도 연결되지 않음(고립)",
            })

    # R1 고립 공간(연결요소 > 1)
    if spaces:
        comps = list(nx.connected_components(sub))
        if len(comps) > 1:
            # 가장 큰 덩어리 외 나머지를 고립으로 보고
            comps.sort(key=len, reverse=True)
            for comp in comps[1:]:
                violations.append({
                    "rule": "R1_isolated_component",
                    "nodes": sorted(comp, key=str),
                    "types": [G.nodes[n].get("type") for n in comp],
                    "msg": f"본체와 분리된 공간 덩어리({len(comp)}개)",
                })

    # R4 현관 존재
    entrances = _entrances(G)
    if not entrances:
        violations.append({
            "rule": "R4_no_entrance",
            "msg": "현관(진입점) 공간이 없음",
        })

    # R3 현관에서 전 공간 도달 (exterior 제외 공간 서브그래프로 reachability)
    if entrances and spaces:
        reach: set = set()
        for e in entrances:
            reach |= nx.node_connected_component(sub, e)
        unreached = [n for n in spaces if n not in reach]
        if unreached:
            violations.append({
                "rule": "R3_unreachable_from_entrance",
                "nodes": sorted(unreached, key=str),
                "types": [G.nodes[n].get("type") for n in unreached],
                "msg": f"현관에서 도달 불가한 공간 {len(unreached)}개",
            })

    # R5 미해소 문
    n_unres = G.graph.get("n_unresolved_doors", 0)
    if n_unres:
        violations.append({
            "rule": "R5_unresolved_doors", "count": n_unres,
            "msg": f"양쪽 공간을 못 찾은 문 {n_unres}개(추론 신뢰도 저하)",
        })

    return {
        "passed": len(violations) == 0,
        "n_violations": len(violations),
        "violations": violations,
        "checked": ["R1", "R2", "R3", "R4", "R5"],
    }


def check_legal(G: nx.Graph) -> dict:
    """법규 검사 — rules_legal 엔진(국가법령정보센터 API 근거 규칙 DB)에 위임.
    창 기반 규칙(채광·환기)은 scale 불요, 면적 규칙은 scale 확보분에만 적용.
    """
    from plan2graph.rules_legal import check_legal as _legal
    return _legal(G)


def validate(G: nx.Graph) -> dict:
    integ = check_integrity(G)
    legal = check_legal(G)
    return {
        "graph_id": G.graph.get("graph_id", ""),
        "passed": integ["passed"],
        "integrity": integ,
        "legal": legal,
    }


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import json
    from plan2graph.coco import load_coco
    from plan2graph.geometry import assemble_drawing
    from plan2graph.topology import build_graph
    docs = [load_coco(p) for p in sys.argv[1:]]
    dr = assemble_drawing(docs)
    G = build_graph(dr, graph_id="demo")
    rep = validate(G)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
