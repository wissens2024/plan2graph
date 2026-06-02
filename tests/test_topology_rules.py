"""Task 1-3/1-5 단위 테스트 — 합성 그래프로 문-방 추론·무결성 규칙 검증.

실데이터 없이 도는 빠른 테스트. 합성 도면(작은 사각형 방 + 문)으로
build_graph의 엣지 추론과 rules의 위반 탐지를 확인한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shapely.geometry import box  # noqa: E402

from plan2graph.geometry import Drawing, Element  # noqa: E402
from plan2graph.topology import build_graph, EXTERIOR  # noqa: E402
from plan2graph.rules import check_integrity  # noqa: E402


def _room(cls, x0, y0, x1, y1):
    p = box(x0, y0, x1, y1)
    c = p.centroid
    return Element(kind="room", class_name=cls, subtype=None, polygon=p,
                   bbox=[x0, y0, x1 - x0, y1 - y0], area_px=p.area,
                   centroid=(c.x, c.y))


def _door(x0, y0, x1, y1, subtype="여닫이문"):
    p = box(x0, y0, x1, y1)
    c = p.centroid
    return Element(kind="door", class_name="구조_출입문", subtype=subtype,
                   polygon=p, bbox=[x0, y0, x1 - x0, y1 - y0], area_px=p.area,
                   centroid=(c.x, c.y))


def _wall(x0, y0, x1, y1):
    p = box(x0, y0, x1, y1)
    c = p.centroid
    return Element(kind="wall", class_name="구조_벽체", subtype=None,
                   polygon=p, bbox=[x0, y0, x1 - x0, y1 - y0], area_px=p.area,
                   centroid=(c.x, c.y))


def _two_room_drawing():
    """[현관 0..100][침실 100..200], 경계 x=100에 문(벽 두께 표현)."""
    dr = Drawing(image_path=None, width=200, height=100, scale=None)
    dr.rooms = [_room("공간_현관", 0, 0, 100, 100),
                _room("공간_침실", 100, 0, 200, 100)]
    # 문: 벽(세로) 위에 가로로 얇게 → 긴 변은 세로(벽), 법선은 가로(x) → 양쪽 방
    dr.doors = [_door(95, 40, 105, 60)]
    return dr


def test_door_connects_two_rooms():
    G = build_graph(_two_room_drawing(), graph_id="t1")
    assert G.has_edge(0, 1), "문이 두 방을 잇는 엣지를 만들어야 함"
    assert G[0][1]["via"] == "door"
    assert G[0][1]["door_type"] == "여닫이문"
    assert G.graph["n_unresolved_doors"] == 0


def test_entrance_to_exterior():
    G = build_graph(_two_room_drawing(), graph_id="t1")
    assert G.has_node(EXTERIOR)
    assert G.has_edge(0, EXTERIOR), "현관은 외부와 연결돼야 함"


def test_clean_graph_passes_integrity():
    G = build_graph(_two_room_drawing(), graph_id="t1")
    rep = check_integrity(G)
    assert rep["passed"], f"정상 그래프는 무결성 통과해야 함: {rep['violations']}"


def test_remove_door_detects_violation():
    """벽으로 막힌(간격을 벽이 채운) 두 방에서 문을 제거하면 고립 → 위반 탐지.
    ※ 실제 도면처럼 방 사이에 벽 두께 간격을 두고, 그 간격을 벽이 채운다."""
    dr = Drawing(image_path=None, width=210, height=100, scale=None)
    dr.rooms = [_room("공간_현관", 0, 0, 90, 100),
                _room("공간_침실", 110, 0, 210, 100)]
    dr.walls = [_wall(90, 0, 110, 100)]  # 두 방 사이 간격을 벽이 완전히 채움
    dr.doors = []  # 흠집: 문 제거
    G = build_graph(dr, graph_id="t1-broken")
    rep = check_integrity(G)
    assert not rep["passed"], "문 없고 벽으로 막힌 그래프는 위반이어야 함"
    rules = {v["rule"] for v in rep["violations"]}
    assert "R2_doorless_room" in rules or "R1_isolated_component" in rules


def test_isolated_room_detected():
    """문과 무관한 떠 있는 방 → 고립 탐지."""
    dr = _two_room_drawing()
    dr.rooms.append(_room("공간_화장실", 500, 500, 600, 600))  # 멀리 떨어진 방
    G = build_graph(dr, graph_id="t1-iso")
    rep = check_integrity(G)
    assert not rep["passed"]
    assert any(v["rule"] in ("R2_doorless_room", "R1_isolated_component")
               for v in rep["violations"])


def test_balcony_window_becomes_passage():
    """발코니에 접한 미닫이창 → via:balcony 통로 엣지로 승격."""
    from plan2graph.geometry import Element
    from shapely.geometry import box
    dr = Drawing(image_path=None, width=300, height=100, scale=None)
    dr.rooms = [_room("공간_거실", 0, 0, 100, 100),
                _room("공간_발코니", 100, 0, 200, 100)]
    # 거실-발코니 경계(x=100)에 미닫이창
    w = box(95, 40, 105, 60)
    c = w.centroid
    dr.windows = [Element(kind="window", class_name="구조_창호", subtype="미닫이창",
                          polygon=w, bbox=[95, 40, 10, 20], area_px=w.area,
                          centroid=(c.x, c.y))]
    G = build_graph(dr, graph_id="bal")
    assert G.has_edge(0, 1), "발코니 슬라이딩이 통로 엣지를 만들어야 함"
    assert G[0][1]["via"] == "balcony"


def test_classify_unit_complete_vs_quarantine():
    from plan2graph.build_dataset import classify_unit
    complete = {"meta": {"n_rooms": 6},
                "constraints": {"program": {"현관": 1, "거실": 1, "침실": 1,
                                            "주방": 1, "화장실": 1, "발코니": 1}},
                "validation": {"passed": True}}
    assert classify_unit(complete)[0] == "complete"
    no_kitchen = {"meta": {"n_rooms": 5},
                  "constraints": {"program": {"현관": 1, "거실": 1, "침실": 1,
                                              "화장실": 1, "발코니": 1}},
                  "validation": {"passed": True}}
    s, reason = classify_unit(no_kitchen)
    assert s == "quarantine" and "주방" in reason


def test_split_bucket_deterministic_and_sheet_grouped():
    from plan2graph.split import _bucket, _sheet_key
    # 같은 시트의 다른 세대는 같은 버킷
    assert _sheet_key("APT_FP_abc123_u0") == _sheet_key("APT_FP_abc123_u3")
    b1 = _bucket("APT_FP_abc123")
    assert b1 in ("train", "val", "test")
    assert _bucket("APT_FP_abc123") == b1  # 결정적


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
