"""Task 1-3 ★ Topology Extraction — 문-방-방 위상 그래프 (프로젝트 최대 난관).

라벨에는 "이 문이 어느 두 방을 잇는가"가 없다 → 기하로 추론한다.

핵심 알고리즘 (baseline):
  1. 각 출입문 폴리곤의 최소회전사각형(MRR)으로 긴 변=벽 방향을 구한다.
  2. 벽 방향의 법선(짧은 변 방향) 양쪽으로 probe point를 쏜다(문은 벽 개구부라
     법선 방향에 양쪽 방이 있다).
  3. probe가 들어간 공간 폴리곤 = 연결 후보. 양쪽에서 가장 가까운 2개를 엣지로.
  4. 한쪽만 잡히면 외부문 → 가상 'exterior' 노드로 연결.

노드 = 공간(SPA). 엣지 = 문(via=door) / 현관-외부(via=entrance) / 외부문(via=exterior_door).
창호·객체는 노드 속성으로 귀속(엣지 아님).

정확도는 손검증셋 20장 게이트(NOTES.md)에서 측정·보강한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import networkx as nx
from shapely.geometry import Point
from shapely.ops import unary_union
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph.geometry import Drawing, Element  # noqa: E402

EXTERIOR = "exterior"


def _space_nodes(G: nx.Graph) -> list:
    """exterior 가상노드를 제외한 공간(방) 노드 목록."""
    return [n for n in G.nodes if n != EXTERIOR]


# ─────────────────────────────────────────────────────────────────────────────
# 문 기하: 법선 방향 산출
# ─────────────────────────────────────────────────────────────────────────────
def _door_normal(door: Element) -> tuple[tuple[float, float], float] | None:
    """문 폴리곤의 최소회전사각형에서 (단위법선벡터, 짧은변 길이)를 구한다.
    법선 = 벽(긴 변)에 수직 = 문이 잇는 두 공간을 향하는 방향.
    """
    poly = door.polygon
    if poly is None or poly.is_empty:
        return None
    mrr = poly.minimum_rotated_rectangle
    if mrr.geom_type != "Polygon":
        return None
    xs, ys = mrr.exterior.coords.xy
    # 사각형 코너 4개(+닫힘점). 첫 코너 기준 두 변 벡터.
    p0 = (xs[0], ys[0])
    e1 = (xs[1] - p0[0], ys[1] - p0[1])
    e2 = (xs[3] - p0[0], ys[3] - p0[1])
    len1 = (e1[0] ** 2 + e1[1] ** 2) ** 0.5
    len2 = (e2[0] ** 2 + e2[1] ** 2) ** 0.5
    if len1 == 0 or len2 == 0:
        return None
    # 긴 변 = 벽 방향, 법선 = 짧은 변 방향(단위벡터)
    if len1 >= len2:
        short, slen = e2, len2
    else:
        short, slen = e1, len1
    n = (short[0] / slen, short[1] / slen)
    return n, slen


# ─────────────────────────────────────────────────────────────────────────────
# 공간 탐색 (probe point → 어느 방?)
# ─────────────────────────────────────────────────────────────────────────────
def excluded_rooms(dr: "Drawing") -> set[int]:
    """세대 그래프에서 제외할 방 index — 건물 코어 + 작은 기타 단편(노이즈)."""
    ex: set[int] = set()
    for i, r in enumerate(dr.rooms):
        if r.class_name in config.CORE_CLASSES:
            ex.add(i)
        elif r.class_name == config.ETC_CLASS and r.area_px < config.MIN_ETC_AREA_PX:
            ex.add(i)
    return ex


class _RoomIndex:
    """공간 폴리곤 공간 인덱스(STRtree). point→방, 최근접 방 질의.
    exclude: 노드에서 뺄 방 index(코어·노이즈) — 인덱스에서도 제외해 엣지가 안 생김.
    """

    def __init__(self, rooms: list[Element], exclude: set[int] | None = None):
        ex = exclude or set()
        self.rooms = rooms
        self.geoms = [r.polygon for i, r in enumerate(rooms)
                      if r.polygon is not None and i not in ex]
        self.idx_of = [i for i, r in enumerate(rooms)
                       if r.polygon is not None and i not in ex]
        self.tree = STRtree(self.geoms) if self.geoms else None

    def at(self, pt: Point) -> int | None:
        """pt를 포함하는 방 index(없으면 None)."""
        if self.tree is None:
            return None
        for hit in self.tree.query(pt):  # 후보(경계상자 교차)만
            ri = self.idx_of[int(hit)]
            if self.rooms[ri].polygon.contains(pt):
                return ri
        return None

    def nearest_within(self, pt: Point, max_dist: float) -> int | None:
        """pt에서 max_dist 이내 최근접 방 index."""
        best, best_d = None, max_dist
        for ri in self.idx_of:
            d = self.rooms[ri].polygon.distance(pt)
            if d < best_d:
                best, best_d = ri, d
        return best


def _rooms_for_door(door: Element, ridx: _RoomIndex) -> list[int]:
    """문이 잇는 공간 index들(보통 2개)을 추론.
    법선 양방향으로 여러 거리에서 probe → 포함하는 방 수집. 실패 시 최근접 보강.
    """
    if door.centroid is None:
        return []
    cx, cy = door.centroid
    nrm = _door_normal(door)
    found: list[int] = []
    if nrm is not None:
        (nx_, ny_), slen = nrm
        # probe 거리: 문 두께 절반 + 여유. 여러 단계로 쏴 견고하게.
        base = max(slen * 0.6, config.DOOR_PROBE_DIST_PX * 0.5)
        steps = [base + config.DOOR_PROBE_DIST_PX * f for f in (0.0, 0.5, 1.0)]
        for sign in (+1, -1):
            side = None
            for d in steps:
                px, py = cx + nx_ * d * sign, cy + ny_ * d * sign
                ri = ridx.at(Point(px, py))
                if ri is not None:
                    side = ri
                    break
            if side is not None and side not in found:
                found.append(side)
    # 법선 추론이 2개를 못 채우면: 문 buffer에 닿는 최근접 방으로 보강
    if len(found) < 2:
        dpoly = door.polygon
        cand = []
        for ri in ridx.idx_of:
            if ri in found:
                continue
            d = ridx.rooms[ri].polygon.distance(dpoly)
            if d <= config.DOOR_MAX_GAP_PX:
                cand.append((d, ri))
        cand.sort()
        for _, ri in cand:
            if len(found) >= 2:
                break
            found.append(ri)
    return found


# ─────────────────────────────────────────────────────────────────────────────
# 개방통로(via:"open") — 문·발코니창 없이 벽이 끊긴 개구부로 연결된 방
# ─────────────────────────────────────────────────────────────────────────────
def open_passages(dr: "Drawing", max_gap: float | None = None,
                  min_ratio: float | None = None,
                  exclude: set[int] | None = None) -> list[tuple[int, int]]:
    """인접한 두 방의 공유 경계대(buffer 교집합)에서 구조_벽체가 덮지 않은
    개구부 비율이 임계 이상이면 개방통로로 보고 (a,b) 쌍을 반환.

    개방형 LDK(거실-주방 트임)·문 없이 트인 침실/욕실 등을 잇는다.
    한국 아파트 평면은 문+발코니창만으로는 연결이 끊겨, 이 통로가 표준 엣지로 필요하다.
    """
    max_gap = config.OPEN_MAX_GAP_PX if max_gap is None else max_gap
    min_ratio = config.OPEN_MIN_RATIO if min_ratio is None else min_ratio
    ex = exclude or set()
    rooms = [(i, r) for i, r in enumerate(dr.rooms)
             if r.polygon is not None and i not in ex]
    if not rooms:
        return []
    g = max_gap / 2.0
    wall_geoms = [w.polygon for w in dr.walls if w.polygon is not None]
    wall_tree = STRtree(wall_geoms) if wall_geoms else None
    geoms = [r.polygon for _, r in rooms]
    tree = STRtree(geoms)
    out: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for ti, (i, ri) in enumerate(rooms):
        bi = ri.polygon.buffer(g)
        for hit in tree.query(bi):
            tj = int(hit)
            if tj <= ti:
                continue
            j, rj = rooms[tj]
            key = (i, j)
            if key in seen or ri.polygon.distance(rj.polygon) > max_gap:
                continue
            # 건축적으로 직접 트일 수 없는 방쌍은 개방통로 금지(과연결 차단)
            if tuple(sorted((ri.class_name, rj.class_name))) in config.OPEN_FORBIDDEN_PAIRS:
                continue
            # 두 방 '사이의 간격(gap) 띠'만 본다(방 내부 제외). 벽 미피복 비율로 개구부 판정.
            zone = bi.intersection(rj.polygon.buffer(g))
            if zone.is_empty:
                continue
            gap = zone.difference(ri.polygon).difference(rj.polygon)
            # 두 방의 볼록껍질로 클립 → 방 밖으로 튀는 buffer 캡(가짜 간격) 제거.
            try:
                hull = unary_union([ri.polygon, rj.polygon]).convex_hull
                gap = gap.intersection(hull)
            except Exception:
                pass
            ga = gap.area
            if ga < 1.0:  # 방이 거의 맞붙음 → 간격으로 판단 불가(문/발코니로 처리됨)
                continue
            open_area = ga
            if wall_tree is not None:
                near = [wall_geoms[int(h)] for h in wall_tree.query(gap)]
                if near:
                    blocked = gap.intersection(unary_union(near))
                    open_area -= (blocked.area if not blocked.is_empty else 0)
            if open_area / ga >= min_ratio:
                seen.add(key)
                out.append((i, j))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 창호·객체 귀속
# ─────────────────────────────────────────────────────────────────────────────
def _attach_windows(dr: Drawing, ridx: _RoomIndex, win_count: dict[int, int],
                    win_len: dict[int, float]) -> None:
    for w in dr.windows:
        if w.polygon is None or w.centroid is None:
            continue
        # 창이 닿는 방을 '양쪽 모두' 귀속(법선 probe). 발코니 뒤 실내방도 채광 인정.
        rooms = _rooms_for_door(w, ridx)
        if not rooms:
            # probe 실패 시 최근접 1개로 폴백
            ri = ridx.nearest_within(Point(*w.centroid), config.DOOR_MAX_GAP_PX)
            rooms = [ri] if ri is not None else []
        if not rooms:
            continue
        try:
            mrr = w.polygon.minimum_rotated_rectangle
            xs, ys = mrr.exterior.coords.xy
            e1 = ((xs[1] - xs[0]) ** 2 + (ys[1] - ys[0]) ** 2) ** 0.5
            e2 = ((xs[3] - xs[0]) ** 2 + (ys[3] - ys[0]) ** 2) ** 0.5
            wlen = max(e1, e2)
        except Exception:
            wlen = 0.0
        for ri in rooms:                      # 양쪽 방에 창 크레딧
            win_count[ri] = win_count.get(ri, 0) + 1
            win_len[ri] = win_len.get(ri, 0.0) + wlen


def _attach_objects(dr: Drawing, ridx: _RoomIndex,
                    objs: dict[int, list[str]]) -> None:
    for o in dr.objects:
        if o.centroid is None:
            continue
        ri = ridx.at(Point(*o.centroid))
        if ri is None:
            ri = ridx.nearest_within(Point(*o.centroid), config.DOOR_MAX_GAP_PX)
        if ri is None:
            continue
        objs.setdefault(ri, []).append(o.class_name.replace("객체_", ""))


# ─────────────────────────────────────────────────────────────────────────────
# 그래프 구축
# ─────────────────────────────────────────────────────────────────────────────
def build_graph(dr: Drawing, graph_id: str = "") -> nx.Graph:
    """Drawing → 방-문-방 위상 그래프(networkx)."""
    G = nx.Graph(graph_id=graph_id, house_type=None,
                 scale=dr.scale, area_unit="px2")
    excluded = excluded_rooms(dr)   # 코어·작은 기타 노이즈 제외
    ridx = _RoomIndex(dr.rooms, exclude=excluded)

    # 노드: 공간
    win_count: dict[int, int] = {}
    win_len: dict[int, float] = {}
    objs: dict[int, list[str]] = {}
    _attach_windows(dr, ridx, win_count, win_len)
    _attach_objects(dr, ridx, objs)

    for i, r in enumerate(dr.rooms):
        if i in excluded:
            continue
        rtype = r.class_name.replace("공간_", "")
        G.add_node(
            i, type=rtype, raw_class=r.class_name,
            hierarchy=config.HIERARCHY.get(r.class_name),
            area_px=round(r.area_px, 1),
            centroid=[round(r.centroid[0], 1), round(r.centroid[1], 1)]
            if r.centroid else None,
            n_windows=win_count.get(i, 0),
            window_len_px=round(win_len.get(i, 0.0), 1),
            objects=objs.get(i, []),
            is_entrance=(r.class_name == config.ENTRANCE_CLASS),
        )

    # 엣지: 문
    n_unresolved = 0
    door_edges = 0
    ext_doors = 0
    for d in dr.doors:
        rooms = _rooms_for_door(d, ridx)
        dtype = d.subtype or "출입문"
        if len(rooms) >= 2:
            a, b = rooms[0], rooms[1]
            if a != b:
                _add_door_edge(G, a, b, dtype)
                door_edges += 1
        elif len(rooms) == 1:
            # 한쪽만 잡힘 → 외부문(현관·발코니문 등)
            G.add_edge(rooms[0], EXTERIOR, via="exterior_door", door_type=dtype)
            ext_doors += 1
            G.nodes  # exterior 노드는 add_edge로 자동 생성
        else:
            n_unresolved += 1

    # 엣지: 발코니 슬라이딩 창호 → 통로(via=balcony)
    #   창호도 문과 동일하게 법선 probe. 양쪽 중 한쪽이 발코니/실외기실이면 통로로 승격.
    win_edges = 0
    for wd in dr.windows:
        rooms = _rooms_for_door(wd, ridx)
        if len(rooms) < 2:
            continue
        a, b = rooms[0], rooms[1]
        if a == b:
            continue
        ca = dr.rooms[a].class_name
        cb = dr.rooms[b].class_name
        if ca in config.WINDOW_PASSAGE_CLASSES or cb in config.WINDOW_PASSAGE_CLASSES:
            if not G.has_edge(a, b):
                G.add_edge(a, b, via="balcony", door_type=wd.subtype or "미닫이창")
                win_edges += 1

    # 엣지: 개방통로(via=open) — 문·발코니창 없이 벽이 끊긴 개구부(개방형 LDK 등)
    open_edges = 0
    for a, b in open_passages(dr, exclude=excluded):
        if not G.has_edge(a, b):
            G.add_edge(a, b, via="open", door_type=None)
            open_edges += 1

    # 현관 → 외부 가상 엣지(진입점)
    for i, r in enumerate(dr.rooms):
        if r.class_name == config.ENTRANCE_CLASS:
            if not G.has_edge(i, EXTERIOR):
                G.add_edge(i, EXTERIOR, via="entrance", door_type=None)

    if G.has_node(EXTERIOR):
        G.nodes[EXTERIOR].update(type="exterior", hierarchy=None,
                                 centroid=_exterior_pos(dr))

    G.graph.update(n_rooms=G.number_of_nodes() - (1 if G.has_node(EXTERIOR) else 0),
                   n_rooms_raw=len(dr.rooms), n_excluded=len(excluded),
                   n_doors=len(dr.doors),
                   n_door_edges=door_edges, n_exterior_doors=ext_doors,
                   n_balcony_edges=win_edges, n_open_edges=open_edges,
                   n_unresolved_doors=n_unresolved)
    return G


def _add_door_edge(G: nx.Graph, a: int, b: int, dtype: str) -> None:
    """두 방 사이 문 엣지 추가. 이미 있으면 문 개수만 증가."""
    if G.has_edge(a, b):
        G[a][b]["n_doors"] = G[a][b].get("n_doors", 1) + 1
    else:
        G.add_edge(a, b, via="door", door_type=dtype, n_doors=1)


def _exterior_pos(dr: Drawing) -> list[float]:
    """exterior 노드를 도면 바깥(상단 중앙)에 배치(시각화용)."""
    return [dr.width / 2 if dr.width else 0.0, -dr.height * 0.05 if dr.height else 0.0]


# ─────────────────────────────────────────────────────────────────────────────
# 세대(unit) 분해
#   ⚠️ AI-Hub FP 이미지는 단일 주거가 아니라 여러 세대가 타일된 발표 시트다
#   (현관·거실이 2~4개). 문 그래프의 연결요소가 곧 세대 경계(세대끼리 문 공유 안 함).
#   '진짜 세대' = 현관을 가진 연결요소. 현관 없는 작은 조각(공간_기타 단편 등)은 노이즈.
# ─────────────────────────────────────────────────────────────────────────────
def iter_units(G: nx.Graph, min_rooms: int = 2):
    """시트 그래프 → 세대별 서브그래프 yield. 각 세대는 자기 현관·exterior 포함.
    반환 부산물: (unit_subgraph, dropped_noise_components_info).
    """
    spaces = _space_nodes(G)
    sub = G.subgraph(spaces)
    units = []
    noise = []  # 현관 없는/너무 작은 조각
    for comp in nx.connected_components(sub):
        comp = set(comp)
        has_entr = any(G.nodes[n].get("is_entrance") for n in comp)
        if has_entr and len(comp) >= min_rooms:
            # 세대 노드 + (이 세대 현관의) exterior 엣지 포함
            nodes = comp | {EXTERIOR} if G.has_node(EXTERIOR) else comp
            U = G.subgraph(nodes).copy()
            # 다른 세대 현관과 exterior 사이 엣지는 subgraph가 자동 제외(노드 없음)
            U.graph = {k: G.graph.get(k) for k in (
                "graph_id", "house_type", "scale", "area_unit")}
            U.graph["n_rooms"] = len(comp)
            U.graph["n_doors"] = sum(
                1 for u, v, d in U.edges(data=True) if d.get("via") == "door")
            U.graph["n_balcony_edges"] = sum(
                1 for u, v, d in U.edges(data=True) if d.get("via") == "balcony")
            U.graph["n_unresolved_doors"] = 0
            units.append(U)
        else:
            noise.append({"size": len(comp),
                          "types": [G.nodes[n].get("type") for n in comp]})
    return units, noise


# ─────────────────────────────────────────────────────────────────────────────
# 직렬화 (배치 그래프 node-link)
# ─────────────────────────────────────────────────────────────────────────────
def to_node_link(G: nx.Graph) -> dict:
    return nx.node_link_data(G, edges="edges")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    from plan2graph.coco import load_coco
    from plan2graph.geometry import assemble_drawing
    # 사용법: topology.py <label1.json> [label2.json ...]
    docs = [load_coco(p) for p in sys.argv[1:]]
    dr = assemble_drawing(docs)
    G = build_graph(dr, graph_id="demo")
    print(f"그래프: 노드 {G.number_of_nodes()} 엣지 {G.number_of_edges()}")
    print(f"  방={G.graph['n_rooms']} 문={G.graph['n_doors']} "
          f"문엣지={G.graph['n_door_edges']} 외부문={G.graph['n_exterior_doors']} "
          f"미해소={G.graph['n_unresolved_doors']}")
    for u, v, d in list(G.edges(data=True))[:12]:
        tu = G.nodes[u].get("type", u)
        tv = G.nodes[v].get("type", v)
        print(f"    {tu}({u}) - {tv}({v})  via={d.get('via')} {d.get('door_type') or ''}")
