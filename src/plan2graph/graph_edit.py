"""graph_edit — 그래프 구조 편집 연산(순수 함수, shapely). 에디터(edit_server)가 import해 호출.

SPLIT: 추출이 연결공간(복도·파우더룸/전실)을 옆 큰 방에 흡수해 길쭉해진 폴리곤
(거실+복도, 드레스룸+파우더룸 뭉침)을 사람이 그은 컷 라인으로 2개로 나누고,
문·창·인접을 **기하적으로 재분배**해 연결공간 허브를 복원한다([[canonical-kr-apartment-topology]]).

핵심: 컷(사람 판단) + 문/엣지 자동 재분배(기하). 컷만 하고 재분배 안 하면 위상이 더 엉킴.
merge(edit_server)의 역연산. 스키마: rooms 키=str(int), edges {from,to,via,door_id}(int),
doors {id,connects[int],position}, windows {belongs_to:int}.
"""
from __future__ import annotations

import math


def split_room(graph: dict, room_id, cut, roles):
    """room_id 폴리곤을 cut([[x1,y1],[x2,y2]] 선분)으로 2분할.
    roles=[left_role, right_role] — cut 방향(p0→p1) 기준 **좌측=roles[0], 우측=roles[1]**.
    문·창은 위치가 속한 조각으로, 엣지는 (문 위치 또는 이웃 근접으로) 재귀속하고
    두 조각을 open으로 연결. graph를 in-place 수정 후 (graph, err) 반환(err=None이면 성공)."""
    try:
        from shapely.geometry import Polygon, Point, LineString
        from shapely.ops import split as shp_split, unary_union
    except Exception as e:  # noqa: BLE001
        return graph, f"shapely 없음: {e}"

    rooms = graph.get("rooms", {})
    rid = str(room_id)
    if rid not in rooms or not (rooms[rid].get("polygon") and len(rooms[rid]["polygon"]) >= 3):
        return graph, "room/polygon 없음"
    poly = Polygon(rooms[rid]["polygon"])
    if not poly.is_valid:
        poly = poly.buffer(0)
    (x1, y1), (x2, y2) = cut
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy) or 1.0
    bx = poly.bounds
    ext = max(bx[2] - bx[0], bx[3] - bx[1]) * 2 + 10          # 폴리곤 밖까지 연장
    line = LineString([(x1 - dx / L * ext, y1 - dy / L * ext),
                       (x2 + dx / L * ext, y2 + dy / L * ext)])
    try:
        parts = [p for p in shp_split(poly, line).geoms if p.area > 1.0]
    except Exception as e:  # noqa: BLE001
        return graph, f"split 실패: {e}"
    if len(parts) < 2:
        return graph, "컷이 폴리곤을 가르지 못함(양 끝이 방을 통과해야)"

    def _side(p):                                              # >0 좌, <0 우 (cut 방향 기준)
        c = p.representative_point()
        return (x2 - x1) * (c.y - y1) - (y2 - y1) * (c.x - x1)

    left = unary_union([p for p in parts if _side(p) >= 0])
    right = unary_union([p for p in parts if _side(p) < 0])
    if left.is_empty or right.is_empty:
        return graph, "한쪽만 생성됨(컷 위치 확인)"
    if left.geom_type == "MultiPolygon":
        left = max(left.geoms, key=lambda g: g.area)
    if right.geom_type == "MultiPolygon":
        right = max(right.geoms, key=lambda g: g.area)

    nums = [int(k) for k in rooms if str(k).lstrip("-").isdigit()]
    nid = str(max(nums) + 1 if nums else 0)
    ridi, nidi = int(rid), int(nid)

    try:
        from plan2graph import geomgraph
        PRIV, CONN = geomgraph.PRIVACY, set(geomgraph.CONNECTOR_ROLES)
    except Exception:  # noqa: BLE001
        PRIV, CONN = {}, {"복도", "전실"}

    keep = {k: v for k, v in rooms[rid].items()
            if k not in ("polygon", "centroid", "centroid_norm", "area_px", "area_m2", "bbox_px",
                         "role", "base", "is_connector", "privacy", "door_ids", "window_ids",
                         "fixtures", "has_window", "n_windows", "aspect_ratio", "perimeter_px",
                         "exterior_len_px", "wall_ids", "dist_from_entrance")}

    def _mk(p, role):
        c, b = p.centroid, p.bounds
        return {**keep, "role": role, "base": role,
                "polygon": [[round(x, 1), round(y, 1)] for x, y in p.exterior.coords[:-1]],
                "centroid": [round(c.x, 1), round(c.y, 1)],
                "bbox_px": [round(b[0], 1), round(b[1], 1), round(b[2] - b[0], 1), round(b[3] - b[1], 1)],
                "area_px": round(p.area, 1), "area_m2": None,
                "is_connector": role in CONN, "privacy": PRIV.get(role, "private"),
                "door_ids": [], "window_ids": [], "fixtures": [], "has_window": False, "n_windows": 0}

    rooms[rid] = _mk(left, roles[0])
    rooms[nid] = _mk(right, roles[1])

    def _which(pt):                                           # 점이 속한 조각 id(int)
        P = Point(pt)
        if left.covers(P):
            return ridi
        if right.covers(P):
            return nidi
        return ridi if left.distance(P) <= right.distance(P) else nidi

    # 문: connects의 rid를 위치가 속한 조각으로
    for d in graph.get("doors", []):
        conn = d.get("connects") or []
        if ridi not in conn:
            continue
        piece = _which(d["position"]) if d.get("position") else ridi
        d["connects"] = [piece if c == ridi else c for c in conn]

    # 창: belongs_to
    for w in graph.get("windows", []):
        if w.get("belongs_to") == ridi and w.get("position"):
            w["belongs_to"] = _which(w["position"])

    # 엣지: 문 위치(있으면) 또는 이웃 근접으로 재귀속
    doors_by_id = {d.get("id"): d for d in graph.get("doors", [])}

    def _piece_for_neighbor(nb):
        nbp = rooms.get(str(nb), {}).get("polygon")
        if not nbp:
            return ridi
        NB = Polygon(nbp)
        return ridi if left.distance(NB) <= right.distance(NB) else nidi

    out = []
    for e in graph.get("edges", []):
        f, t = e.get("from"), e.get("to")
        if f != ridi and t != ridi:
            out.append(e)
            continue
        other = t if f == ridi else f
        piece = None
        did = e.get("door_id")
        if did and did in doors_by_id and doors_by_id[did].get("position"):
            piece = _which(doors_by_id[did]["position"])
        if piece is None:
            piece = _piece_for_neighbor(other)
        nf = piece if f == ridi else f
        nt = piece if t == ridi else t
        if nf == nt:
            continue
        out.append({**e, "from": nf, "to": nt})
    out.append({"from": ridi, "to": nidi, "via": "open", "door_id": None})  # 두 조각 open 연결
    graph["edges"] = out

    # 조각별 door_ids/window_ids 재집계
    for d in graph.get("doors", []):
        for c in (d.get("connects") or []):
            if str(c) in (rid, nid) and d.get("id"):
                rooms[str(c)]["door_ids"].append(d["id"])
    for w in graph.get("windows", []):
        b = w.get("belongs_to")
        if str(b) in (rid, nid):
            rm = rooms[str(b)]
            if w.get("id"):
                rm["window_ids"].append(w["id"])
            rm["n_windows"] += 1
            rm["has_window"] = True

    graph["n_rooms"] = len(rooms)
    graph["n_edges"] = len(graph["edges"])
    return graph, None
