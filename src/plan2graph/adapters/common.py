"""글로벌 데이터 어댑터 공통 — (방+연결) → Plan2Graph 공통 스키마 레코드.

RPLAN·CubiCasa5k 등 출처가 달라도 **동일한 layout+constraints 레코드**로 변환해
하나의 풀에 합류시킨다("합쳐서 학습"의 전제). 각 어댑터(rplan/cubicasa)는 자기 포맷을
파싱해 (rooms, edges)만 만들고 to_record()를 호출 → 포맷 일치는 schema.py가 보장.

타입 매핑: 외부 라벨 → 우리 공간_* 온톨로지 클래스(위계·program 정렬).
"""
from __future__ import annotations

import sys
from pathlib import Path

import networkx as nx

ROOT = Path(__file__).resolve().parents[3]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph.topology import EXTERIOR  # noqa: E402
from plan2graph.schema import serialize  # noqa: E402

# 외부 라벨 → 우리 온톨로지 클래스. (소문자 키로 정규화 매칭)
GLOBAL_TYPE_MAP = {
    # 거실/LDK
    "living": "공간_거실", "livingroom": "공간_거실", "living_room": "공간_거실",
    "lvroom": "공간_거실", "saloon": "공간_거실",
    # 침실
    "bedroom": "공간_침실", "masterroom": "공간_침실", "master": "공간_침실",
    "secondroom": "공간_침실", "childroom": "공간_침실", "guestroom": "공간_침실",
    "room": "공간_침실", "bed": "공간_침실",
    # 주방/식당
    "kitchen": "공간_주방", "dining": "공간_주방", "diningroom": "공간_주방",
    # 화장실
    "bathroom": "공간_화장실", "bath": "공간_화장실", "toilet": "공간_화장실",
    "washroom": "공간_화장실", "restroom": "공간_화장실",
    # 현관/홀
    "entrance": "공간_현관", "entry": "공간_현관", "foyer": "공간_현관",
    "hall": "공간_기타", "hallway": "공간_기타", "corridor": "공간_기타",
    # 발코니
    "balcony": "공간_발코니", "terrace": "공간_발코니", "loggia": "공간_발코니",
    # 기타 거주·서비스
    "study": "공간_다목적공간", "studyroom": "공간_다목적공간", "office": "공간_다목적공간",
    "storage": "공간_드레스룸", "closet": "공간_드레스룸", "wardrobe": "공간_드레스룸",
    "dressing": "공간_드레스룸",
    "laundry": "공간_실외기실", "utility": "공간_실외기실",
}


def map_type(label: str) -> str:
    """외부 라벨 → 공간_* 클래스(미상은 공간_기타)."""
    return GLOBAL_TYPE_MAP.get(str(label).strip().lower().replace(" ", ""), "공간_기타")


def build_graph_from_rooms(rooms: list[dict], edges: list[tuple],
                           graph_id: str) -> nx.Graph:
    """rooms[{type(공간_*), centroid, area_px, n_windows?}], edges[(i,j,via)] → nx.Graph.
    우리 build_graph 산출과 동일한 노드/엣지 속성(스키마 호환)."""
    G = nx.Graph(graph_id=graph_id, house_type=None, scale=None, area_unit="px2")
    for i, r in enumerate(rooms):
        cls = r["type"]
        G.add_node(i, type=cls.replace("공간_", ""), raw_class=cls,
                   hierarchy=config.HIERARCHY.get(cls),
                   area_px=round(r.get("area_px", 0.0), 1),
                   centroid=r.get("centroid"),
                   n_windows=r.get("n_windows", 0),
                   window_len_px=r.get("window_len_px", 0.0),
                   objects=r.get("objects", []),
                   is_entrance=(cls == config.ENTRANCE_CLASS))
    for a, b, via in edges:
        if a == b:
            continue
        if not G.has_edge(a, b):
            G.add_edge(a, b, via=via, door_type=None,
                       n_doors=1 if via == "door" else None)
    # 현관 → 외부
    for i, r in enumerate(rooms):
        if r["type"] == config.ENTRANCE_CLASS and not G.has_edge(i, EXTERIOR):
            G.add_edge(i, EXTERIOR, via="entrance", door_type=None)
    if G.has_node(EXTERIOR):
        G.nodes[EXTERIOR].update(type="exterior", hierarchy=None, centroid=None)
    G.graph.update(n_rooms=len(rooms), n_doors=sum(1 for _, _, v in edges if v == "door"),
                   n_open_edges=sum(1 for _, _, v in edges if v == "open"),
                   n_balcony_edges=sum(1 for _, _, v in edges if v == "balcony"),
                   n_unresolved_doors=0)
    return G


def to_record(graph_id: str, source: str, rooms: list[dict], edges: list[tuple],
              width: int, height: int) -> dict:
    """(rooms, edges) → 공통 스키마 레코드(layout+constraints+validation)."""
    from plan2graph.rules import validate
    G = build_graph_from_rooms(rooms, edges, graph_id)
    rec = serialize(G, graph_id=graph_id, house_type=None,
                    width=width, height=height, validation=validate(G))
    rec["meta"]["source"] = source       # 'rplan' / 'cubicasa5k'
    rec["meta"]["provenance"] = "global_pretrain"
    return rec


def _self_test() -> bool:
    """합성 (방+연결) → 레코드 생성 + 스키마 필드 확인(파서 불요)."""
    rooms = [
        {"type": "공간_현관", "centroid": [10, 10], "area_px": 1000},
        {"type": "공간_거실", "centroid": [50, 50], "area_px": 8000, "n_windows": 1},
        {"type": "공간_침실", "centroid": [90, 30], "area_px": 5000, "n_windows": 1},
        {"type": "공간_주방", "centroid": [50, 90], "area_px": 4000},
        {"type": "공간_화장실", "centroid": [90, 70], "area_px": 1500},
    ]
    edges = [(0, 1, "door"), (1, 2, "open"), (1, 3, "open"), (2, 4, "door")]
    rec = to_record("RPLAN_0001", "rplan", rooms, edges, 256, 256)
    ok = (rec["constraints"]["program"].get("거실") == 1
          and any(e["via"] == "entrance" for e in rec["layout"]["edges"])
          and rec["meta"]["source"] == "rplan"
          and "adjacency" in rec["constraints"])
    print(f"공통변환 self-test: program={rec['constraints']['program']} "
          f"adjacency={rec['constraints']['adjacency'][:3]} → {'OK' if ok else 'FAIL'}")
    return ok


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(0 if _self_test() else 1)
