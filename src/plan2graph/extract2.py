"""정밀 위상 추출기 v2 (AI-Hub) — 골드 검수 GUI용.

SPA+STR+OBJ(+OCR) 지문 병합 → 연결공간(복도/전실) 음의공간 복원 → 문 재배정(연결공간 포함)
→ 기구→욕실/화장실·주방·역할 유도. + 원본 위 '실좌표' 오버레이 렌더(박스 아님).
"""
from __future__ import annotations

import io
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image
import networkx as nx
from shapely.geometry import Point
from shapely.ops import unary_union

import config
from plan2graph.build_aihub import scan_sources
from plan2graph import review as rv
from plan2graph.topology import (_door_normal, open_passages, build_graph,
                                 iter_units, EXTERIOR, _RoomIndex, _rooms_for_door)

matplotlib.rcParams["font.family"] = getattr(rv, "KFONT", "NanumGothic")
matplotlib.rcParams["axes.unicode_minus"] = False

PXM2 = 5390.0
ROLE_COLOR = {
    "침실": "#6CA6E8", "안방": "#1f6fd6", "거실": "#E8453C", "주방": "#F2A900",
    "화장실": "#9ACD66", "욕실": "#3a9a3a", "전용욕실": "#1a7a4a", "전용화장실": "#7ab04a",
    "현관": "#d9534f", "발코니": "#2DBE60", "드레스룸": "#a070d0", "복도": "#9aa0a6",
    "전실": "#c0c4c8", "기타": "#bcbcbc", "실외기실": "#9cc8e8", "다목적공간": "#fdc08a", "알파룸": "#26a69a",
    "계단실": "#888", "엘리베이터": "#888", "엘리베이터홀": "#888",
}

_FPMAP = None


def _fpmap():
    global _FPMAP
    if _FPMAP is None:
        _, entries = scan_sources()
        m = defaultdict(dict)
        for (s, label, key, house) in entries:
            m[s][label] = key
        _FPMAP = m
    return _FPMAP


def load_unit(sheet_id: str, unit_i: int):
    """4라벨 병합 dr + 원본 png + 유닛 방 인덱스 + enriched G(노드에 poly·역할)."""
    fp = rv.fingerprint_of(sheet_id)
    keys = _fpmap().get(fp, {})
    idx = rv.build_indices(("Training", "Validation"))
    docs, png = [], None
    for label, key in keys.items():
        for s in ("Training", "Validation"):
            ie = idx.label_entry.get((s, label, key))
            if ie:
                docs.append(rv.load_coco_bytes(rv._read_zip(*ie), source=ie[1]))
                break
    for label, key in keys.items():
        for s in ("Training", "Validation"):
            se = idx.source_entry.get((s, label, key))
            if se:
                png = rv._read_zip(*se)
                break
        if png:
            break
    dr = rv.assemble_drawing(docs)
    G0 = build_graph(dr, graph_id=sheet_id)
    units, _ = iter_units(G0, min_rooms=config.ACCEPT_MIN_ROOMS - 3)
    unit_i = min(unit_i, len(units) - 1)
    u_idx = [n for n in units[unit_i].nodes() if n != EXTERIOR and isinstance(n, int)]
    G = extract_unit(dr, u_idx)
    return dr, png, u_idx, G, len(units)


def _recover_connectors(union):
    conn = union.buffer(55).buffer(-55).difference(union).buffer(-22).buffer(22)
    parts = [conn] if conn.geom_type == "Polygon" else list(conn.geoms)
    parts = [p for p in parts if p.area > 1500]
    if not parts:
        return []
    m = unary_union([p.buffer(30) for p in parts])
    return [m] if m.geom_type == "Polygon" else list(m.geoms)


def _fixtures(dr, poly):
    return [o.class_name.replace("객체_", "") for o in dr.objects
            if o.centroid and poly.contains(Point(*o.centroid))]


def _assign_door(door, spaces, thr=45):
    nrm = _door_normal(door)
    if not nrm or not door.centroid:
        return []
    (nxv, nyv), slen = nrm
    cx, cy = door.centroid
    found = []
    for sign in (1, -1):
        for dd in (slen * 0.6, slen * 0.6 + 35, slen * 0.6 + 75,
                   slen * 0.6 + 120, slen * 0.6 + 160):
            px, py = cx + nxv * dd * sign, cy + nyv * dd * sign
            best, bd = None, thr
            for sid, poly in spaces:
                z = poly.distance(Point(px, py))
                if z < bd:
                    best, bd = sid, z
            if best is not None:
                if best not in found:
                    found.append(best)
                break
    return found[:2]


def _split_circulation(poly, r=42, min_arm=3000):
    """방 폴리곤에서 좁은 순환부(복도/전실 팔)를 morphological opening으로 분리.
    반환 (본체, [순환부...]). 넓은 본체는 방, 가는 팔은 복도/전실."""
    body = poly.buffer(-r).buffer(r)
    if body.is_empty:
        return poly, []
    if body.geom_type == "MultiPolygon":
        body = max(body.geoms, key=lambda g: g.area)
    arm = poly.difference(body)
    if arm.is_empty:
        return poly, []
    arm = arm.buffer(-4).buffer(4)
    parts = [arm] if arm.geom_type == "Polygon" else list(getattr(arm, "geoms", []))
    parts = [p for p in parts if p.area > min_arm]
    return (body, parts) if parts else (poly, [])


def _blob_priv_door_pts(blob, dr, rooms):
    """오픈 거실 덩어리에 면한, 사적/습식 방으로 향하는 문들의 위치."""
    PRIV = ("공간_침실", "공간_화장실", "공간_드레스룸")
    pts = []
    for d in dr.doors:
        if not d.polygon or not d.centroid or blob.distance(d.polygon) > 45:
            continue
        nrm = _door_normal(d)
        if not nrm:
            continue
        (nxv, nyv), sl = nrm
        cx, cy = d.centroid
        in_blob = priv = False
        for sg in (1, -1):
            for dd in (sl * 0.6, sl * 0.6 + 45, sl * 0.6 + 95):
                p = Point(cx + nxv * dd * sg, cy + nyv * dd * sg)
                if blob.contains(p):
                    in_blob = True
                    break
                if any(r.class_name in PRIV and r.polygon and r.polygon.contains(p)
                       for r in rooms.values()):
                    priv = True
                    break
        if in_blob and priv:
            pts.append((cx, cy))
    return pts


def _carve_corridor(blob, door_pts, hw=78, min_area=2500):
    """오픈 거실 덩어리에서 사적방 문이 면한 부분을 복도로 도려냄.
    반환 (거실 본체, 복도) 또는 (blob, None)."""
    if not door_pts:
        return blob, None
    seed = unary_union([Point(*p).buffer(hw) for p in door_pts]).intersection(blob)
    if seed.is_empty:
        return blob, None
    living = blob.difference(seed)
    if living.geom_type == "MultiPolygon":
        living = max(living.geoms, key=lambda g: g.area)
    if living.is_empty:
        return blob, None
    corridor = blob.difference(living).buffer(-3).buffer(3)
    if corridor.is_empty or corridor.area < min_area:
        return blob, None
    if corridor.geom_type == "MultiPolygon":
        corridor = unary_union(list(corridor.geoms))
    return living, corridor


# open(벽 없는 트임) 허용 타입 — 사적(침실)·습식(화장실)은 항상 문이라 제외
_OPEN_OK = {"거실", "주방", "발코니", "다목적공간", "기타", "알파룸"}


def extract_unit(dr, u_idx) -> nx.Graph:
    rooms = {di: dr.rooms[di] for di in u_idx}
    union = unary_union([r.polygon for r in rooms.values()])
    G = nx.Graph()
    cidx = 100000

    def add_space(base, role, poly):
        nonlocal cidx
        nid = cidx
        cidx += 1
        G.add_node(nid, base=base, role=role, fx=[], poly=poly,
                   cx=poly.centroid.x, cy=poly.centroid.y, area=round(poly.area / PXM2, 1))
        return nid

    _liv_corr = []   # (거실 di, 복도 nid) — open 연결용
    # 방 노드 — 거실: 오픈 덩어리에서 복도 도려냄 / 드레스룸: 전실 분리
    for di, r in rooms.items():
        base = r.class_name.replace("공간_", "")
        fx = _fixtures(dr, r.polygon)
        poly = r.polygon
        if base == "거실":
            pts = _blob_priv_door_pts(poly, dr, rooms)
            living, corr = _carve_corridor(poly, pts)
            if corr is not None:
                cnid = add_space("복도", "복도", corr)
                _liv_corr.append((di, cnid))
                poly = living
        elif base == "드레스룸":
            poly, arms = _split_circulation(poly, r=30, min_arm=2000)
            for a in arms:
                add_space("전실", "전실", a)
        role = base
        if base == "화장실":
            role = "욕실" if any("욕조" in f for f in fx) else "화장실"
        elif base == "주방" or any(f in ("싱크대", "가스레인지") for f in fx):
            role = "주방"
        G.add_node(di, base=base, role=role, fx=fx, poly=poly,
                   cx=poly.centroid.x, cy=poly.centroid.y, area=round(poly.area / PXM2, 1))

    # 거실 ─open─ 복도 (도려낸 복도는 거실과 트여 있음)
    for liv, corr in _liv_corr:
        G.add_edge(liv, corr, via="open")

    # 라벨 안 된 음의공간 복도(어떤 폴리곤에도 안 덮인 빈 통로)
    covered = unary_union([G.nodes[n]["poly"] for n in G])
    for p in _recover_connectors(covered):
        add_space("복도", "복도", p)

    # 문 재배정 — 검증된 _rooms_for_door(법선probe + gap내 최근접 폴백)에
    #   방 본체 + 연결공간(복도/전실)을 함께 인덱스로 넣어 배정(침실→복도 폴백).
    class _P:
        def __init__(self, poly):
            self.polygon = poly
    order = list(G.nodes)
    ridx = _RoomIndex([_P(G.nodes[n]["poly"]) for n in order])
    doors = [d for d in dr.doors if d.polygon and union.distance(d.polygon) < 60 and d.centroid]
    for d in doors:
        f = _rooms_for_door(d, ridx)
        if len(f) == 2:
            G.add_edge(order[f[0]], order[f[1]], via="door")

    # 연결공간끼리 인접
    conns = [n for n in G if G.nodes[n]["base"] in ("복도", "전실")]
    for a in range(len(conns)):
        for b in range(a + 1, len(conns)):
            if G.nodes[conns[a]]["poly"].buffer(22).intersects(G.nodes[conns[b]]["poly"]):
                G.add_edge(conns[a], conns[b], via="corridor")

    # 개방연결(LDK) — 사적·습식 제외, 공용/주방/발코니만 open
    for i, j in open_passages(dr):
        if i in rooms and j in rooms:
            if (G.nodes[i]["base"] in _OPEN_OK and G.nodes[j]["base"] in _OPEN_OK
                    and not G.has_edge(i, j)):
                G.add_edge(i, j, via="open")

    # C: 거실-주방 LDK — 사적/습식 방이 주방에 문연결되면 거실로 재배선
    kit = [n for n in G if G.nodes[n]["role"] == "주방"]
    liv = [n for n in G if G.nodes[n]["role"] == "거실"]
    if kit and liv:
        k, lv = kit[0], liv[0]
        for n in list(G.neighbors(k)):
            if G.nodes[n]["base"] in ("침실", "화장실", "드레스룸") and \
                    G[k][n].get("via") == "door":
                G.remove_edge(k, n)
                if not G.has_edge(lv, n):
                    G.add_edge(lv, n, via="door")
        if not G.has_edge(lv, k):
            G.add_edge(lv, k, via="open")

    # 안방 유도 (전실·복도 1-hop 포함)
    def nbases(nid):
        out = set()
        for m in G.neighbors(nid):
            out.add(G.nodes[m]["role"])
            if G.nodes[m]["base"] in ("복도", "전실"):
                for kk in G.neighbors(m):
                    out.add(G.nodes[kk]["role"])
        return out
    master = None
    for di in rooms:
        if G.nodes[di]["base"] == "침실":
            nb = nbases(di)
            if "드레스룸" in nb and ({"욕실", "화장실"} & nb):
                if master is None or G.nodes[di]["area"] > G.nodes[master]["area"]:
                    master = di
    if master is not None:
        G.nodes[master]["role"] = "안방"
        for m in G.neighbors(master):
            if G.nodes[m]["role"] in ("욕실", "화장실"):
                G.nodes[m]["role"] = "전용" + G.nodes[m]["role"]
    return G


def unit_crop(dr, png, G, margin=90):
    """유닛 영역으로 원본 PNG 크롭 → (PIL.Image, x0, y0). 클릭 좌표→방 매핑용.
    화면 클릭(x,y)을 원본좌표로: px = x0 + x*(crop.width/표시폭), py = y0 + y*(동일배율)."""
    polys = [G.nodes[n]["poly"] for n in G]
    u = unary_union(polys)
    bx0, by0, bx1, by1 = u.bounds
    img = Image.open(io.BytesIO(png)).convert("RGB") if png else None
    if img is None:
        return None, 0, 0
    x0 = max(0, int(bx0 - margin))
    y0 = max(0, int(by0 - margin))
    x1 = min(img.width, int(bx1 + margin))
    y1 = min(img.height, int(by1 + margin))
    return img.crop((x0, y0, x1, y1)), x0, y0


def node_at(G, px, py):
    """원본좌표 (px,py)를 포함하는 노드 id(가장 작은 면적 우선 — 겹침 시 안쪽)."""
    from shapely.geometry import Point
    p = Point(px, py)
    hits = [n for n in G if G.nodes[n]["poly"].contains(p)]
    if not hits:
        hits = [n for n in G if G.nodes[n]["poly"].distance(p) < 20]
    if not hits:
        return None
    return min(hits, key=lambda n: G.nodes[n]["poly"].area)


def render_review(dr, png, G, width=15.0, height=8.0, highlight=None):
    """원본 ∥ 원본+추출오버레이(실좌표). 박스 아님 — 실제 방 폴리곤·복도·문·연결."""
    polys = [G.nodes[n]["poly"] for n in G]
    u = unary_union(polys)
    x0, y0, x1, y1 = u.bounds
    m = 90
    x0, y0, x1, y1 = int(x0 - m), int(y0 - m), int(x1 + m), int(y1 + m)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(width, height))
    crop = None
    if png:
        img = Image.open(io.BytesIO(png)).convert("RGB")
        x1c, y1c = min(img.width, x1), min(img.height, y1)
        x0c, y0c = max(0, x0), max(0, y0)
        crop = img.crop((x0c, y0c, x1c, y1c))
        a1.imshow(crop)
        a2.imshow(crop, alpha=0.35)
        ox, oy = x0c, y0c
    else:
        ox, oy = x0, y0
        for ax in (a1, a2):
            ax.set_xlim(x0, x1)
            ax.set_ylim(y1, y0)
    a1.set_title("원본 도면")
    a2.set_title("추출 위상 (실좌표 오버레이)")
    # 방·복도 폴리곤 채우기 + 라벨
    for n in G:
        nd = G.nodes[n]
        try:
            xs, ys = nd["poly"].exterior.xy
        except Exception:
            continue
        _hl = (n == highlight)
        a2.fill([x - ox for x in xs], [y - oy for y in ys],
                color=ROLE_COLOR.get(nd["role"], "#ccc"), alpha=0.7 if _hl else 0.5,
                ec="red" if _hl else "#333", lw=3 if _hl else 0.8)
        a2.text(nd["cx"] - ox, nd["cy"] - oy, nd["role"], fontsize=8,
                ha="center", va="center", weight="bold")
    # 연결선 (실 centroid 간), via 색
    ec = {"door": "#222", "open": "#F2A900", "corridor": "#666"}
    for uu, vv, d in G.edges(data=True):
        a2.plot([G.nodes[uu]["cx"] - ox, G.nodes[vv]["cx"] - ox],
                [G.nodes[uu]["cy"] - oy, G.nodes[vv]["cy"] - oy],
                color=ec.get(d["via"], "#222"), lw=1.6,
                ls="--" if d["via"] == "corridor" else "-")
    # 문 위치
    for dd in dr.doors:
        if dd.centroid and x0 <= dd.centroid[0] <= x1 and y0 <= dd.centroid[1] <= y1:
            a2.plot(dd.centroid[0] - ox, dd.centroid[1] - oy, "ks", ms=4)
    for ax in (a1, a2):
        ax.axis("off")
    fig.tight_layout()
    return fig
