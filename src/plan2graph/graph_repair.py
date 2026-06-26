"""출력 repair (graph-level, 범용) — 생성 그래프를 '도면답게' 보정. 재학습 0.

전략(조립):
  1) 각 방 직각화(rectify_diagonals: 대각선→L코너)
  2) 전역 코너 축-스냅: 모든 방 코너의 x/y를 tol 내 클러스터로 병합 → 인접 방 정렬
     (틈 닫힘→pieces·fill↑, 근축 변→정확 축=사선 제거)
  3) 스냅 후 재직각화 + collinear/중복 코너 제거
반환: (g, log)  — g는 in-place 수정.
"""
from __future__ import annotations
from plan2graph.wallcycle_codec import rectify_diagonals
from shapely.geometry import Polygon, MultiPolygon, box
from shapely.ops import unary_union

_BIG = 1e7


def _largest(P):
    if isinstance(P, MultiPolygon):
        P = max(P.geoms, key=lambda q: q.area) if not P.is_empty else P
    return P


def _declash_wall(rooms, max_pass=4):
    """공유벽 분할: 겹친 방 쌍을 겹침 중앙에 칸막이 긋고 양쪽 대칭 트림(슬리버 방지)."""
    keys = []
    polys = {}
    for k, r in rooms.items():
        p = r.get("polygon") or []
        if len(p) < 3:
            continue
        try:
            P = Polygon(p).buffer(0)
        except Exception:
            continue
        if not P.is_empty and P.area > 1:
            polys[k] = P
            keys.append(k)
    for _ in range(max_pass):
        changed = False
        order = sorted(keys, key=lambda k: polys[k].area, reverse=True)
        for i in range(len(order)):
            for j in range(i + 1, len(order)):
                a, b = order[i], order[j]
                PA, PB = polys[a], polys[b]
                inter = PA.intersection(PB)
                if inter.is_empty or inter.area <= 1:
                    continue
                changed = True
                ca, cb = PA.centroid, PB.centroid
                ox0, oy0, ox1, oy1 = inter.bounds
                if abs(ca.x - cb.x) >= abs(ca.y - cb.y):   # 좌우 배치 → 수직 칸막이
                    cut = (ox0 + ox1) / 2.0
                    left = box(-_BIG, -_BIG, cut, _BIG)
                    right = box(cut, -_BIG, _BIG, _BIG)
                    if ca.x <= cb.x:
                        PA, PB = PA.intersection(left), PB.intersection(right)
                    else:
                        PA, PB = PA.intersection(right), PB.intersection(left)
                else:                                       # 상하 배치 → 수평 칸막이
                    cut = (oy0 + oy1) / 2.0
                    low = box(-_BIG, -_BIG, _BIG, cut)
                    high = box(-_BIG, cut, _BIG, _BIG)
                    if ca.y <= cb.y:
                        PA, PB = PA.intersection(low), PB.intersection(high)
                    else:
                        PA, PB = PA.intersection(high), PB.intersection(low)
                polys[a], polys[b] = _largest(PA.buffer(0)), _largest(PB.buffer(0))
        if not changed:
            break
    for k in keys:
        P = _largest(polys[k])
        if P.is_empty or P.area <= 1:
            continue
        rooms[k]["polygon"] = [[float(x), float(y)] for x, y in P.exterior.coords[:-1]]


def _make_valid(poly):
    """자기교차(selfint) 폴리곤 → buffer(0)로 유효화, 최대 조각 외곽 반환."""
    try:
        P = Polygon(poly)
        if P.is_valid and P.area > 0:
            return poly
        F = P.buffer(0)
        if F.is_empty:
            return poly
        if isinstance(F, MultiPolygon):
            F = max(F.geoms, key=lambda q: q.area)
        return [[float(x), float(y)] for x, y in F.exterior.coords[:-1]]
    except Exception:
        return poly


def _cluster_snap(values, tol):
    """1D 그리디 클러스터링: 정렬 후 tol 내 연속값을 한 그룹으로 → value->대표값(그룹평균)."""
    vs = sorted(set(values))
    rep = {}
    if not vs:
        return rep
    group = [vs[0]]
    for v in vs[1:]:
        if v - group[-1] <= tol:
            group.append(v)
        else:
            c = sum(group) / len(group)
            for g in group:
                rep[g] = c
            group = [v]
    c = sum(group) / len(group)
    for g in group:
        rep[g] = c
    return rep


def _dedup(poly):
    """연속 중복점 + collinear 중간점 제거 (직각 폴리곤 정리)."""
    pts = [(float(p[0]), float(p[1])) for p in poly]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    # 연속 중복 제거
    out = []
    for p in pts:
        if not out or (abs(p[0] - out[-1][0]) > 1e-6 or abs(p[1] - out[-1][1]) > 1e-6):
            out.append(p)
    if len(out) > 1 and out[0] == out[-1]:
        out = out[:-1]
    n = len(out)
    if n < 3:
        return [list(p) for p in out]
    # collinear 중간점 제거 (직각이라 같은 x 또는 같은 y 3연속)
    keep = []
    for i in range(n):
        a, b, c = out[(i - 1) % n], out[i], out[(i + 1) % n]
        collinear = (abs(a[0] - b[0]) < 1e-6 and abs(b[0] - c[0]) < 1e-6) or \
                    (abs(a[1] - b[1]) < 1e-6 and abs(b[1] - c[1]) < 1e-6)
        if not collinear:
            keep.append(b)
    return [list(p) for p in (keep if len(keep) >= 3 else out)]


def _declash(rooms):
    """겹치는 방 제거: 큰 방 우선, 각 방에서 이미 점유된 영역을 difference(clip)."""
    items = [(k, r) for k, r in rooms.items() if (r.get("polygon") and len(r["polygon"]) >= 3)]
    polys = {}
    for k, r in items:
        try:
            P = Polygon(r["polygon"]).buffer(0)
            if not P.is_empty and P.area > 1:
                polys[k] = P
        except Exception:
            pass
    claimed = None
    for k, _ in sorted(items, key=lambda kr: polys.get(kr[0]).area if kr[0] in polys else 0, reverse=True):
        if k not in polys:
            continue
        P = polys[k]
        if claimed is not None:
            P = P.difference(claimed)
        if P.is_empty or P.area <= 1:
            continue
        if isinstance(P, MultiPolygon):
            P = max(P.geoms, key=lambda q: q.area)
        rooms[k]["polygon"] = [[float(x), float(y)] for x, y in P.exterior.coords[:-1]]
        claimed = P if claimed is None else unary_union([claimed, P])


def _rebuild_walls(g):
    """repair된 방 폴리곤 변에서 벽 재생성 — 렌더가 repair를 반영하게(공유변=내벽·단독변=외벽).
    필수: from_geomgraph는 g['walls']에서 그리므로, 방만 고치면 벽이 옛날 그대로 남음."""
    segs = {}
    for r in (g.get("rooms") or {}).values():
        poly = r.get("polygon") or []
        n = len(poly)
        for i in range(n):
            a = (round(float(poly[i][0]), 1), round(float(poly[i][1]), 1))
            b = (round(float(poly[(i + 1) % n][0]), 1), round(float(poly[(i + 1) % n][1]), 1))
            if a == b:
                continue
            key = tuple(sorted([a, b]))
            segs[key] = segs.get(key, 0) + 1
    g["walls"] = [{"segment": [list(a), list(b)],
                   "type": "interior" if cnt >= 2 else "exterior",
                   "openings": []}
                  for (a, b), cnt in segs.items()]


def repair_graph(g, tol=3.0, snap=False, declash=False, drop_bad=True):
    rooms = g.get("rooms") or {}
    log = {"tol": tol, "snap": snap, "declash": declash, "drop_bad": drop_bad}
    # 1) 직각화 → selfint 유효화 (검증을 마지막에: rectify 후 make_valid 순서)
    for r in rooms.values():
        if r.get("polygon"):
            r["polygon"] = _make_valid(rectify_diagonals(r["polygon"]))
    # 1a) degenerate(고칠 수 없는 무효/미세 방=아티팩트) 드롭 → 잔여 selfint 1개가 그래프 죽이는 것 방지
    #     ★현관 등 핵심 역할은 폴리곤만 유효하면(≥3 verts·valid) 보존 — 드롭 시 egress(피난동선) 앵커 소실.
    if drop_bad:
        CRITICAL_ROLES = {"현관"}
        drop = []
        for k, r in rooms.items():
            p = r.get("polygon") or []
            nuniq = len(set(map(tuple, p)))
            ok = False
            if nuniq >= 4:
                try:
                    P = Polygon(p)
                    ok = P.is_valid and P.area > 4
                except Exception:
                    ok = False
            if not ok and r.get("role") in CRITICAL_ROLES and nuniq >= 3:
                try:
                    ok = Polygon(p).is_valid and Polygon(p).area > 0   # 현관 보존: 미세해도 노드 유지
                except Exception:
                    ok = False
            if not ok:
                drop.append(k)
        for k in drop:
            del rooms[k]
        log["dropped"] = len(drop)
        log["protected"] = [k for k, r in rooms.items() if r.get("role") in CRITICAL_ROLES]
    # 1b) overlap 제거 (옵션): "wall"=공유벽 분할(대칭 트림) / "clip"=큰방 우선 잘라내기
    if declash:
        mode = "wall" if declash is True else declash
        if mode == "wall":
            _declash_wall(rooms)
        else:
            _declash(rooms)
        for r in rooms.values():
            if r.get("polygon"):
                r["polygon"] = _make_valid(rectify_diagonals(r["polygon"]))
    # 2) 전역 코너 축-스냅 (옵션 — 방 붕괴 위험, 충돌 방지 가드)
    if snap:
        xs = [p[0] for r in rooms.values() for p in (r.get("polygon") or [])]
        ys = [p[1] for r in rooms.values() for p in (r.get("polygon") or [])]
        xmap, ymap = _cluster_snap(xs, tol), _cluster_snap(ys, tol)
        for r in rooms.values():
            poly = r.get("polygon") or []
            snapped = [[xmap.get(p[0], p[0]), ymap.get(p[1], p[1])] for p in poly]
            cleaned = _dedup(rectify_diagonals(snapped))
            if len(set(map(tuple, cleaned))) >= 4:   # 붕괴 방지: 4꼭짓점 유지 시만 채택
                r["polygon"] = cleaned
    # 3) 벽 재생성 (★렌더가 repair된 방을 반영하게 — from_geomgraph는 g['walls']에서 그림)
    _rebuild_walls(g)
    return g, log
