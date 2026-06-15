"""wall-cycle + opening 토큰 코덱 (ADR-0012 §1-3 생성 타깃 직렬화).

g-0.4 그래프(jagged 폴리곤·미통합 벽) ↔ 생성 가능한 토큰 시퀀스.

핵심 아이디어: **격자 양자화가 곧 정제(canonicalize)** —
  bbox 정규화 후 N격자로 스냅하면 (a) 벽두께로 어긋난 끝점이 같은 셀로 모여
  전역 junction이 자동 통합되고(겹침0의 근거), (b) 미세 벽·jagged 꼭짓점이 붕괴해
  단순화된다. = GSDiff식 "코너 집합 + 벽".

표현(벽은 1급 토큰이 아니라 파생):
  corner 집합  : 모든 방 폴리곤 꼭짓점을 양자화·dedup한 전역 junction.
  room-cycle   : 방 = corner index 순환 + role.
  opening      : 문/창 = corner-pair 엣지 + 위치비율(+ 방).
  → 벽은 디코드 때 room-cycle 공유엣지로 유도(2방 공유=interior/단독=exterior, 두께 결정).
    같은 corner를 공유하므로 겹침이 구조적으로 불가능.

라운드트립은 **원본 무손실 복원이 아니라** "canonical 표현 ↔ 토큰" 동등 +
원본 대비 면적·인접·문창수 보존(양자화 손실 허용)으로 정의한다(정직).

ADR로 토큰 문법을 확정하기 전, 실데이터에서 이 코덱이 도는지 먼저 검증하는 게 목적.
의존: shapely.geometry.Polygon 만(부작용 import 회피).
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 13 역할 어휘 — geomgraph.PRIVACY 키(역할)와 정합. 인덱스 = 토큰 id.
ROLES = [
    "거실", "안방", "침실", "주방", "화장실", "욕실", "발코니", "드레스룸",
    "전실", "복도", "실외기실", "다목적공간", "현관", "전용욕실", "전용화장실",
    "파우더룸", "알파룸", "엘리베이터홀", "계단실", "구조물", "기타",
]
ROLE2ID = {r: i for i, r in enumerate(ROLES)}
ROLE_OTHER = ROLE2ID["기타"]

COUNTRIES = ["KR", "CN", "EU"]
HOUSING = ["apartment", "detached", "rowhouse"]
SCHEMAS = ["korean_13cat", "rplan_6cat", "cubicasa_Ncat"]
SCOPES = ["unit", "floor"]   # ADR-0016 생성 단위: 단위세대 / 층평면도


# ─────────────────────────────────────────────────────────────────────────────
# canonical 표현
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Canon:
    grid: int
    bbox: list                       # [x0, y0, w, h] px (역양자화용)
    meta: dict                       # country/housing/label_schema
    corners: list = field(default_factory=list)   # [(qx, qy), ...] 정렬된 전역 junction
    rooms: list = field(default_factory=list)      # [{id, role_id, cycle:[ci, ...]}]
    openings: list = field(default_factory=list)   # [{kind, edge:(ca,cb), rooms:[...], pos}]


def _quant(x, y, bbox, grid):
    x0, y0, w, h = bbox
    qx = round((x - x0) / w * grid) if w > 1e-6 else 0
    qy = round((y - y0) / h * grid) if h > 1e-6 else 0
    return (max(0, min(grid, qx)), max(0, min(grid, qy)))


def _dequant(q, bbox, grid):
    x0, y0, w, h = bbox
    qx, qy = q
    return [round(x0 + qx / grid * w, 1), round(y0 + qy / grid * h, 1)]


def _dedup_cycle(idxs):
    """연속 중복 + 폐합 중복 제거(닫힘은 암묵)."""
    out = []
    for i in idxs:
        if not out or out[-1] != i:
            out.append(i)
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def _edge_key(a, b):
    return (a, b) if a <= b else (b, a)


def _poly_area(pts):
    """shoelace (정수 격자 좌표). 양자화 좌표로 충분."""
    n = len(pts)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


# ─────────────────────────────────────────────────────────────────────────────
# canonicalize: g-0.4 graph → Canon
# ─────────────────────────────────────────────────────────────────────────────
def _simplify_poly(poly, tol):
    """양자화 전 직교 단순화 — jagged 노이즈(벽두께 미세 꼭짓점·짧은 변) 제거.
    shapely simplify(preserve_topology)로 핵심 코너만 남긴다. 실패 시 원본 반환."""
    if tol <= 0 or len(poly) < 4:
        return poly
    try:
        from shapely.geometry import Polygon
        p = Polygon(poly)
        if not p.is_valid:
            p = p.buffer(0)
        if p.is_empty or p.geom_type != "Polygon":
            return poly
        p2 = p.simplify(tol, preserve_topology=True)
        if p2.is_empty or p2.geom_type != "Polygon":
            return poly
        out = [list(c) for c in p2.exterior.coords]
        return out if len(out) >= 4 else poly
    except Exception:  # noqa: BLE001
        return poly


def canonicalize(g: dict, grid: int = 128, simplify_frac: float = 0.01,
                 use_wall_snap: bool = False) -> Canon:
    bbox = g.get("bbox_px") or _bbox_from_rooms(g)
    meta_in = g.get("meta") or {}
    meta = {
        "country": meta_in.get("country", "KR"),
        "housing": meta_in.get("housing_type")
                   or {"APT": "apartment", "DEH": "detached", "ROW": "rowhouse"}.get(
                       g.get("house") or meta_in.get("house_type"), "apartment"),
        "label_schema": meta_in.get("label_schema", "korean_13cat"),
        # ADR-0016 생성 단위 scope/세대수(없으면 단위세대 기본)
        "scope": meta_in.get("plan_scope", "unit"),
        "units": int(meta_in.get("units", 1) or 1),
    }
    canon = Canon(grid=grid, bbox=list(bbox), meta=meta)
    # 단순화 tol = 도면 규모 비례(작은 방은 덜, preserve_topology가 최소형 보장)
    tol = simplify_frac * (max(1.0, bbox[2]) * max(1.0, bbox[3])) ** 0.5 if simplify_frac > 0 else 0.0

    # 1. 양자화 → 임시 corner pool(아직 병합 전)
    qpt_id: dict = {}    # (qx,qy) -> tmp id
    def _qid(pt):
        q = _quant(pt[0], pt[1], bbox, grid)
        if q not in qpt_id:
            qpt_id[q] = len(qpt_id)
        return qpt_id[q]

    room_tmp: dict = {}    # rid -> (role, [tmp_id cycle])
    room_poly: dict = {}   # rid -> 단순화 polygon(snap 계산용)
    for nid, r in (g.get("rooms") or {}).items():
        poly = _simplify_poly(r.get("polygon") or [], tol)
        if len(poly) < 3:
            continue
        ids = _dedup_cycle([_qid(p) for p in poly])
        if len(ids) < 3:
            continue
        rid = int(nid) if str(nid).lstrip("-").isdigit() else nid
        role = r.get("role") or r.get("base") or "기타"
        room_tmp[rid] = (role, ids)
        room_poly[rid] = poly

    # 2. union-find
    parent = list(range(len(qpt_id)))
    def _find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def _union(a, b):
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    id2q = {v: k for k, v in qpt_id.items()}

    def _nearest_tmp(poly, ep):
        best, bd = None, 1e18
        for p in poly:
            d = (p[0] - ep[0]) ** 2 + (p[1] - ep[1]) ** 2
            if d < bd:
                bd, best = d, p
        return _qid(best)

    # 3. 원본 interior 벽(공유 방쌍, _derive_walls가 buffer로 계산) 신뢰 →
    #    벽 양끝에서 두 방 경계 꼭짓점을 같은 junction으로 병합(gap-closing).
    for w in ((g.get("walls") or []) if use_wall_snap else []):
        if w.get("type") != "interior":
            continue
        rms = [r for r in (w.get("rooms") or []) if r in room_poly]
        if len(rms) < 2:
            continue
        seg = w.get("segment") or []
        if len(seg) < 2:
            continue
        a, b = rms[0], rms[1]
        for ep in (seg[0], seg[-1]):
            _union(_nearest_tmp(room_poly[a], ep), _nearest_tmp(room_poly[b], ep))

    # 4. 대표 corner 재인덱싱(병합 그룹당 1개)
    rep_idx: dict = {}
    def _final(tid):
        root = _find(tid)
        if root not in rep_idx:
            rep_idx[root] = len(canon.corners)
            canon.corners.append(id2q[root])
        return rep_idx[root]

    room_cycle: dict = {}    # room_id -> [final corner idx]
    for rid, (role, ids) in room_tmp.items():
        cyc = _dedup_cycle([_final(t) for t in ids])
        if len(cyc) < 3:
            continue
        canon.rooms.append({"id": rid, "role_id": ROLE2ID.get(role, ROLE_OTHER), "cycle": cyc})
        room_cycle[rid] = cyc

    # 5. 각 방 cycle의 엣지 집합(공유 판정용)
    edge_rooms: dict = {}    # edge_key -> set(room_id)
    for rid, cyc in room_cycle.items():
        n = len(cyc)
        for i in range(n):
            ek = _edge_key(cyc[i], cyc[(i + 1) % n])
            if ek[0] != ek[1]:
                edge_rooms.setdefault(ek, set()).add(rid)

    # openings: door/window → corner-pair 엣지 + 위치비율
    for d in (g.get("doors") or []):
        cn = d.get("connects") or d.get("rooms")
        ek = _pick_shared_edge(cn, d.get("position"), room_cycle, edge_rooms,
                               canon.corners, bbox, grid, want_shared=True)
        if ek is None:
            continue
        canon.openings.append({
            "kind": "door", "edge": list(ek),
            "rooms": [r for r in (cn or []) if r in room_cycle][:2],
            "pos": _edge_pos(ek, d.get("position"), canon.corners, bbox, grid),
        })
    for w in (g.get("windows") or []):
        rid = w.get("belongs_to")
        rid = int(rid) if str(rid).lstrip("-").isdigit() else rid
        ek = _pick_shared_edge([rid], w.get("position"), room_cycle, edge_rooms,
                               canon.corners, bbox, grid, want_shared=False)
        if ek is None:
            continue
        canon.openings.append({
            "kind": "window", "edge": list(ek), "rooms": [rid] if rid in room_cycle else [],
            "pos": _edge_pos(ek, w.get("position"), canon.corners, bbox, grid),
        })

    # open 경계(문 없는 공유, ADR-0010 boundary=open) = 방쌍 명시 토큰.
    # corner 공유에 의존 않으므로 union 없이도 인접 보존(방 붕괴 회피).
    for e in (g.get("edges") or []):
        if e.get("via") != "open":
            continue
        a, b = _int(e.get("from")), _int(e.get("to"))
        if a in room_cycle and b in room_cycle and a != b:
            canon.openings.append({"kind": "open", "edge": None, "rooms": [a, b], "pos": 0})
    return canon


def _bbox_from_rooms(g):
    xs, ys = [], []
    for r in (g.get("rooms") or {}).values():
        for p in (r.get("polygon") or []):
            xs.append(p[0]); ys.append(p[1])
    if not xs:
        return [0, 0, 1, 1]
    return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]


def _pick_shared_edge(room_ids, pos, room_cycle, edge_rooms, corners, bbox, grid, want_shared):
    """opening 위치에 가장 가까운 후보 엣지를 고른다.
    want_shared=True(문): room_ids 두 방이 공유하는 엣지. False(창): 한 방의 외곽(단독) 엣지."""
    rids = [r for r in (room_ids or []) if r in room_cycle]
    if not rids:
        return None
    cand = []
    if want_shared and len(rids) >= 2:
        a, b = rids[0], rids[1]
        for ek, rs in edge_rooms.items():
            if a in rs and b in rs:
                cand.append(ek)
    if not cand:
        # 폴백: 첫 방의 엣지 전부(창=단독 우선)
        rid = rids[0]
        cyc = room_cycle[rid]
        n = len(cyc)
        for i in range(n):
            ek = _edge_key(cyc[i], cyc[(i + 1) % n])
            if ek[0] == ek[1]:
                continue
            if want_shared:
                cand.append(ek)
            else:
                if len(edge_rooms.get(ek, set())) <= 1:   # 외곽(단독)
                    cand.append(ek)
        if not cand:   # 창인데 외곽 못 찾으면 아무 엣지
            for i in range(n):
                ek = _edge_key(cyc[i], cyc[(i + 1) % n])
                if ek[0] != ek[1]:
                    cand.append(ek)
    if not cand:
        return None
    if pos is None:
        return cand[0]
    # position에 가장 가까운 엣지 중점
    best, bestd = None, 1e18
    for ek in cand:
        mx = (corners[ek[0]][0] + corners[ek[1]][0]) / 2
        my = (corners[ek[0]][1] + corners[ek[1]][1]) / 2
        px = _quant(pos[0], pos[1], bbox, grid)
        d = (mx - px[0]) ** 2 + (my - px[1]) ** 2
        if d < bestd:
            best, bestd = ek, d
    return best


def _edge_pos(ek, pos, corners, bbox, grid, nbins=16):
    """엣지 위 위치비율 → nbins 양자화(0..nbins)."""
    if pos is None:
        return nbins // 2
    a, b = corners[ek[0]], corners[ek[1]]
    px = _quant(pos[0], pos[1], bbox, grid)
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    vx, vy = bx - ax, by - ay
    denom = vx * vx + vy * vy
    if denom < 1e-9:
        return nbins // 2
    t = ((px[0] - ax) * vx + (px[1] - ay) * vy) / denom
    t = max(0.0, min(1.0, t))
    return round(t * nbins)


# ─────────────────────────────────────────────────────────────────────────────
# decode: Canon → g-0.4 호환 graph dict (cadrender.from_geomgraph 입력)
# ─────────────────────────────────────────────────────────────────────────────
def canon_to_graph(canon: Canon) -> dict:
    bbox, grid = canon.bbox, canon.grid
    cpx = [_dequant(q, bbox, grid) for q in canon.corners]   # corner → px

    # walls 유도: 모든 room-cycle 엣지 집계
    edge_rooms: dict = {}
    for rm in canon.rooms:
        cyc = rm["cycle"]
        n = len(cyc)
        for i in range(n):
            ek = _edge_key(cyc[i], cyc[(i + 1) % n])
            if ek[0] != ek[1]:
                edge_rooms.setdefault(ek, set()).add(rm["id"])

    walls, wall_of_edge = [], {}
    for k, (ek, rs) in enumerate(sorted(edge_rooms.items())):
        ty = "interior" if len(rs) >= 2 else "exterior"
        wid = f"w{k}"
        wall_of_edge[ek] = wid
        walls.append({
            "id": wid, "type": ty,
            "segment": [cpx[ek[0]], cpx[ek[1]]],
            "rooms": sorted(rs), "openings": [],
            "thickness_mm": 200 if ty == "exterior" else 120,
        })

    rooms = {}
    for rm in canon.rooms:
        role = ROLES[rm["role_id"]] if 0 <= rm["role_id"] < len(ROLES) else "기타"
        poly = [cpx[c] for c in rm["cycle"]]
        poly = poly + [poly[0]]    # 닫기(렌더 관례)
        rooms[str(rm["id"])] = {
            "role": role, "base": role,
            "polygon": poly,
            "wall_ids": [], "door_ids": [], "window_ids": [],
        }

    doors, windows, edges = [], [], []
    dn = wn = 0
    for op in canon.openings:
        if op["kind"] == "open":
            rms = op["rooms"]
            if len(rms) == 2:
                edges.append({"from": rms[0], "to": rms[1], "via": "open", "door_id": None})
            continue
        ek = _edge_key(op["edge"][0], op["edge"][1])
        wid = wall_of_edge.get(ek)
        a, b = cpx[ek[0]], cpx[ek[1]]
        t = op["pos"] / 16.0
        px = [round(a[0] + (b[0] - a[0]) * t, 1), round(a[1] + (b[1] - a[1]) * t, 1)]
        # door/window의 방 귀속 = edge가 속한 room-cycle로 유도(decode 경로에서 op.rooms는 빔).
        #   door=공유벽(2방 connects), window=외벽(1방 belongs_to). corner-pair가 방을 결정.
        ek_rooms = sorted(edge_rooms.get(ek, set()))
        if op["kind"] == "door":
            did = f"d{dn}"; dn += 1
            connects = ek_rooms[:2] or list(op.get("rooms") or [])
            doors.append({"id": did, "connects": connects, "via": "door",
                          "position": px, "on_wall": wid})
            if wid:
                for w in walls:
                    if w["id"] == wid:
                        w["openings"].append(did)
            for rid in connects:
                if str(rid) in rooms:
                    rooms[str(rid)]["door_ids"].append(did)
            if len(connects) == 2:
                edges.append({"from": connects[0], "to": connects[1],
                              "via": "door", "door_id": did})
        else:
            win = f"win{wn}"; wn += 1
            rid = ek_rooms[0] if ek_rooms else (op["rooms"][0] if op.get("rooms") else None)
            windows.append({"id": win, "belongs_to": rid, "position": px, "on_wall": wid})
            if rid is not None and str(rid) in rooms:
                rooms[str(rid)]["window_ids"].append(win)
            if wid:
                for w in walls:
                    if w["id"] == wid:
                        w["openings"].append(win)

    return {
        "schema_version": "g-0.4",
        "bbox_px": bbox,
        "meta": {"country": canon.meta["country"], "housing_type": canon.meta["housing"],
                 "label_schema": canon.meta["label_schema"]},
        "n_rooms": len(rooms), "n_walls": len(walls),
        "n_doors": len(doors), "n_windows": len(windows),
        "rooms": rooms, "walls": walls, "doors": doors, "windows": windows, "edges": edges,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 토큰 직렬화: Canon ↔ flat int 시퀀스
# ─────────────────────────────────────────────────────────────────────────────
# 어휘 오프셋(구간 분리). coord/role/pos/meta를 한 정수 공간에 배치.
class V:
    BOS = 0
    EOS = 1
    SEC_CORNERS = 2
    SEC_ROOMS = 3
    SEC_OPEN = 4
    ROOM_END = 5
    DOOR = 6
    WINDOW = 7
    OPEN = 8       # ADR-0010 boundary=open: 문 없는 공유 경계(방쌍 명시, 벽 안 그림)
    _BASE = 9
    # 동적 구간: META(country|housing|schema), COORD(0..grid), ROLE, POS, ROOM(ordinal)


def _vocab(grid: int, nbins: int = 16, maxrooms: int = 64, max_units: int = 8):
    off = V._BASE
    meta_off = off;                 off += len(COUNTRIES) + len(HOUSING) + len(SCHEMAS)
    scope_off = off;                off += len(SCOPES)        # ADR-0016 plan_scope
    units_off = off;                off += (max_units + 1)    # ADR-0016 세대수(floor=1..n)
    coord_off = off;                off += (grid + 1)
    role_off = off;                 off += len(ROLES)
    pos_off = off;                  off += (nbins + 1)
    room_off = off;                 off += maxrooms          # open 토큰의 방 ordinal 참조
    return {"meta": meta_off, "scope": scope_off, "units": units_off,
            "coord": coord_off, "role": role_off,
            "pos": pos_off, "room": room_off, "maxrooms": maxrooms, "max_units": max_units,
            "size": off, "grid": grid, "nbins": nbins}


def encode(canon: Canon, vocab=None) -> list:
    vb = vocab or _vocab(canon.grid)
    t = [V.BOS]
    # META
    t.append(vb["meta"] + COUNTRIES.index(canon.meta["country"]) if canon.meta["country"] in COUNTRIES else vb["meta"])
    t.append(vb["meta"] + len(COUNTRIES) + (HOUSING.index(canon.meta["housing"]) if canon.meta["housing"] in HOUSING else 0))
    t.append(vb["meta"] + len(COUNTRIES) + len(HOUSING) + (SCHEMAS.index(canon.meta["label_schema"]) if canon.meta["label_schema"] in SCHEMAS else 0))
    # SCOPE / UNITS (ADR-0016)
    t.append(vb["scope"] + (SCOPES.index(canon.meta["scope"]) if canon.meta.get("scope") in SCOPES else 0))
    t.append(vb["units"] + min(max(1, int(canon.meta.get("units", 1))), vb["max_units"]))
    # CORNERS
    t.append(V.SEC_CORNERS)
    for (qx, qy) in canon.corners:
        t.append(vb["coord"] + qx)
        t.append(vb["coord"] + qy)
    # ROOMS
    t.append(V.SEC_ROOMS)
    for rm in canon.rooms:
        t.append(vb["role"] + rm["role_id"])
        for c in rm["cycle"]:
            t.append(vb["coord"] + c) if False else t.append(_corner_ref(c, vb))
        t.append(V.ROOM_END)
    # OPENINGS
    room_ord = {rm["id"]: i for i, rm in enumerate(canon.rooms)}
    t.append(V.SEC_OPEN)
    for op in canon.openings:
        if op["kind"] == "open":
            a, b = op["rooms"][0], op["rooms"][1]
            if a not in room_ord or b not in room_ord:
                continue
            t.append(V.OPEN)
            t.append(vb["room"] + room_ord[a])
            t.append(vb["room"] + room_ord[b])
            continue
        t.append(V.DOOR if op["kind"] == "door" else V.WINDOW)
        t.append(_corner_ref(op["edge"][0], vb))
        t.append(_corner_ref(op["edge"][1], vb))
        t.append(vb["pos"] + op["pos"])
    t.append(V.EOS)
    return t


def _corner_ref(idx, vb):
    """방 cycle·opening의 corner 참조는 인덱스(coord 구간 재사용)."""
    return vb["coord"] + idx


def decode(tokens: list, vocab) -> Canon:
    vb = vocab
    grid, nbins = vb["grid"], vb["nbins"]
    i = 0
    assert tokens[i] == V.BOS; i += 1
    # META
    c_tok = tokens[i] - vb["meta"]; i += 1
    h_tok = tokens[i] - vb["meta"] - len(COUNTRIES); i += 1
    s_tok = tokens[i] - vb["meta"] - len(COUNTRIES) - len(HOUSING); i += 1
    scope_tok = tokens[i] - vb["scope"]; i += 1
    units_tok = tokens[i] - vb["units"]; i += 1
    meta = {"country": COUNTRIES[c_tok] if 0 <= c_tok < len(COUNTRIES) else "KR",
            "housing": HOUSING[h_tok] if 0 <= h_tok < len(HOUSING) else "apartment",
            "label_schema": SCHEMAS[s_tok] if 0 <= s_tok < len(SCHEMAS) else "korean_13cat",
            "scope": SCOPES[scope_tok] if 0 <= scope_tok < len(SCOPES) else "unit",
            "units": units_tok}
    canon = Canon(grid=grid, bbox=[0, 0, grid, grid], meta=meta)
    assert tokens[i] == V.SEC_CORNERS; i += 1
    while tokens[i] != V.SEC_ROOMS:
        qx = tokens[i] - vb["coord"]; qy = tokens[i + 1] - vb["coord"]; i += 2
        canon.corners.append((qx, qy))
    assert tokens[i] == V.SEC_ROOMS; i += 1
    rid = 0
    while tokens[i] != V.SEC_OPEN:
        role_id = tokens[i] - vb["role"]; i += 1
        cyc = []
        while tokens[i] != V.ROOM_END:
            cyc.append(tokens[i] - vb["coord"]); i += 1
        i += 1
        canon.rooms.append({"id": rid, "role_id": role_id, "cycle": cyc}); rid += 1
    assert tokens[i] == V.SEC_OPEN; i += 1
    while tokens[i] != V.EOS:
        if tokens[i] == V.OPEN:
            i += 1
            oa = tokens[i] - vb["room"]; ob = tokens[i + 1] - vb["room"]; i += 2
            rid_a = canon.rooms[oa]["id"] if 0 <= oa < len(canon.rooms) else oa
            rid_b = canon.rooms[ob]["id"] if 0 <= ob < len(canon.rooms) else ob
            canon.openings.append({"kind": "open", "edge": None, "rooms": [rid_a, rid_b], "pos": 0})
            continue
        kind = "door" if tokens[i] == V.DOOR else "window"; i += 1
        ca = tokens[i] - vb["coord"]; cb = tokens[i + 1] - vb["coord"]; i += 2
        pos = tokens[i] - vb["pos"]; i += 1
        canon.openings.append({"kind": kind, "edge": [ca, cb], "rooms": [], "pos": pos})
    return canon


# ─────────────────────────────────────────────────────────────────────────────
# 라운드트립 검증
# ─────────────────────────────────────────────────────────────────────────────
def roundtrip_metrics(g: dict, grid: int = 128, simplify_frac: float = 0.01,
                      use_wall_snap: bool = False) -> dict:
    """원본 g-0.4 ↔ canonical/토큰 라운드트립의 보존율 측정."""
    canon = canonicalize(g, grid=grid, simplify_frac=simplify_frac, use_wall_snap=use_wall_snap)
    vb = _vocab(grid)
    toks = encode(canon, vb)
    canon2 = decode(toks, vb)

    # 토큰 라운드트립(canonical 동등): corner/room cycle/opening 일치
    def _op_sig(c):
        ro = {rm["id"]: i for i, rm in enumerate(c.rooms)}
        out = []
        for o in c.openings:
            if o["kind"] == "open":
                out.append(("open", tuple(sorted(ro.get(r, -1) for r in o["rooms"]))))
            else:
                out.append((o["kind"], tuple(o["edge"]), o["pos"]))
        return out
    tok_ok = (
        [tuple(c) for c in canon.corners] == [tuple(c) for c in canon2.corners]
        and [(r["role_id"], r["cycle"]) for r in canon.rooms]
            == [(r["role_id"], r["cycle"]) for r in canon2.rooms]
        and _op_sig(canon) == _op_sig(canon2)
    )

    # 원본 대비 보존(양자화 손실 허용)
    g2 = canon_to_graph(canon)
    n_rooms_in = len(g.get("rooms") or {})
    n_rooms_out = len(g2["rooms"])

    # 면적 보존(양자화 폴리곤 면적 / 원본 면적)
    area_ratios = []
    orig_rooms = {(_int(k)): r for k, r in (g.get("rooms") or {}).items()}
    for rm in canon.rooms:
        qa = _poly_area([canon.corners[c] for c in rm["cycle"]])
        orig = orig_rooms.get(rm["id"])
        if orig and orig.get("polygon") and qa > 0:
            # 원본 면적을 grid 단위로 환산
            ox = _orig_area_in_grid(orig["polygon"], canon.bbox, grid)
            if ox > 0:
                area_ratios.append(qa / ox)

    # 인접 보존(원본 edges vs 디코드 edges, 방쌍 집합)
    def _pairs(edges):
        s = set()
        for e in edges or []:
            a, b = e.get("from"), e.get("to")
            a, b = _int(a), _int(b)
            if a is not None and b is not None:
                s.add(frozenset((a, b)))
        return s
    pin, pout = _pairs(g.get("edges")), _pairs(g2["edges"])
    inter = len(pin & pout)
    jac = inter / len(pin | pout) if (pin or pout) else 1.0

    # 겹침(양자화 폴리곤 pairwise) — shapely 있으면 정밀, 없으면 스킵
    overlap = _overlap_area(canon)

    return {
        "grid": grid,
        "n_corners": len(canon.corners),
        "n_rooms_in": n_rooms_in, "n_rooms_out": n_rooms_out,
        "n_tokens": len(toks),
        "token_roundtrip_ok": tok_ok,
        "n_doors_in": len(g.get("doors") or []), "n_doors_out": g2["n_doors"],
        "n_windows_in": len(g.get("windows") or []), "n_windows_out": g2["n_windows"],
        "area_ratio_med": _median(area_ratios),
        "area_ratio_min": min(area_ratios) if area_ratios else None,
        "area_ratio_max": max(area_ratios) if area_ratios else None,
        "adj_jaccard": round(jac, 3),
        "adj_pairs_in": len(pin), "adj_pairs_out": len(pout),
        "overlap_area_frac": overlap,
        "vocab_size": vb["size"],
    }


def _int(k):
    try:
        return int(k)
    except (ValueError, TypeError):
        return k if isinstance(k, str) else None


def _orig_area_in_grid(poly, bbox, grid):
    pts = [_quant(p[0], p[1], bbox, grid) for p in poly]
    ded = []
    for p in pts:
        if not ded or ded[-1] != p:
            ded.append(p)
    return _poly_area(ded)


def _overlap_area(canon: Canon):
    """양자화 방 폴리곤들의 pairwise 겹침 면적 합 / 전체 면적. shapely 필요."""
    try:
        from shapely.geometry import Polygon
    except Exception:  # noqa: BLE001
        return None
    polys = []
    for rm in canon.rooms:
        pts = [canon.corners[c] for c in rm["cycle"]]
        try:
            p = Polygon(pts)
            if p.is_valid and p.area > 0:
                polys.append(p)
        except Exception:  # noqa: BLE001
            pass
    if len(polys) < 2:
        return 0.0
    total = sum(p.area for p in polys)
    ov = 0.0
    for i in range(len(polys)):
        for j in range(i + 1, len(polys)):
            try:
                ov += polys[i].intersection(polys[j]).area
            except Exception:  # noqa: BLE001
                pass
    return round(ov / total, 4) if total > 0 else None


def _median(xs):
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return round((s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2), 3)
