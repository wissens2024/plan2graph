"""geometry-rich graph 추출기 — docs/GEOMETRY_SCHEMA.md 구현.

위상편집 State(=사람 보정 Layer1) + Drawing(검출 원시) → Layer2 geometry-rich graph(JSON).
파생 필드는 전부 여기서 계산(사람이 입력하지 않음). 자동 경로(사람보정 없음)는
init_state(dr) 결과를 그대로 넣으면 된다 — 그래서 gold/auto가 같은 추출기를 공유한다.

채워지는 필드(현 단계):
  room  : polygon, role, area_px/m2, aspect_ratio, centroid,
          perimeter_px, exterior_len_px, has_window, n_windows, fixtures, privacy
  door  : connects[a,b], via, width_px, is_entrance        (orientation=TODO)
  window: room, bbox                                        (wall_seg/방위=TODO)
  edge  : from,to,via, privacy_transition, dist_from_entrance
  wall  : (TODO — 방 폴리곤 경계서 유도)
"""
from __future__ import annotations

import math

import networkx as nx

# role → 동선 위계(KR_CONVENTIONS). connector=service.
PRIVACY = {
    "거실": "public", "주방": "public", "현관": "public", "엘리베이터홀": "public",
    "침실": "private", "안방": "private", "드레스룸": "private", "파우더룸": "private",
    "화장실": "private", "욕실": "private", "전용욕실": "private", "전용화장실": "private",
    "발코니": "service", "실외기실": "service", "복도": "service", "전실": "service",
    "다목적공간": "service",
}


def _aspect_ratio(poly) -> float:
    """최소회전사각형의 장변/단변(≥1). 길쭉할수록 큼."""
    try:
        mrr = poly.minimum_rotated_rectangle
        xs, ys = mrr.exterior.coords.xy
        e = [math.dist((xs[i], ys[i]), (xs[i + 1], ys[i + 1])) for i in range(4)]
        w, h = (e[0] + e[2]) / 2, (e[1] + e[3]) / 2
        lo, hi = sorted((w, h))
        return round(hi / lo, 3) if lo > 1e-6 else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def _exterior_len(poly, others) -> float:
    """방 경계 중 다른 방과 공유하지 않는 길이(=외곽 접면). 근사."""
    try:
        from shapely.ops import unary_union
        bnd = poly.exterior
        if not others:
            return round(bnd.length, 1)
        shared = bnd.intersection(unary_union([o.buffer(2.0) for o in others]))
        return round(max(0.0, bnd.length - shared.length), 1)
    except Exception:  # noqa: BLE001
        return round(poly.exterior.length, 1)


def _assign(dr, rooms_xy, attr, gap=40.0):
    """dr.<attr>(windows/objects) 각 원소를 가장 가까운 방 id에 귀속 {nid: [elem...]}."""
    from shapely.geometry import Point
    out = {nid: [] for nid, _ in rooms_xy}
    for el in getattr(dr, attr, []):
        if not el.centroid:
            continue
        p = Point(*el.centroid)
        cand = sorted(((poly.distance(p), nid) for nid, poly in rooms_xy), key=lambda t: t[0])
        if cand and cand[0][0] <= gap:
            out[cand[0][1]].append(el)
    return out


def _door_width(dr, ca, cb, gap=80.0):
    """엣지(두 방 중심 ca,cb) 사이 문의 폭(px). 가장 가까운 dr.door의 bbox 단변."""
    mx, my = (ca[0] + cb[0]) / 2, (ca[1] + cb[1]) / 2
    best, bd = None, 1e18
    for d in dr.doors:
        if not d.centroid:
            continue
        dd = math.dist(d.centroid, (mx, my))
        if dd < bd:
            best, bd = d, dd
    if best is None or bd > gap or not best.bbox or len(best.bbox) != 4:
        return None
    _x, _y, w, h = best.bbox          # COCO [x,y,w,h] — 문 폭=단변
    return round(min(abs(w), abs(h)), 1)


def build(state, dr) -> dict:
    """State(+dr) → geometry-rich graph dict (docs/GEOMETRY_SCHEMA.md)."""
    sc = getattr(dr, "scale", None)
    rooms_xy = [(nid, n.polygon) for nid, n in state.nodes.items() if n.polygon is not None]
    polys = [p for _, p in rooms_xy]
    win_by = _assign(dr, rooms_xy, "windows", gap=40.0)
    fix_by = _assign(dr, rooms_xy, "objects", gap=10.0)

    rooms = {}
    for i, (nid, poly) in enumerate(rooms_xy):
        n = state.nodes[nid]
        others = polys[:i] + polys[i + 1:]
        nwin = len(win_by.get(nid, []))
        rooms[nid] = {
            "role": n.role,
            "centroid": [round(n.cx, 1), round(n.cy, 1)],
            "area_px": round(n.area_px, 1),
            "area_m2": (round(n.area_px * sc * sc, 2) if sc else None),
            "aspect_ratio": _aspect_ratio(poly),
            "perimeter_px": round(poly.exterior.length, 1),
            "exterior_len_px": _exterior_len(poly, others),
            "has_window": nwin > 0,
            "n_windows": nwin,
            "fixtures": [o.class_name.replace("객체_", "") for o in fix_by.get(nid, [])],
            "privacy": PRIVACY.get(n.role, "other"),
            "polygon": [[round(x, 1), round(y, 1)] for x, y in poly.exterior.coords],
        }

    # 위상 그래프(거리 계산용) + 엣지 상세
    G = nx.Graph()
    G.add_nodes_from(rooms)
    edges = []
    for e in state.edges:
        a, b = e["a"], e["b"]
        if a not in rooms or b not in rooms:
            continue
        G.add_edge(a, b)
        edges.append({"from": a, "to": b, "via": e.get("via"),
                      "privacy_transition": f"{rooms[a]['privacy']}_to_{rooms[b]['privacy']}"})

    # 현관에서 BFS 거리
    ent = [nid for nid, r in rooms.items() if r["role"] == "현관"]
    dist = {}
    for s in ent:
        for tgt, d in nx.single_source_shortest_path_length(G, s).items():
            dist[tgt] = min(dist.get(tgt, 1e9), d)
    for nid, r in rooms.items():
        r["dist_from_entrance"] = (int(dist[nid]) if nid in dist else None)
    for e in edges:
        e["dist_from_entrance"] = rooms[e["from"]].get("dist_from_entrance")

    # door 상세(via=door 엣지에 폭·진입 표시)
    doors = []
    for e in edges:
        if e["via"] != "door":
            continue
        a, b = e["from"], e["to"]
        doors.append({
            "connects": [a, b],
            "width_px": _door_width(dr, rooms[a]["centroid"], rooms[b]["centroid"]),
            "is_entrance": rooms[a]["role"] == "현관" or rooms[b]["role"] == "현관",
            # "orientation": TODO(arc 폴리곤서 스윙)
        })

    windows = []
    for nid, ws in win_by.items():
        for w in ws:
            windows.append({"room": nid, "bbox": [round(v, 1) for v in (w.bbox or [])]})

    return {
        "plan_id": state.plan_id, "house": state.house,
        "scale_mm_per_px": round(sc * 1000, 4) if sc else None,
        "n_rooms": len(rooms), "n_edges": len(edges),
        "rooms": rooms, "edges": edges, "doors": doors, "windows": windows,
        # "walls": TODO(방 폴리곤 경계서 유도 — 공유변=내벽/외곽=외벽)
    }


if __name__ == "__main__":
    import sys
    from pathlib import Path as _P
    _root = _P(__file__).resolve().parents[2]
    for _p in (str(_root), str(_root / "src")):
        if _p not in sys.path:
            sys.path.insert(0, _p)
    import config
    from plan2graph import topoedit as T

    for rp in T.scan_dir(config.DATA_DIR / "raw" / "linked_demo"):
        dr, _ = T.load_plan(rp)
        units = T.segment_units(dr)
        if not units:
            continue
        st = T.init_state(dr, rp.plan_id, rp.house, units[0])
        g = build(st, dr)
        print(f"{g['plan_id']} house={g['house']} rooms={g['n_rooms']} edges={g['n_edges']} "
              f"doors={len(g['doors'])} windows={len(g['windows'])} scale={g['scale_mm_per_px']}")
        sample = next(iter(g["rooms"].values()))
        print("  room[0]:", {k: sample[k] for k in
              ("role", "area_m2", "aspect_ratio", "has_window", "n_windows",
               "fixtures", "privacy", "dist_from_entrance")})
        break
