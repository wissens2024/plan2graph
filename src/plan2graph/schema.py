"""Task 2-2 — 표준 그래프 스키마 직렬화 (배치 그래프 + 파생 제약 그래프).

한 도면 = {layout(실측 위상) + constraints(기하 제거한 프로그램·인접 요구)} 한 쌍.
- layout: build_graph 결과(방 노드 + 문/현관 엣지). 픽셀 좌표 + 0~1 정규화 병기.
- constraints: layout에서 파생. 방 프로그램(타입별 개수), 인접 요구(엣지 타입쌍),
  프라이버시. 기하 좌표 제거 → Phase 3-a(Text→제약그래프)/3-b(제약→배치) 학습쌍.

출력 dict는 그대로 JSON 직렬화 가능(ensure_ascii=False).
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

SCHEMA_VERSION = "0.2"   # 0.2: meta.status/reason/role/tier 추가(가산적, DATASET_DESIGN §8)
SOURCE_TAG = "aihub-71465"


def _normalized_centroid(c, w, h):
    if c is None or not w or not h:
        return None
    return [round(c[0] / w, 4), round(c[1] / h, 4)]


def to_layout(G: nx.Graph, width: int, height: int) -> dict:
    """배치 그래프 직렬화. 노드=공간, 엣지=문/현관. 좌표 픽셀+정규화 병기."""
    nodes = []
    for n, d in G.nodes(data=True):
        if n == EXTERIOR:
            nodes.append({"id": "exterior", "type": "exterior", "hierarchy": None})
            continue
        c = d.get("centroid")
        nodes.append({
            "id": n,
            "type": d.get("type"),
            "hierarchy": d.get("hierarchy"),
            "area_px2": d.get("area_px"),
            "centroid_px": c,
            "centroid_norm": _normalized_centroid(c, width, height),
            "n_windows": d.get("n_windows", 0),
            "window_len_px": d.get("window_len_px", 0.0),
            "objects": d.get("objects", []),
            "is_entrance": d.get("is_entrance", False),
        })
    edges = []
    for u, v, d in G.edges(data=True):
        edges.append({
            "source": "exterior" if u == EXTERIOR else u,
            "target": "exterior" if v == EXTERIOR else v,
            "via": d.get("via"),
            "door_type": d.get("door_type"),
            "n_doors": d.get("n_doors", 1) if d.get("via") == "door" else None,
        })
    return {"nodes": nodes, "edges": edges}


def derive_constraints(G: nx.Graph) -> dict:
    """배치 그래프 → 제약 그래프 파생(기하 제거).
    program: 방 타입별 개수. adjacency: 인접한 방 타입쌍(문 연결). privacy: 타입→위계.
    """
    program: dict[str, int] = {}
    privacy: dict[str, str] = {}
    for n, d in G.nodes(data=True):
        if n == EXTERIOR:
            continue
        t = d.get("type")
        if t is None:
            continue
        program[t] = program.get(t, 0) + 1
        h = d.get("hierarchy")
        if h:
            privacy[t] = h

    # 인접 요구: 문으로 연결된 방 타입쌍(정렬·중복 제거, 자기쌍 포함 가능)
    adjacency_set = set()
    has_exterior_entry = []
    for u, v, d in G.edges(data=True):
        if d.get("via") not in ("door", "entrance", "exterior_door", "balcony", "open"):
            continue
        tu = "exterior" if u == EXTERIOR else G.nodes[u].get("type")
        tv = "exterior" if v == EXTERIOR else G.nodes[v].get("type")
        if tu is None or tv is None:
            continue
        if "exterior" in (tu, tv):
            other = tv if tu == "exterior" else tu
            has_exterior_entry.append(other)
            continue
        adjacency_set.add(tuple(sorted((tu, tv))))

    return {
        "program": program,
        "adjacency": [list(p) for p in sorted(adjacency_set)],
        "privacy": privacy,
        "exterior_access": sorted(set(has_exterior_entry)),
    }


def serialize(G: nx.Graph, *, graph_id: str, house_type: str | None,
              width: int, height: int, validation: dict | None = None,
              status: str = "success", reason: str = "",
              role: str | None = None, tier: int | None = None) -> dict:
    """완전한 표준 레코드(배치+제약+검증) 생성.

    status/reason : 정상/격리 분기(DATASET_DESIGN §2③). 기본 success.
    role          : benchmark | pretrain (§6). 출처에서 stamp(미지정 시 None).
    tier          : 1(파싱) | 2(비전) (§3).
    """
    return {
        "graph_id": graph_id,
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "house_type": house_type,
            "scale": G.graph.get("scale"),
            "area_unit": "px2" if G.graph.get("scale") is None else "m2",
            "source": SOURCE_TAG,
            "status": status,
            "reason": reason,
            "role": role,
            "tier": tier,
            "width_px": width,
            "height_px": height,
            "n_rooms": G.graph.get("n_rooms"),
            "n_doors": G.graph.get("n_doors"),
            "n_unresolved_doors": G.graph.get("n_unresolved_doors"),
        },
        "layout": to_layout(G, width, height),
        "constraints": derive_constraints(G),
        "validation": validation or {},
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
    from plan2graph.rules import validate
    docs = [load_coco(p) for p in sys.argv[1:]]
    dr = assemble_drawing(docs)
    G = build_graph(dr, graph_id="demo")
    rec = serialize(G, graph_id="demo", house_type="APT",
                    width=dr.width, height=dr.height, validation=validate(G))
    # 요약만 출력(전체는 큼)
    print(json.dumps({
        "graph_id": rec["graph_id"], "meta": rec["meta"],
        "constraints": rec["constraints"],
        "n_layout_nodes": len(rec["layout"]["nodes"]),
        "n_layout_edges": len(rec["layout"]["edges"]),
        "validation_passed": rec["validation"].get("passed"),
    }, ensure_ascii=False, indent=2))
