"""법규 엔진 v2 — SWRL + 온톨로지 추론기(HermiT) 기반 심볼릭 검증.

사업계획서 3단계 "온톨로지 추론기 기반 위상 무결성 검증(Symbolic Reasoner Loop)".
규제 AI가 호출하는 '진짜' 심볼릭 엔진. v1(rules_legal, 순수 파이썬)을 대체/보완한다.

설계 (Neuro-Symbolic):
- 수치·위상 사실은 파이썬으로 grounding(면적㎡·창수·도달성) → boolean 데이터속성으로 단언.
  (DL 추론기는 산술 built-in에 약함: HermiT는 미지원, Pellet은 Java17 충돌 → grounding이 정석.)
- SWRL 규칙(산술 built-in 없음) + HermiT 추론기 → 위반 클래스를 '논리 추론'으로 도출.
- 결과 위반 개체 + 법조문 근거(rules.json)를 리포트로 반환. check_legal과 동일 형태.

규제 AI 루프(생성 도면 1장 검증)에 적합(HermiT ~1.5s/그래프). 대량 주석은 v1(빠름) 사용.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import networkx as nx
import owlready2
from owlready2 import Thing, FunctionalProperty, Imp, sync_reasoner_hermit, World

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph.topology import EXTERIOR, _space_nodes  # noqa: E402

owlready2.JAVA_EXE = shutil.which("java") or owlready2.JAVA_EXE
IRI = "http://plan2graph.org/legal.owl"

# 위반 클래스 → 법조문 근거(rules.json/카탈로그와 연결)
VIOLATION_LAW = {
    "DaylightViolation": ("L1_daylight_window", "피난·방화규칙 제17조제1항",
                          "거실·침실 채광창 미보유"),
    "MinAreaViolation": ("L4_bedroom_min_area", "최저주거기준",
                         "침실 최소면적 미달"),
    "IsolationViolation": ("R1_isolated_component", "위상 무결성",
                           "고립 공간(문 없이 단절)"),
    "UnreachableViolation": ("R3_unreachable_from_entrance", "건축법시행령 제34조",
                             "현관에서 도달 불가(피난 동선 단절)"),
}


def _build(world: World):
    """법규 온톨로지(클래스·속성·SWRL 규칙) 정의."""
    onto = world.get_ontology(IRI)
    with onto:
        class Space(Thing):
            pass

        class Habitable(Space):     # 거실·침실(거주실)
            pass

        class Bedroom(Habitable):
            pass

        class LivingRoom(Habitable):
            pass

        # 위반 클래스(추론 결과)
        class DaylightViolation(Space):
            pass

        class MinAreaViolation(Space):
            pass

        class IsolationViolation(Space):
            pass

        class UnreachableViolation(Space):
            pass

        # grounding된 사실(파이썬이 계산해 단언) — boolean 데이터속성
        class hasNoWindow(Space >> bool, FunctionalProperty):
            pass

        class belowMinArea(Space >> bool, FunctionalProperty):
            pass

        class isIsolated(Space >> bool, FunctionalProperty):
            pass

        class isUnreachable(Space >> bool, FunctionalProperty):
            pass

        # SWRL 규칙(산술 built-in 없는 순수 논리) — grounding 사실 → 위반 분류
        for body, head in [
            ("Habitable(?r), hasNoWindow(?r, true)", "DaylightViolation(?r)"),
            ("Bedroom(?r), belowMinArea(?r, true)", "MinAreaViolation(?r)"),
            ("Space(?r), isIsolated(?r, true)", "IsolationViolation(?r)"),
            ("Space(?r), isUnreachable(?r, true)", "UnreachableViolation(?r)"),
        ]:
            Imp().set_as_rule(f"{body} -> {head}")
    return onto


def _ground(G: nx.Graph, onto, world: World):
    """그래프 → ABox 개체 + grounding 사실(수치·위상은 파이썬이 계산)."""
    scale = G.graph.get("scale")
    min_m2 = getattr(config, "LEGAL_BEDROOM_MIN_M2", None)
    # 위상 grounding: 고립·현관 도달성(networkx, open-world DL이 못 하는 부분)
    spaces = _space_nodes(G)
    sub = G.subgraph(spaces)
    reach = set()
    entrances = [n for n in spaces if G.nodes[n].get("is_entrance")]
    for e in entrances:
        reach |= nx.node_connected_component(sub, e)
    comps = list(nx.connected_components(sub))
    main = max(comps, key=len) if comps else set()

    Bedroom, LivingRoom, Habitable, Space = (onto.Bedroom, onto.LivingRoom,
                                             onto.Habitable, onto.Space)
    ind = {}
    for n in spaces:
        d = G.nodes[n]
        t = d.get("type")
        cls = Bedroom if t == "침실" else LivingRoom if t == "거실" else Space
        x = cls(f"n{n}")
        ind[n] = x
        if t in ("침실", "거실"):
            x.hasNoWindow = (d.get("n_windows", 0) < 1)
        if t == "침실" and scale and min_m2 and d.get("area_px"):
            m2 = d["area_px"] * scale ** 2 / 1e6
            x.belowMinArea = (m2 < min_m2)
        # 고립: 본체(최대 연결요소)에 없으면 고립
        x.isIsolated = (n not in main) if comps else False
        # 도달 불가: 현관 있는데 못 닿으면
        x.isUnreachable = bool(entrances) and (n not in reach)
    return ind


def check_legal_swrl(G: nx.Graph, debug: bool = False) -> dict:
    """SWRL+HermiT 심볼릭 법규 검증. 위반 클래스 추론 → 리포트."""
    world = World()
    onto = _build(world)
    ind = _ground(G, onto, world)
    try:
        with onto:
            sync_reasoner_hermit(world, infer_property_values=False,
                                 debug=1 if debug else 0)
    except Exception as e:  # noqa: BLE001
        return {"passed": None, "error": f"reasoner: {str(e)[:200]}", "violations": []}

    # 추론된 위반 클래스 개체 수집
    inv = {v: n for n, v in ind.items()}
    violations = []
    for vcls, (rule_id, law, msg) in VIOLATION_LAW.items():
        cls = onto[vcls]
        for x in cls.instances():
            node = inv.get(x)
            violations.append({"rule": rule_id, "violation_class": vcls,
                               "node": node, "type": (G.nodes[node].get("type")
                                                      if node is not None else None),
                               "law": law, "msg": msg})
    return {
        "engine": "swrl+hermit",
        "passed": len(violations) == 0,
        "n_violations": len(violations),
        "violations": violations,
    }


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import glob
    import json
    from plan2graph.review import record_to_graph
    g = sorted(glob.glob(str(config.PROCESSED_DIR / "graphs" / "*.json")))
    rec = json.loads(Path(g[0]).read_text(encoding="utf-8"))
    G = record_to_graph(rec)
    G.graph["scale"] = rec["meta"].get("scale")
    for nd in rec["layout"]["nodes"]:
        if isinstance(nd["id"], int) and G.has_node(nd["id"]):
            G.nodes[nd["id"]]["area_px"] = nd.get("area_px2")
            G.nodes[nd["id"]]["n_windows"] = nd.get("n_windows", 0)
            G.nodes[nd["id"]]["is_entrance"] = nd.get("is_entrance", False)
    print(f"검증: {rec['graph_id']} (scale={G.graph['scale']})")
    rep = check_legal_swrl(G)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
