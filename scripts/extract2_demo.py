"""Consolidation 데모 — AI-Hub 한 세대를 정교한 enriched 위상으로 추출.

조각(모두 u0서 개별 증명됨)을 하나로:
  1) SPA+STR+OBJ(+OCR) 4라벨 지문 병합
  2) 연결공간(복도/전실) 음의공간 복원
  3) 문 재배정 — 법선 probe에 연결공간 포함(가짜 침실-침실 제거)
  4) 기구(OBJ) → 방 귀속 → 욕실(욕조)/화장실, 주방(싱크/레인지) 확정
  5) 개방연결(LDK) open_passages
  6) 역할 유도 — 안방(드레스룸+wet에 닿는 최대 침실), 전용/공용 욕실
출력: enriched 그래프 + 렌더(scripts 출력 PNG).

사용: python scripts/extract2_demo.py <SHEET_ID> <UNIT_GRAPH_JSON>
"""
import sys
import json
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
matplotlib.rcParams["font.family"] = "NanumGothic"
matplotlib.rcParams["axes.unicode_minus"] = False
import networkx as nx
from shapely.geometry import Point
from shapely.ops import unary_union

import config
from plan2graph.build_aihub import scan_sources
from plan2graph import review as rv
from plan2graph.topology import (_door_normal, open_passages, build_graph,
                                 iter_units, EXTERIOR)

PXM2 = 5390.0  # px^2 per m^2 (이 시트 근사 — 면적표시용)


def load_four_labels(sheet_id):
    """SPA/STR/OBJ/OCR 4라벨을 지문으로 모아 Drawing 조립."""
    sig, entries = scan_sources()
    fpmap = defaultdict(dict)
    for (s, label, key, house) in entries:
        fpmap[s][label] = key
    fp = rv.fingerprint_of(sheet_id)
    keys = fpmap.get(fp, {})
    idx = rv.build_indices(("Training", "Validation"))
    docs, loaded = [], []
    for label, key in keys.items():
        for sp in ("Training", "Validation"):
            ie = idx.label_entry.get((sp, label, key))
            if ie:
                docs.append(rv.load_coco_bytes(rv._read_zip(*ie), source=ie[1]))
                loaded.append(label)
                break
    return rv.assemble_drawing(docs), loaded


def unit_room_indices(dr, sheet_id, unit_i):
    """정식 유닛 분할: build_graph→iter_units → 유닛 k의 방 노드 id(=dr.room 인덱스)."""
    G0 = build_graph(dr, graph_id=sheet_id)
    units, _ = iter_units(G0, min_rooms=config.ACCEPT_MIN_ROOMS - 3)
    U = units[unit_i]
    return [n for n in U.nodes() if n != EXTERIOR and isinstance(n, int)]


def recover_connectors(union):
    """방 union 음의공간 → 연결공간(복도/전실) 폴리곤들."""
    conn = union.buffer(55).buffer(-55).difference(union).buffer(-22).buffer(22)
    parts = [conn] if conn.geom_type == "Polygon" else list(conn.geoms)
    parts = [p for p in parts if p.area > 1500]
    if not parts:
        return []
    m = unary_union([p.buffer(30) for p in parts])
    return [m] if m.geom_type == "Polygon" else list(m.geoms)


def fixtures_in(dr, poly):
    return [o.class_name.replace("객체_", "") for o in dr.objects
            if o.centroid and poly.contains(Point(*o.centroid))]


def assign_door(door, spaces):
    """문 법선 양방향 probe → 닿는 두 공간(방 or 연결공간) id."""
    nrm = _door_normal(door)
    if not nrm or not door.centroid:
        return []
    (nxv, nyv), slen = nrm
    cx, cy = door.centroid
    found = []
    for sign in (1, -1):
        for dd in (slen * 0.6, slen * 0.6 + 35, slen * 0.6 + 75, slen * 0.6 + 120):
            px, py = cx + nxv * dd * sign, cy + nyv * dd * sign
            best, bd = None, 30
            for sid, poly in spaces:
                z = poly.distance(Point(px, py))
                if z < bd:
                    best, bd = sid, z
            if best is not None:
                if best not in found:
                    found.append(best)
                break
    return found[:2]


def extract_unit(dr, u_idx):
    """dr + 유닛 방 인덱스 → enriched nx.Graph."""
    rooms = {di: dr.rooms[di] for di in u_idx}
    union = unary_union([r.polygon for r in rooms.values()])
    connectors = recover_connectors(union)

    G = nx.Graph()
    # 방 노드 + 기구·욕실/화장실
    for di, r in rooms.items():
        base = r.class_name.replace("공간_", "")
        fx = fixtures_in(dr, r.polygon)
        role = base
        if base == "화장실":
            role = "욕실" if any("욕조" in f for f in fx) else "화장실"
        elif base == "주방" or any(f in ("싱크대", "가스레인지") for f in fx):
            role = "주방"
        G.add_node(di, base=base, role=role, fx=fx,
                   area=round(r.polygon.area / PXM2, 1))
    # 연결공간 노드
    cbase = 100000
    for j, p in enumerate(connectors):
        G.add_node(cbase + j, base="복도", role="복도", fx=[],
                   area=round(p.area / PXM2, 1))

    spaces = [(di, rooms[di].polygon) for di in rooms] + \
             [(cbase + j, p) for j, p in enumerate(connectors)]
    # 문 재배정
    doors = [d for d in dr.doors if d.polygon and union.distance(d.polygon) < 60 and d.centroid]
    for d in doors:
        f = assign_door(d, spaces)
        if len(f) == 2:
            G.add_edge(f[0], f[1], via="door")
    # 연결공간끼리 인접 → 복도 연결성
    for a in range(len(connectors)):
        for b in range(a + 1, len(connectors)):
            if connectors[a].buffer(20).intersects(connectors[b]):
                G.add_edge(cbase + a, cbase + b, via="corridor")
    # 개방연결(LDK) — dr 인덱스 기준
    for i, j in open_passages(dr):
        if i in rooms and j in rooms and not G.has_edge(i, j):
            G.add_edge(i, j, via="open")

    # 역할: 안방 = 드레스룸 + (욕실/화장실)에 (직접 or 복도경유) 닿는 최대 침실
    def nbases(nid):
        out = set()
        for m in G.neighbors(nid):
            out.add(G.nodes[m]["role"])
            if G.nodes[m]["base"] == "복도":
                for k in G.neighbors(m):
                    out.add(G.nodes[k]["role"])
        return out
    master = None
    for di, r in rooms.items():
        if G.nodes[di]["base"] == "침실":
            nb = nbases(di)
            if "드레스룸" in nb and ({"욕실", "화장실"} & nb):
                if master is None or G.nodes[di]["area"] > G.nodes[master]["area"]:
                    master = di
    if master is not None:
        G.nodes[master]["role"] = "안방"
        # 전용욕실: 안방에 (복도경유 포함) 닿는 욕실/화장실 중 방이웃이 안방쪽뿐
        for m in list(G.neighbors(master)):
            if G.nodes[m]["role"] in ("욕실", "화장실"):
                G.nodes[m]["role"] = "전용" + G.nodes[m]["role"]
    return G


def render(G, path):
    col = {"침실": "#6CA6E8", "안방": "#1f6fd6", "거실": "#E8453C", "주방": "#F2A900",
           "화장실": "#9C6", "욕실": "#3a3", "전용욕실": "#185", "전용화장실": "#7a4",
           "현관": "#d44", "발코니": "#2DBE60", "드레스룸": "#a070d0",
           "복도": "#888", "기타": "#bbb", "실외기실": "#9cf", "다목적공간": "#fc8"}
    lab = {n: (G.nodes[n]["role"] or G.nodes[n]["base"]) for n in G}
    fig, ax = plt.subplots(figsize=(12, 9))
    pos = nx.kamada_kawai_layout(G)
    ecol = {"door": "#333", "open": "#F2A900", "corridor": "#888"}
    for via in ("door", "open", "corridor"):
        es = [(u, v) for u, v, d in G.edges(data=True) if d.get("via") == via]
        nx.draw_networkx_edges(G, pos, edgelist=es, ax=ax,
                               edge_color=ecol[via], width=2,
                               style="dashed" if via == "corridor" else "solid")
    nx.draw_networkx_nodes(G, pos, ax=ax, node_size=1700,
                           node_color=[col.get(lab[n], "#ccc") for n in G])
    nx.draw_networkx_labels(G, pos, labels=lab, ax=ax, font_size=9,
                            font_family="NanumGothic")
    ax.set_title("u0 enriched 위상 — 복도경유·욕실/화장실·안방 (문=검정 open=주황 복도=점선)")
    ax.axis("off")
    fig.savefig(path, dpi=100, bbox_inches="tight")


def main():
    sheet_id = sys.argv[1]
    unit_i = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    dr, loaded = load_four_labels(sheet_id)
    print(f"로드 라벨: {loaded} | rooms={len(dr.rooms)} objects={len(dr.objects)} texts={len(dr.texts)}")
    u_idx = unit_room_indices(dr, sheet_id, unit_i)
    G = extract_unit(dr, u_idx)
    print(f"유닛 방 {len(u_idx)} → 노드 {G.number_of_nodes()} 엣지 {G.number_of_edges()}")
    bb = sum(1 for u, v in G.edges if G.nodes[u]["base"] == "침실" and G.nodes[v]["base"] == "침실")
    print(f"침실-침실 직접: {bb}")
    print("노드:", sorted((G.nodes[n]["role"], G.nodes[n].get("fx")) for n in G))
    print("엣지:", sorted((G.nodes[u]["role"], G.nodes[v]["role"], d["via"]) for u, v, d in G.edges(data=True)))
    render(G, "artifacts/u0_enriched.png")
    print("saved artifacts/u0_enriched.png")


if __name__ == "__main__":
    main()
