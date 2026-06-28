"""법규 엔진 — 강행규정을 기계규칙으로 (사업계획서 1단계 "법규의 기계어 변환").

모듈형 규칙 DB: 각 규칙은 법령 근거(국가법령정보센터 API로 수집)와 검사함수를 가진다.
현재 그래프 데이터(방 타입·㎡·창 개수·위상)로 '검사 가능한' 강행규정부터 구현하고,
정확 수치/추가 데이터가 필요한 부분은 status로 표시한다(예외조항 확장은 후속).

근거는 law_api로 조회·캐시. scale(㎡) 확보분에만 면적 의존 규칙을 적용한다.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph.topology import EXTERIOR  # noqa: E402


@dataclass
class Rule:
    id: str
    name: str
    category: str            # 강행 / 권장
    law: str                 # 법령명
    article: str             # 조문
    mst: str | None          # 법령일련번호(API 근거)
    basis: str               # 기준 요약
    status: str              # confirmed / needs_window_area / needs_expert
    needs_scale: bool


# ── 규칙 DB (API로 근거 확보, docs/ROADMAP.md §8 열린 질문에 전문가 확인 항목 표시) ──
RULES: list[Rule] = [
    Rule("L1_daylight_window", "거실·침실 채광창 보유", "강행",
         "건축물의 피난·방화구조 등의 기준에 관한 규칙", "제17조제1항", "279461",
         "거실 창문 면적 ≥ 거실 바닥면적 1/10. (v1: 창 보유=필요조건 검사, 정확비율은 창면적 확보 후)",
         "needs_window_area", False),
    Rule("L2_ventilation_window", "거실·침실 환기창 보유", "강행",
         "건축물의 피난·방화구조 등의 기준에 관한 규칙", "제17조제2항", "279461",
         "환기 창문 면적 ≥ 바닥면적 1/20. (v1: 창 보유 검사)",
         "needs_window_area", False),
    Rule("L3_egress_reachable", "거실에서 직통 피난 경로", "강행",
         "건축법 시행령", "제34조(직통계단)", "273503",
         "각 실에서 피난층/외부로 통하는 경로 존재(위상 도달성).",
         "confirmed", False),
    Rule("L1_daylight_ratio", "거실·침실 채광 면적비", "강행",
         "건축물의 피난·방화구조 등의 기준에 관한 규칙", "제17조제1항", "279461",
         f"창 면적 ≥ 바닥 {config.LEGAL_DAYLIGHT_RATIO}. 창높이 {config.WINDOW_EST_HEIGHT_M}m 추정(폭만 라벨).",
         "estimate_scale", True),
    Rule("L4_bedroom_min_area", "침실 최소 면적", "강행(전문가확인)",
         "주거기본법/최저주거기준 고시", "최저주거기준", None,
         f"침실 면적 ≥ {config.LEGAL_BEDROOM_MIN_M2}㎡(설계 참고값). 최저주거기준 고시는 방 개수만 "
         "규정하고 침실 1실 면적은 미규정 → 전문가 확인 필요.",
         "needs_expert", True),
    Rule("L5_dwelling_min_area", "세대 최소 전용면적", "강행",
         "주거기본법 / 최저주거기준 고시", "제17조(최저주거기준)", "280141",
         f"가구원수별 최소 주거면적(1인 {config.LEGAL_MIN_DWELLING_M2}㎡ 등) — 주거기본법 §17이 위임한 "
         "최저주거기준 고시(행정규칙 2000000059613)에 명시. 면적검사는 도면 scale 필요.",
         "estimate_scale", True),
    Rule("L6_refuge_area", "발코니 대피공간 최소 면적", "강행",
         "건축법 시행령", "제46조제4항·제5항", "273503",
         f"대피공간 세대별 ≥ {config.LEGAL_REFUGE_MIN_M2}㎡(인접세대 공동설치 3㎡). "
         "설치 의무(4층 이상·직통계단 2 미만)는 층/계단 데이터 확보 후 확장.",
         "estimate_scale", True),
]

# 채광·환기 대상 공간(거실·침실 등 거주실)
HABITABLE = ("공간_거실", "공간_침실")


def _hab_nodes(G: nx.Graph):
    for n, d in G.nodes(data=True):
        if n == EXTERIOR:
            continue
        t = d.get("type")
        if t and ("공간_" + t) in HABITABLE:
            yield n, d


def check_daylight(G: nx.Graph) -> list[dict]:
    """L1: 거실·침실 채광(§17①). 창 없으면 필요조건 위반.
    scale·창폭·면적 있으면 **면적비**(창면적 ≥ 바닥 1/10)까지 검사(창높이 추정)."""
    scale = G.graph.get("scale")
    ratio = getattr(config, "LEGAL_DAYLIGHT_RATIO", 0.10)
    hgt = getattr(config, "WINDOW_EST_HEIGHT_M", 1.2)
    v = []
    for n, d in _hab_nodes(G):
        if d.get("n_windows", 0) < 1:
            v.append({"rule": "L1_daylight_window", "node": n, "type": d.get("type"),
                      "law": "피난·방화규칙 제17조제1항",
                      "msg": "채광창 없음(거실·침실은 채광창 필수)"})
            continue
        # 면적비 검사(scale 확보분): 창면적 ≈ 폭(px·scale→m) × 추정높이
        if scale and d.get("window_len_px") and d.get("area_px"):
            win_m2 = (d["window_len_px"] * scale / 1000.0) * hgt
            floor_m2 = d["area_px"] * scale ** 2 / 1e6
            if floor_m2 > 0 and win_m2 / floor_m2 < ratio:
                v.append({"rule": "L1_daylight_ratio", "node": n, "type": d.get("type"),
                          "law": "피난·방화규칙 제17조제1항",
                          "ratio": round(win_m2 / floor_m2, 3), "min_ratio": ratio,
                          "msg": f"채광창 면적비 {win_m2/floor_m2:.2f} < {ratio} (추정)"})
    return v


def check_ventilation(G: nx.Graph) -> list[dict]:
    """L2: 거실·침실 환기창 보유(설비기준 §11). v1=창 보유 검사(채광과 동일 거주실 요건,
    면적비 1/20은 scale 확보 시). 채광용 창이 환기 겸용이라 거실·침실 창 1개로 L1·L2 동시 충족."""
    v = []
    for n, d in _hab_nodes(G):
        if d.get("n_windows", 0) < 1:
            v.append({"rule": "L2_ventilation_window", "node": n, "type": d.get("type"),
                      "law": "건축물 설비기준 규칙 제11조",
                      "msg": "환기창 없음(거실·침실은 환기창 필수)"})
    return v


def check_egress(G: nx.Graph) -> list[dict]:
    """L3: 각 실에서 현관 경유 외부로 통하는 피난·접근 동선(위상 도달성). 고립 실=위반.
    문(door) 인접 그래프에서 EXTERIOR(현관 경유)와 연결 안 된 실을 적발(scale 불요)."""
    v = []
    if EXTERIOR not in G:                                  # 현관/외부 연결 자체가 없음
        v.append({"rule": "L3_egress_reachable", "node": None, "type": None,
                  "law": "건축법 시행령 제34조",
                  "msg": "피난 동선 없음(현관에서 외부로 통하는 경로 부재)"})
        return v
    reach = nx.node_connected_component(G, EXTERIOR)       # 외부와 연결된 실 집합
    for n, d in G.nodes(data=True):
        if n == EXTERIOR or n in reach:
            continue
        v.append({"rule": "L3_egress_reachable", "node": n, "type": d.get("type"),
                  "law": "건축법 시행령 제34조",
                  "msg": f"{d.get('type')}#{n}: 피난 동선 없음(현관에서 도달 불가, 고립)"})
    return v


def check_bedroom_area(G: nx.Graph, scale) -> list[dict]:
    """L4: scale 있고 기준 설정 시 침실 면적 하한 검사."""
    min_m2 = getattr(config, "LEGAL_BEDROOM_MIN_M2", None)
    if scale is None or not min_m2:
        return []
    v = []
    for n, d in G.nodes(data=True):
        if d.get("type") != "침실":
            continue
        a_px = d.get("area_px")
        if a_px is None:
            continue
        m2 = a_px * (scale ** 2) / 1e6
        if m2 < min_m2:
            v.append({"rule": "L4_bedroom_min_area", "node": n,
                      "area_m2": round(m2, 1), "min_m2": min_m2,
                      "law": "최저주거기준",
                      "msg": f"침실 {m2:.1f}㎡ < 최소 {min_m2}㎡"})
    return v


def check_dwelling_area(G: nx.Graph, scale) -> list[dict]:
    """L5: 세대 전용면적(거주실 면적 합) 최소 미달 검사(scale 필요)."""
    min_m2 = getattr(config, "LEGAL_MIN_DWELLING_M2", None)
    if scale is None or not min_m2:
        return []
    tot = sum(d.get("area_px", 0) for n, d in G.nodes(data=True)
              if d.get("type") not in (None, "exterior", "발코니", "실외기실"))
    m2 = tot * scale ** 2 / 1e6
    if 0 < m2 < min_m2:
        return [{"rule": "L5_dwelling_min_area", "area_m2": round(m2, 1),
                 "min_m2": min_m2, "law": "주거기본법/최저주거기준",
                 "msg": f"세대 면적 {m2:.0f}㎡ < 최소 {min_m2}㎡"}]
    return []


def check_refuge_area(G: nx.Graph, scale) -> list[dict]:
    """L6: 발코니 대피공간 최소 면적(건축법 시행령 §46④⑤, scale 필요).

    그래프에 존재하는 대피공간 노드의 면적이 세대별 하한(기본 2㎡) 미만이면 위반.
    설치 의무 자체(4층 이상·직통계단 2 미만 조건)는 층수·계단 데이터가 없어 미검사 —
    노드가 있을 때 '면적 적정성'만 검사한다([[handoff-v3-rectilinear-train]] 규제레이어).
    """
    min_m2 = getattr(config, "LEGAL_REFUGE_MIN_M2", None)
    if scale is None or not min_m2:
        return []
    v = []
    for n, d in G.nodes(data=True):
        if d.get("type") != "대피공간":
            continue
        a_px = d.get("area_px")
        if a_px is None:
            continue
        m2 = a_px * (scale ** 2) / 1e6
        if m2 < min_m2:
            v.append({"rule": "L6_refuge_area", "node": n,
                      "area_m2": round(m2, 1), "min_m2": min_m2,
                      "law": "건축법 시행령 제46조제4항·제5항",
                      "msg": f"대피공간 {m2:.1f}㎡ < 세대별 최소 {min_m2}㎡"})
    return v


def check_legal(G: nx.Graph) -> dict:
    """법규 검사. 창 기반은 scale 불요, 면적 의존 규칙은 scale 확보분에만."""
    scale = G.graph.get("scale")
    violations = []
    violations += check_daylight(G)             # L1 채광(창 보유+면적비)
    violations += check_ventilation(G)          # L2 환기(거실·침실 창)
    violations += check_egress(G)               # L3 동선(현관→외부 피난 도달성)
    violations += check_bedroom_area(G, scale)  # L4 침실 최소면적
    violations += check_dwelling_area(G, scale)  # L5 세대 최소면적
    violations += check_refuge_area(G, scale)   # L6 대피공간 최소면적
    applied = ["L1_daylight_window", "L2_ventilation_window", "L3_egress_reachable"]
    skipped = []
    if scale is not None:
        applied += ["L1_daylight_ratio", "L4_bedroom_min_area",
                    "L5_dwelling_min_area", "L6_refuge_area"]
    else:
        skipped += ["L1_daylight_ratio", "L4_bedroom_min_area",
                    "L5_dwelling_min_area", "L6_refuge_area"]
        skipped = [s + "(scale 미확보)" for s in skipped]
    return {
        "passed": len(violations) == 0,
        "n_violations": len(violations),
        "violations": violations,
        "applied_rules": applied,
        "skipped_rules": skipped,
        "scale_available": scale is not None,
    }


def rule_catalog() -> list[dict]:
    """규칙 DB를 직렬화(데이터시트·문서화용)."""
    return [r.__dict__ for r in RULES]


RULE_DB_PATH = ROOT / "legal" / "rules.json"


def export_rule_db(path: Path = None) -> Path:
    """모듈형 법규 규칙 DB를 독립 파일(legal/rules.json)로 영속화.
    각 규칙의 법령 근거(MST)는 data/interim/law_cache/law_<MST>.xml과 연결된다.
    """
    import json
    p = path or RULE_DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "plan2graph-legal-rules/0.1",
        "source": "국가법령정보센터 Open API (law.go.kr/DRF)",
        "oc_env": "LAW_API_OC",
        "law_cache_dir": "data/interim/law_cache",
        "note": "강행규정 핵심셋. status=needs_* 는 데이터/전문가 확인 후 확장(예외조항 포함).",
        "rules": [
            {**r.__dict__,
             "law_source_xml": f"data/interim/law_cache/law_{r.mst}.xml" if r.mst else None}
            for r in RULES
        ],
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _graph_from_record(rec: dict) -> nx.Graph:
    G = nx.Graph(graph_id=rec["graph_id"])
    G.graph["scale"] = rec["meta"].get("scale")
    for nd in rec["layout"]["nodes"]:
        if isinstance(nd["id"], int):
            G.add_node(nd["id"], type=nd.get("type"),
                       area_px=nd.get("area_px2"), n_windows=nd.get("n_windows", 0))
    return G


def annotate_all(processed_dir: Path = None) -> dict:
    """채택 그래프 전체에 법규 검사 결과를 validation.legal로 기록."""
    import glob
    import json
    pd = processed_dir or config.PROCESSED_DIR
    import collections
    stat = collections.Counter()
    vio = collections.Counter()
    n = 0
    for fp in glob.glob(str(pd / "graphs" / "*.json")):
        rec = json.loads(Path(fp).read_text(encoding="utf-8"))
        G = _graph_from_record(rec)
        rep = check_legal(G)
        rec.setdefault("validation", {})["legal"] = rep
        Path(fp).write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        n += 1
        stat["legal_pass" if rep["passed"] else "legal_violation"] += 1
        for v in rep["violations"]:
            vio[v["rule"]] += 1
    return {"n": n, "status": dict(stat), "violations": dict(vio)}


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import json
    print("=== 법규 규칙 DB ===")
    for r in RULES:
        print(f"  [{r.id}] {r.name} — {r.law} {r.article} (status={r.status})")
    # 채택 그래프 하나에 적용
    import glob
    g = sorted(glob.glob(str(config.PROCESSED_DIR / "graphs" / "*.json")))
    if g:
        from plan2graph.review import record_to_graph
        rec = json.loads(Path(g[0]).read_text(encoding="utf-8"))
        G = record_to_graph(rec)
        G.graph["scale"] = rec["meta"].get("scale")
        # area_px를 노드에 실어 면적 규칙 가능케
        for nd in rec["layout"]["nodes"]:
            if isinstance(nd["id"], int) and G.has_node(nd["id"]):
                G.nodes[nd["id"]]["area_px"] = nd.get("area_px2")
                G.nodes[nd["id"]]["n_windows"] = nd.get("n_windows", 0)
        rep = check_legal(G)
        print(f"\n적용: {rec['graph_id']} (scale={G.graph['scale']})")
        print(json.dumps(rep, ensure_ascii=False, indent=2))
