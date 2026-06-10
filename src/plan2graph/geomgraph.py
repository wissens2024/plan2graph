"""geometry-rich graph 추출기 — docs/GEOMETRY_SCHEMA.md 구현 (G-라인, schema g-0.3).

위상편집 State(=Layer1, 자동 init_state 또는 사람 보정) + Drawing(검출 원시)
  → Layer2 geometry-rich graph(JSON). 파생 필드는 전부 여기서 계산(사람 입력 아님).
자동 경로(사람보정 없음)는 topoedit.init_state(dr)를 그대로 넣으면 된다 — gold/auto가
같은 추출기를 공유한다(프로그램이 1차 완전 SVG/그래프를 낸다는 원칙).

이 추출기 = T-라인(schema.py, layout.nodes)과 절대 안 섞임(ADR-0002). 여기는 rooms 스키마.

──────────────────────────────────────────────────────────────────────────────
schema g-0.3 — GPT 제안 반영(벽·문방향·창귀속·외곽접촉) + 검증기/사유 + meta. 이전 0.2 대비:
  room  : +bbox_px, centroid_norm, touches_exterior, wall_ids, door_ids, window_ids, base
  wall  : 신규(방 폴리곤 공유변=interior / 외곽=exterior, openings) ← 이전 TODO
  door  : +id, position, polygon, bbox_px, width_m, subtype, orientation(arc), on_wall ← orientation 이전 TODO
  window: +id, belongs_to, position, width_px/m, on_wall, orientation_deg ← wall_seg/방위 이전 TODO
  edge  : +door_id
  top   : +schema_version, meta(status/reason — 회계용), walls, bbox_px, validation
호환 보존: 방 dict 키=정수 node id, from/to/connects=정수(GUI _disp_id·집합비교 의존).
  GUI(topoedit)·train_geom·geom_correct가 읽는 기존 키는 전부 유지하고 가산만 한다.
"""
from __future__ import annotations

import math

import networkx as nx

SCHEMA_VERSION = "g-0.3"

# role → 동선 위계(KR_CONVENTIONS). connector·공용부=service. topoedit.ROLES 전부 커버
#   (역할 누락 시 'other'로 떨어지던 구멍 메움 — 역할미상 사유는 validate가 별도 표시).
PRIVACY = {
    "거실": "public", "주방": "public", "현관": "public", "엘리베이터홀": "public",
    "침실": "private", "안방": "private", "드레스룸": "private", "파우더룸": "private",
    "화장실": "private", "욕실": "private", "전용욕실": "private", "전용화장실": "private",
    "발코니": "service", "실외기실": "service", "복도": "service", "전실": "service",
    "다목적공간": "service", "계단실": "service", "엘리베이터": "service",
    "구조물": "structure", "실외": "exterior",
}
CONNECTOR_ROLES = ("복도", "전실")
# 단일세대 필수 5요소 — **역할 패밀리**로 본다(세분역할 흡수). suggest_roles가 화장실→욕실/
# 전용욕실, 침실→안방 등으로 세분하므로 정확매칭이면 거짓 '필수공간없음'이 대량 발생(검증 버그).
ESSENTIAL_FAMILIES = {
    "현관": {"현관"},
    "거실": {"거실"},
    "침실": {"침실", "안방"},
    "주방": {"주방"},
    "화장실": {"화장실", "욕실", "전용욕실", "전용화장실"},
}
MIN_ROOMS = 5


# ─────────────────────────────────────────────────────────────────────────────
# 기하 헬퍼 (순수 — shapely)
# ─────────────────────────────────────────────────────────────────────────────
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


def _bbox(poly):
    """[x, y, w, h] (정수 반올림)."""
    x0, y0, x1, y1 = poly.bounds
    return [round(x0, 1), round(y0, 1), round(x1 - x0, 1), round(y1 - y0, 1)]


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


def _segments(poly):
    """폴리곤 외곽 → 인접 꼭짓점 선분 [(p1, p2), ...]."""
    cs = list(poly.exterior.coords)
    return [(cs[i], cs[i + 1]) for i in range(len(cs) - 1)]


def _seg_mid_ang(seg):
    (x1, y1), (x2, y2) = seg
    return ((x1 + x2) / 2, (y1 + y2) / 2), math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180.0


def _seg_close(s1, s2, tol=35.0, ang_tol=20.0) -> bool:
    """두 선분이 같은 물리 벽의 두 반쪽인가 — 중점 근접 + 거의 평행."""
    m1, a1 = _seg_mid_ang(s1)
    m2, a2 = _seg_mid_ang(s2)
    da = abs(a1 - a2)
    da = min(da, 180 - da)
    return math.dist(m1, m2) <= tol and da <= ang_tol


def _derive_walls(rooms_xy, scale, tol=18.0, cover_frac=0.5):
    """방 폴리곤 경계서 벽 유도(검출 안 함). 공유변=interior(두 방)·나머지=exterior(한 방).
    근사: 사람/검출 폴리곤은 벽두께만큼 어긋나므로 buffer(tol)로 공유 판정·중점으로 병합.
    반환 wall dict 리스트(openings는 build에서 문·창 매핑 후 채움)."""
    from shapely.geometry import LineString
    boundaries = [(nid, poly.exterior) for nid, poly in rooms_xy]
    cands = []   # (type, rooms_key, LineString)
    for nid, poly in rooms_xy:
        for p1, p2 in _segments(poly):
            seg = LineString([p1, p2])
            if seg.length < 4:
                continue
            partner, cover = None, 0.0
            for ojd, ob in boundaries:
                if ojd == nid:
                    continue
                inter = seg.intersection(ob.buffer(tol))
                c = inter.length if not inter.is_empty else 0.0
                if c > cover:
                    cover, partner = c, ojd
            if partner is not None and cover >= cover_frac * seg.length:
                cands.append(("interior", frozenset((nid, partner)), seg))
            else:
                cands.append(("exterior", (nid,), seg))
    # interior 병합: 같은 방쌍의 두 반쪽(겹치는 선분)을 하나로(긴 쪽 대표)
    walls, used = [], [False] * len(cands)
    for i, (ty, rms, seg) in enumerate(cands):
        if used[i]:
            continue
        used[i] = True
        if ty == "interior":
            grp = [seg]
            for j in range(i + 1, len(cands)):
                if used[j]:
                    continue
                ty2, rms2, seg2 = cands[j]
                if ty2 == "interior" and rms2 == rms and _seg_close(
                        (seg.coords[0], seg.coords[-1]), (seg2.coords[0], seg2.coords[-1])):
                    grp.append(seg2)
                    used[j] = True
            rep = max(grp, key=lambda s: s.length)
            walls.append(("interior", sorted(rms), rep))
        else:
            walls.append(("exterior", list(rms), seg))
    out = []
    for k, (ty, rms, seg) in enumerate(walls):
        (x1, y1), (x2, y2) = seg.coords[0], seg.coords[-1]
        out.append({
            "id": f"w{k}", "type": ty,
            "segment": [[round(x1, 1), round(y1, 1)], [round(x2, 1), round(y2, 1)]],
            "length_px": round(seg.length, 1),
            "length_m": (round(seg.length * scale, 2) if scale else None),
            "rooms": rms, "openings": [],
        })
    return out


def _seg_dist(segment, pt) -> float:
    """벽 선분([[x1,y1],[x2,y2]])과 점(pt) 사이 거리."""
    from shapely.geometry import LineString, Point
    return LineString(segment).distance(Point(*pt))


def _nearest_wall(walls, pt, prefer_rooms=None, gap=40.0):
    """점 pt에 가장 가까운 벽 id(gap 이내). prefer_rooms 주면 그 방쌍 벽 우선(동률 깸)."""
    best, bd = None, 1e18
    for w in walls:
        d = _seg_dist(w["segment"], pt)
        if prefer_rooms is not None and set(w["rooms"]) == set(prefer_rooms):
            d -= 1e6        # 같은 방쌍 벽 강한 우선
        if d < bd:
            best, bd = w, d
    if best is None:
        return None
    real = _seg_dist(best["segment"], pt)
    return best["id"] if real <= gap else None


def _wall_normal_deg(segment) -> float:
    """벽 선분 법선 방위(도, 상대값). 창 방향 근사 — 도면 방위(남향) 미상이라 상대."""
    (x1, y1), (x2, y2) = segment
    return round((math.degrees(math.atan2(y2 - y1, x2 - x1)) + 90.0) % 360.0, 1)


def _door_orientation(poly):
    """문 arc 폴리곤 → 여닫이 방향 {hinge, swing_dir_deg, radius_px, confidence}.
    arc(부채꼴)는 hinge(원호 중심)서 호 위 점들이 등거리(R)인 성질로 hinge 추정.
    bbox 사각형(검출 seg 없음)이면 정보 없음 → None(사람확인 플래그는 호출부가 처리)."""
    if poly is None:
        return None
    import statistics
    coords = list(poly.exterior.coords)[:-1]
    if len(coords) < 5:          # 사각형(arc 아님)
        return None
    best = None                  # (cov, hinge, radius)
    for h in coords:
        ds = sorted(math.dist(h, c) for c in coords if c != h)
        far = ds[len(ds) // 2:]
        if not far:
            continue
        m = statistics.fmean(far)
        sd = statistics.pstdev(far)
        cov = sd / m if m > 1e-6 else 1e9
        if best is None or cov < best[0]:
            best = (cov, h, m)
    if best is None or best[0] > 0.25:    # 등거리 아님 → arc로 못 봄
        return None
    cov, hinge, radius = best
    far_pts = [c for c in coords if math.dist(hinge, c) > 0.6 * radius]
    if not far_pts:
        return None
    fx = sum(p[0] for p in far_pts) / len(far_pts)
    fy = sum(p[1] for p in far_pts) / len(far_pts)
    ang = math.degrees(math.atan2(fy - hinge[1], fx - hinge[0])) % 360.0
    return {"hinge": [round(hinge[0], 1), round(hinge[1], 1)],
            "swing_dir_deg": round(ang, 1), "radius_px": round(radius, 1),
            "confidence": "med" if cov < 0.18 else "low"}


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


def _bbox_short(bbox):
    """COCO [x,y,w,h] → 단변(문/창 폭). 없으면 None."""
    if not bbox or len(bbox) != 4:
        return None
    _x, _y, w, h = bbox
    return round(min(abs(w), abs(h)), 1)


def _nearest_door(dr, ca, cb, gap=80.0):
    """엣지(두 방 중심 ca,cb) 사이 가장 가까운 검출 문 Element(폴백용)."""
    mx, my = (ca[0] + cb[0]) / 2, (ca[1] + cb[1]) / 2
    best, bd = None, 1e18
    for d in dr.doors:
        if not d.centroid:
            continue
        dd = math.dist(d.centroid, (mx, my))
        if dd < bd:
            best, bd = d, dd
    return best if (best is not None and bd <= gap) else None


def _door_for_edge(dr, walls, a, b, gap=50.0):
    """엣지(방 a,b)의 검출 문 + on_wall — a,b **공유 내벽 위** 최근접 문을 찾는다.
    방 중심 중점이 아니라 실제 벽 기준이라 문폭·여닫이 측정률이 크게 오른다(문은 벽 위에 있음).
    공유벽 없으면 (None, None) → 호출부가 중점 폴백."""
    from shapely.geometry import LineString, Point
    shared = [w for w in walls if set(w["rooms"]) == {a, b}]
    best_d, best_w, bd = None, None, 1e18
    for w in shared:
        seg = LineString(w["segment"])
        for d in dr.doors:
            if not d.centroid:
                continue
            dist = seg.distance(Point(*d.centroid))
            if dist < bd:
                bd, best_d, best_w = dist, d, w
    if best_d is not None and bd <= gap:
        return best_d, best_w
    return None, (shared[0] if shared else None)


# ─────────────────────────────────────────────────────────────────────────────
# 기타방 역할 추정 강화 (위상·기하 신호) — suggest_roles(OCR·기구·면적) 이후 보강
#   속성(역할)은 조인·신호로 도출(관계만 사람이 선언) 원칙. 한국 아파트 정준 연결공간 식별.
#   보수적: 다중 신호 일치 시만 relabel(오분류 최소화). 역할미상(=기타) 축소가 목적.
# ─────────────────────────────────────────────────────────────────────────────
def enhance_roles_g(g) -> set:
    """build된 그래프(dict)의 base=role='기타' 방을 위상(연결수·이웃역할)+기하(길쭉·외벽·창)로 세분.
    규칙(고신뢰 순): 허브연결=복도 · 외벽+창+거주실인접=발코니 · 안방막다른=드레스룸 ·
    길쭉통과=복도 · 사적공간 사이=전실. role·privacy·is_connector·엣지 privacy_transition 갱신.
    id를 str로 정규화 → 메모리(int 키)·JSON(str 키) 양쪽 강건(build·revalidate 공용)."""
    from collections import defaultdict
    rooms = g.get("rooms", {})
    edges = g.get("edges", [])
    rkeys = {str(k): k for k in rooms}          # str → 원래 키(int/str)
    deg, nbr = {}, defaultdict(list)
    for e in edges:
        a, b = str(e.get("from")), str(e.get("to"))
        if a in rkeys and b in rkeys:
            deg[a] = deg.get(a, 0) + 1
            deg[b] = deg.get(b, 0) + 1
            nbr[a].append(b)
            nbr[b].append(a)
    changed = set()
    for sk, ok in rkeys.items():
        r = rooms[ok]
        if r.get("base") != "기타" or r.get("role") != "기타":
            continue
        d = deg.get(sk, 0)
        ar = r.get("aspect_ratio", 0) or 0
        te = bool(r.get("touches_exterior"))
        nwin = r.get("n_windows", 0) or 0
        fx = r.get("fixtures") or []
        nb = {rooms[rkeys[m]].get("role") for m in nbr.get(sk, [])}
        new = None
        if d >= 3:                                                      # 허브 연결 = 복도
            new = "복도"
        elif te and nwin > 0 and not fx and (nb & {"거실", "침실", "안방", "주방"}):
            new = "발코니"                                              # 외벽+창+거주실 인접
        elif d == 1 and nb == {"안방"} and not fx and nwin == 0:
            new = "드레스룸"                                            # 안방 막다른 부속
        elif ar >= 2.5 and d >= 2:                                      # 길쭉한 통과 = 복도
            new = "복도"
        elif d == 2 and (nb & {"현관", "안방", "욕실", "전용욕실", "드레스룸"}):
            new = "전실"                                                # 사적공간 사이 전실
        if new:
            r["role"] = new
            r["privacy"] = PRIVACY.get(new, "other")
            r["is_connector"] = new in CONNECTOR_ROLES
            changed.add(ok)
    for e in edges:                                                     # privacy_transition 갱신
        a, b = str(e.get("from")), str(e.get("to"))
        if a in rkeys and b in rkeys:
            e["privacy_transition"] = (f"{rooms[rkeys[a]].get('privacy')}_to_"
                                       f"{rooms[rkeys[b]].get('privacy')}")
    return changed


# ─────────────────────────────────────────────────────────────────────────────
# 본체
# ─────────────────────────────────────────────────────────────────────────────
def build(state, dr) -> dict:
    """State(+dr) → geometry-rich graph dict (schema g-0.3, docs/GEOMETRY_SCHEMA.md)."""
    sc = getattr(dr, "scale", None)
    rooms_xy = [(nid, n.polygon) for nid, n in state.nodes.items() if n.polygon is not None]
    polys = [p for _, p in rooms_xy]
    win_by = _assign(dr, rooms_xy, "windows", gap=40.0)
    fix_by = _assign(dr, rooms_xy, "objects", gap=10.0)
    walls = _derive_walls(rooms_xy, sc)

    # 세대 외곽 bbox — 개별 폴리곤 bounds의 합집합 bbox(= unary_union.bounds와 동일).
    #   union을 쓰면 V2V 예측 등 invalid 폴리곤에서 TopologyException → .bounds만 쓰므로 직접 계산(안전).
    if polys:
        _bs = [p.bounds for p in polys]
        ux0 = min(b[0] for b in _bs); uy0 = min(b[1] for b in _bs)
        ux1 = max(b[2] for b in _bs); uy1 = max(b[3] for b in _bs)
    else:
        ux0, uy0, ux1, uy1 = 0.0, 0.0, 1.0, 1.0
    UW, UH = max(ux1 - ux0, 1.0), max(uy1 - uy0, 1.0)

    rooms = {}
    for i, (nid, poly) in enumerate(rooms_xy):
        n = state.nodes[nid]
        others = polys[:i] + polys[i + 1:]
        nwin = len(win_by.get(nid, []))
        ext_len = _exterior_len(poly, others)
        perim = poly.exterior.length
        rooms[nid] = {
            "base": getattr(n, "base", n.role),
            "role": n.role,
            "is_connector": n.role in CONNECTOR_ROLES or getattr(n, "base", "") in CONNECTOR_ROLES,
            "centroid": [round(n.cx, 1), round(n.cy, 1)],
            "centroid_norm": [round((n.cx - ux0) / UW, 4), round((n.cy - uy0) / UH, 4)],
            "bbox_px": _bbox(poly),
            "area_px": round(n.area_px, 1),
            "area_m2": (round(n.area_px * sc * sc, 2) if sc else None),
            "aspect_ratio": _aspect_ratio(poly),
            "perimeter_px": round(perim, 1),
            "exterior_len_px": ext_len,
            "touches_exterior": ext_len > 0.05 * perim,
            "has_window": nwin > 0,
            "n_windows": nwin,
            "fixtures": [o.class_name.replace("객체_", "") for o in fix_by.get(nid, [])],
            "privacy": PRIVACY.get(n.role, "other"),
            "wall_ids": [w["id"] for w in walls if nid in w["rooms"]],
            "door_ids": [],
            "window_ids": [],
            "polygon": [[round(x, 1), round(y, 1)] for x, y in poly.exterior.coords],
        }

    # 위상 그래프(거리 계산용) + 엣지
    G = nx.Graph()
    G.add_nodes_from(rooms)
    edges = []
    for e in state.edges:
        a, b = e["a"], e["b"]
        if a not in rooms or b not in rooms:
            continue
        G.add_edge(a, b)
        edges.append({"from": a, "to": b, "via": e.get("via"),
                      "door_id": None,
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

    # door 상세(via=door 엣지 ↔ 검출 문 기하 결합) — id·폭·여닫이·on_wall
    doors = []
    for ei, e in enumerate(edges):
        if e["via"] != "door":
            continue
        a, b = e["from"], e["to"]
        ca, cb = rooms[a]["centroid"], rooms[b]["centroid"]
        d, dwall = _door_for_edge(dr, walls, a, b)       # 공유벽 위 문(측정률↑)
        if d is None:
            d = _nearest_door(dr, ca, cb)                # 폴백: 중심 중점 최근접
        pos = ([round((ca[0] + cb[0]) / 2, 1), round((ca[1] + cb[1]) / 2, 1)]
               if d is None or not d.centroid
               else [round(d.centroid[0], 1), round(d.centroid[1], 1)])
        wpx = _bbox_short(d.bbox) if d is not None else None
        did = f"d{len(doors)}"
        on_wall = (dwall["id"] if dwall is not None
                   else _nearest_wall(walls, pos, prefer_rooms=(a, b)))
        door = {
            "id": did, "connects": [a, b], "via": "door", "position": pos,
            "polygon": ([[round(x, 1), round(y, 1)] for x, y in d.polygon.exterior.coords]
                        if d is not None and d.polygon is not None else None),
            "bbox_px": (d.bbox if d is not None else None),
            "width_px": wpx,
            "width_m": (round(wpx * sc, 2) if (wpx and sc) else None),
            "subtype": (d.subtype if d is not None else None),
            "orientation": (_door_orientation(d.polygon) if d is not None else None),
            "needs_orientation_review": (d is None or d.polygon is None
                                         or _door_orientation(d.polygon) is None),
            "on_wall": on_wall,
            "is_entrance": rooms[a]["role"] == "현관" or rooms[b]["role"] == "현관",
        }
        doors.append(door)
        e["door_id"] = did
        rooms[a]["door_ids"].append(did)
        rooms[b]["door_ids"].append(did)
        if on_wall:
            for w in walls:
                if w["id"] == on_wall:
                    w["openings"].append(did)

    # window 상세 — 방 귀속·on_wall(외벽)·방위
    windows = []
    for nid, ws in win_by.items():
        for w in ws:
            wid = f"win{len(windows)}"
            pos = ([round(w.centroid[0], 1), round(w.centroid[1], 1)] if w.centroid else None)
            on_wall = _nearest_wall(walls, pos, prefer_rooms=(nid,)) if pos else None
            seg = next((wl["segment"] for wl in walls if wl["id"] == on_wall), None)
            wpx = _bbox_short(w.bbox)
            windows.append({
                "id": wid, "belongs_to": nid, "position": pos,
                "bbox_px": w.bbox,
                "width_px": wpx, "width_m": (round(wpx * sc, 2) if (wpx and sc) else None),
                "on_wall": on_wall,
                "orientation_deg": (_wall_normal_deg(seg) if seg else None),
            })
            rooms[nid]["window_ids"].append(wid)
            if on_wall:
                for wl in walls:
                    if wl["id"] == on_wall:
                        wl["openings"].append(wid)

    g = {
        "schema_version": SCHEMA_VERSION,
        "plan_id": state.plan_id, "house": state.house,
        "scale_mm_per_px": round(sc * 1000, 4) if sc else None,
        "bbox_px": [round(ux0, 1), round(uy0, 1), round(UW, 1), round(UH, 1)],
        "n_rooms": len(rooms), "n_edges": len(edges),
        "n_walls": len(walls), "n_doors": len(doors), "n_windows": len(windows),
        "rooms": rooms, "edges": edges, "walls": walls, "doors": doors, "windows": windows,
    }
    enhance_roles_g(g)                      # 기타방 위상·기하 역할 보강(역할미상↓) — 검증 전
    g["validation"] = validate(g)
    v = g["validation"]
    g["meta"] = {
        "schema_version": SCHEMA_VERSION,
        "house_type": state.house,
        "scale_mm_per_px": g["scale_mm_per_px"],
        "status": "success" if v["passed"] else "quarantine",
        "reason": ",".join(v["reasons"]),
        "n_rooms": len(rooms), "n_edges": len(edges),
        "n_walls": len(walls), "n_doors": len(doors), "n_windows": len(windows),
    }
    return g


def validate(g: dict) -> dict:
    """G-라인 스키마 검증(T-라인 rules.validate의 G판). 회계 사유코드 산출.
    hard(→quarantine): 면적없음·위상단절·방부족·필수공간없음. soft(→warning): 역할미상·문폭없음·현관없음.
    반환 {passed, reasons:[hard코드], warnings:[soft코드]}."""
    rooms = g.get("rooms", {})
    edges = g.get("edges", [])
    reasons, warnings = [], []

    if any((r.get("area_px") or 0) <= 0 or not r.get("polygon") for r in rooms.values()):
        reasons.append("면적없음")
    if len(rooms) < MIN_ROOMS:
        reasons.append("방부족")

    roles = {r.get("role") for r in rooms.values()}
    if any(not (roles & fam) for fam in ESSENTIAL_FAMILIES.values()):
        reasons.append("필수공간없음")

    # 위상단절 — 폴리곤 방들이 한 덩어리로 연결되는가(엣지 기준).
    # id를 str로 정규화: JSON 로드 시 rooms 키(str)↔edge from/to(int)가 어긋나
    # 전부 비연결로 오판되던 버그 방지(메모리 int·JSON str 양쪽 강건).
    if rooms:
        rkeys = {str(k) for k in rooms}
        G = nx.Graph()
        G.add_nodes_from(rkeys)
        for e in edges:
            a, b = str(e["from"]), str(e["to"])
            if a in rkeys and b in rkeys:
                G.add_edge(a, b)
        if G.number_of_nodes() and nx.number_connected_components(G) > 1:
            reasons.append("위상단절")

    # soft warning (보정필요 — 사람이 할 진짜 일)
    if any(r.get("privacy") == "other" or r.get("role") in (None, "기타") for r in rooms.values()):
        warnings.append("역할미상")
    if not any(r.get("role") == "현관" for r in rooms.values()):
        warnings.append("현관없음")
    for e in edges:
        if e.get("via") not in ("door", "open"):
            warnings.append("via미상")
            break

    # info (정보성 — 사용 차단 아님. 측정 결손이지 데이터 결함/사람 일 아님)
    info = []
    if any(d.get("via") == "door" and d.get("width_px") is None for d in g.get("doors", [])):
        info.append("문폭없음")

    return {"passed": len(reasons) == 0, "reasons": reasons,
            "warnings": warnings, "info": info}


if __name__ == "__main__":
    import sys
    from pathlib import Path as _P
    _root = _P(__file__).resolve().parents[2]
    for _p in (str(_root), str(_root / "src")):
        if _p not in sys.path:
            sys.path.insert(0, _p)
    from plan2graph import topoedit as T
    from plan2graph import aihub_source as A   # 정식 소스(zip 코퍼스, 서버 115). linked_demo 폐기.

    recs = [r for r in A.scan(house="APT") if "STR" in r["labels"]]
    if not recs:
        print("코퍼스 없음 — 서버 115(raw zip)에서 실행. 정식 빌드: "
              "scripts/build_gline_auto.py --source aihub")
        raise SystemExit
    dr, _ = A.load(recs[0])
    units = T.segment_units(dr)
    st = T.init_state(dr, recs[0]["plan_id"], recs[0]["house"], units[0] if units else None)
    g = build(st, dr)
    print(f"{g['plan_id']} house={g['house']} rooms={g['n_rooms']} edges={g['n_edges']} "
          f"walls={g['n_walls']} doors={g['n_doors']} windows={g['n_windows']} "
          f"scale={g['scale_mm_per_px']} status={g['meta']['status']} "
          f"reason={g['meta']['reason']}")
    sample = next(iter(g["rooms"].values()))
    print("  room[0]:", {k: sample[k] for k in
          ("role", "area_m2", "aspect_ratio", "touches_exterior", "has_window",
           "n_windows", "fixtures", "privacy", "wall_ids", "dist_from_entrance")})
    if g["doors"]:
        print("  door[0]:", g["doors"][0])
