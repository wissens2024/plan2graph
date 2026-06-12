"""semantic_fill — 의미 추론으로 데이터 공백 채우기 (재사용 함수, 단일 소스).

ADR-0006: 있는 데이터에서 없는 것을 의미적으로 추론. 두 축:
  (a) 척도 — 여닫이문 폭(표준 800mm) 앵커 → mm/px.
  (b) 역할별 가구/기구 규칙 배치(벽에 붙여, 문 반대쪽, 폴리곤 내부).

이 모듈은 **순수 함수**만 둔다(특정 그래프·경로 의존 없음). GUI·CLI·엔진 렌더가
공유한다. 데모(단일 그래프 골라 렌더)는 scripts/semantic_fill.py가 이걸 import.
"""
from __future__ import annotations

import math
import statistics

# 표준 한국 실내 여닫이문 폭(mm) — 척도 앵커
HINGED_DOOR_MM = 800.0
NAIVE_DOOR_MM = 900.0

# 표준 가구/기구 치수 (가로 w_m × 세로 h_m, 미터)
FIX_SIZES = {
    "bed": (1.5, 2.0), "wardrobe": (0.6, 1.2), "sofa": (2.0, 0.9), "tv": (1.2, 0.2),
    "table": (1.2, 0.8), "counter": (2.4, 0.6), "fridge": (0.7, 0.7), "toilet": (0.4, 0.7),
    "basin": (0.5, 0.4), "shower": (0.9, 0.9), "shoecab": (0.3, 1.0),
}
FIX_LABEL = {
    "bed": "침대", "wardrobe": "옷장", "sofa": "소파", "tv": "TV", "table": "식탁",
    "counter": "싱크/레인지", "fridge": "냉장고", "toilet": "변기", "basin": "세면",
    "shower": "샤워", "shoecab": "신발장",
}


# ── 1) 척도 추론 ─────────────────────────────────────────────────────────────
def infer_scale(g: dict) -> dict:
    """여닫이문 폭 중앙값을 800mm에 앵커 → mm/px. 문 없으면 ValueError."""
    doors = g.get("doors") or []
    hinged = []
    for d in doors:
        if d.get("subtype") != "여닫이문" or d.get("is_entrance"):
            continue
        w = d.get("width_px")
        if not w or w < 30.0:
            continue
        hinged.append(float(w))
    if not hinged:
        raise ValueError("여닫이문(표준) 없음 — 척도 추론 불가")
    med = statistics.median(hinged)
    scale = HINGED_DOOR_MM / med
    all_w = [float(d["width_px"]) for d in doors if d.get("width_px")]
    med_all = statistics.median(all_w) if all_w else med
    return {
        "hinged_px": sorted(round(x, 1) for x in hinged), "median_hinged_px": med,
        "scale_mm_per_px": scale, "naive_median_all_px": med_all,
        "naive_scale_mm_per_px": NAIVE_DOOR_MM / med_all,
    }


def room_area_m2(area_px: float, scale_mm_per_px: float) -> float:
    return area_px * (scale_mm_per_px / 1000.0) ** 2


# ── 2) 폴리곤 기하 유틸 ──────────────────────────────────────────────────────
def poly_edges(poly):
    out, n = [], len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        if L < 1e-6:
            continue
        out.append((a, b, L, abs(dy) <= abs(dx)))
    return out


def pt_in_poly(pt, poly):
    from plan2graph import cadrender
    return cadrender._pt_in_poly(pt, poly)


def edge_midpoint(a, b):
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def inward_normal(a, b, poly):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy) or 1.0
    n1, n2 = (-dy / L, dx / L), (dy / L, -dx / L)
    mid = edge_midpoint(a, b)
    p1 = (mid[0] + n1[0] * 4.0, mid[1] + n1[1] * 4.0)
    return n1 if pt_in_poly(p1, poly) else n2


def rects_overlap(r1, r2, pad=0.0):
    x1, y1, w1, h1 = r1
    x2, y2, w2, h2 = r2
    return not (x1 + w1 + pad <= x2 or x2 + w2 + pad <= x1
               or y1 + h1 + pad <= y2 or y2 + h2 + pad <= y1)


def place_against_wall(poly, edge, size_px, door_pos, placed):
    a, b, L, is_h = edge
    nx, ny = inward_normal(a, b, poly)
    span, depth = size_px
    if L < span * 0.6:
        return None
    ex, ey = (b[0] - a[0]) / L, (b[1] - a[1]) / L
    da = math.hypot(a[0] - door_pos[0], a[1] - door_pos[1])
    db = math.hypot(b[0] - door_pos[0], b[1] - door_pos[1])
    far = a if da >= db else b
    for frac in (0.1, 0.3, 0.5, 0.7, 0.9):
        d_along = span / 2.0 + frac * max(0.0, (L - span))
        base = far if far is a else b
        s = 1.0 if base is a else -1.0
        cx_wall = base[0] + ex * d_along * s
        cy_wall = base[1] + ey * d_along * s
        cx = cx_wall + nx * (depth / 2.0 + 1.0)
        cy = cy_wall + ny * (depth / 2.0 + 1.0)
        w, h = (span, depth) if is_h else (depth, span)
        bx, by = cx - w / 2.0, cy - h / 2.0
        corners = [(bx, by), (bx + w, by), (bx, by + h), (bx + w, by + h), (cx, cy)]
        if not all(pt_in_poly(c, poly) for c in corners):
            continue
        cand = (bx, by, w, h)
        if any(rects_overlap(cand, p, pad=2.0) for p in placed):
            continue
        return cand
    return None


def longest_edges(poly, *, horizontal=None):
    es = poly_edges(poly)
    if horizontal is not None:
        es = [e for e in es if e[3] == horizontal]
    return sorted(es, key=lambda e: -e[2])


def m2px(size_m, scale_mm_per_px):
    f = 1000.0 / scale_mm_per_px
    return (size_m[0] * f, size_m[1] * f)


def door_pos_for_room(room, g):
    door_ids = set(room.get("door_ids") or [])
    for d in g.get("doors") or []:
        if d.get("id") in door_ids and d.get("position"):
            return tuple(d["position"])
    return tuple(room.get("centroid") or (0, 0))


# ── 3) 역할별 기구 배치 규칙 ─────────────────────────────────────────────────
def place_fixtures_for_room(room, g, scale):
    """역할별 규칙으로 기구 FixtureG 리스트 생성."""
    from plan2graph.cadrender import FixtureG
    role = room.get("role") or room.get("base") or "기타"
    poly = [tuple(p) for p in (room.get("polygon") or [])]
    if len(poly) < 3:
        return []
    door = door_pos_for_room(room, g)
    placed, out = [], []

    def add(cls, edge):
        wpx, hpx = m2px(FIX_SIZES[cls], scale)
        bbox = place_against_wall(poly, edge, (max(wpx, hpx), min(wpx, hpx)), door, placed)
        if bbox:
            placed.append(bbox)
            out.append(FixtureG(cls=cls, bbox=bbox))
            return True
        return False

    edges = longest_edges(poly)
    if role == "거실":
        if edges:
            add("sofa", edges[0])
        same = longest_edges(poly, horizontal=bool(edges and edges[0][3]))
        if len(same) >= 2:
            add("tv", same[1])
        elif len(edges) >= 2:
            add("tv", edges[1])
        for e in edges[1:]:
            if add("table", e):
                break
    elif role in ("안방", "침실"):
        if edges:
            add("bed", edges[0])
        for e in edges[1:]:
            if add("wardrobe", e):
                break
    elif role == "주방":
        if edges:
            add("counter", edges[0])
        for e in edges[1:]:
            if add("fridge", e):
                break
    elif role in ("화장실", "욕실", "전용욕실", "전용화장실"):
        if edges:
            add("toilet", edges[0])
        for e in edges[1:]:
            if add("basin", e):
                break
        if role in ("욕실", "전용욕실"):
            for e in edges:
                if add("shower", e):
                    break
    elif role == "현관":
        if edges:
            add("shoecab", edges[0])
    return out
