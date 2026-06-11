"""기하 입력·검증 헬퍼 — 생성형 기하 AI(geom_gen)의 방 구성·인접 시드·결과 검증.

program/위상 → rooms[(role, area_frac, nwin)]·관례 인접 edges 를 만들고(생성 AI 입력),
AI가 낸 박스의 인접 실현율·겹침을 검증한다. (옛 treemap 배치 `correct`는 폐기 — 좌표는 geom_gen이 생성.)
"""
from __future__ import annotations


def role_area_priors(version="g0"):
    """g0 실측에서 역할별 정규화 면적 중앙값 — 생성 시 방 크기 prior."""
    import json
    import statistics
    import config
    f = config.release_dir(version) / "geom.jsonl"
    acc = collections.defaultdict(list)
    if f.exists():
        for ln in f.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            rooms = json.loads(ln).get("rooms", {})
            amax = max((r.get("area_px", 0) or 0) for r in rooms.values()) or 1
            for r in rooms.values():
                acc[r["role"]].append((r.get("area_px", 0) or 0) / amax)
    return {role: statistics.median(v) for role, v in acc.items() if v}


_NWIN = {"거실": 1, "안방": 1, "침실": 1, "주방": 1}


def program_to_rooms(program: dict, priors: dict):
    """program {역할:개수} → rooms [(role, area_frac, nwin)] (g0 면적 prior)."""
    rooms = []
    for role, cnt in program.items():
        for _ in range(int(cnt)):
            rooms.append((role, priors.get(role, 0.05), _NWIN.get(role, 0)))
    return rooms


def tline_graph_to_rooms(G, priors: dict):
    """T-라인 위상그래프(networkx) → (rooms, edges) — 생성형 기하 AI(geom_gen) 입력.

    rooms=[(role, area_frac, nwin)], edges=room-index 쌍(외부 노드 제외).
    T 위상의 본질(neural 그래프)을 보존하고 좌표만 AI가 생성 — treemap 무관.
    """
    from plan2graph.topology import EXTERIOR
    from plan2graph.floorgeom import _type
    nodes = [n for n in G.nodes if n != EXTERIOR]
    idx = {n: i for i, n in enumerate(nodes)}
    rooms = [(((_type(G.nodes[n]) or "기타").replace("공간_", "")),
              priors.get((_type(G.nodes[n]) or "기타").replace("공간_", ""), 0.05),
              _NWIN.get((_type(G.nodes[n]) or "기타").replace("공간_", ""), 0))
             for n in nodes]
    edges = [(idx[u], idx[v]) for u, v in G.edges
             if u != EXTERIOR and v != EXTERIOR and u in idx and v in idx]
    return rooms, edges


def convention_edges(rooms):
    """관례 인접: 현관↔거실, 거실↔나머지(거실 허브 star). 인접 구조 시드."""
    roles = [r[0] for r in rooms]
    if not roles:
        return []
    hub = next((i for i, r in enumerate(roles) if r == "거실"), 0)
    edges = [(hub, i) for i in range(len(roles)) if i != hub]
    return edges


def _adjacent(b1, b2, tol=0.01):
    """두 박스가 벽을 공유(인접)하는가 — 한 축 겹침>0 & 다른 축 간격~0."""
    ax0, ax1 = b1[0] - b1[2] / 2, b1[0] + b1[2] / 2
    ay0, ay1 = b1[1] - b1[3] / 2, b1[1] + b1[3] / 2
    bx0, bx1 = b2[0] - b2[2] / 2, b2[0] + b2[2] / 2
    by0, by1 = b2[1] - b2[3] / 2, b2[1] + b2[3] / 2
    ox = min(ax1, bx1) - max(ax0, bx0)     # x 겹침
    oy = min(ay1, by1) - max(ay0, by0)     # y 겹침
    share_v = ox > tol and abs(oy) < tol * 3   # 좌우 이웃(세로벽 공유)
    share_h = oy > tol and abs(ox) < tol * 3   # 상하 이웃(가로벽 공유)
    return share_v or share_h or (ox > tol and oy > tol)


def verify(rooms, edges, boxes):
    """자기교정 결과 검증 — 요구 인접 실현율·겹침수. 실패 엣지 목록(→위상 라우팅)."""
    miss = [(i, j) for i, j in edges if not _adjacent(boxes[i], boxes[j])]
    n_overlap = 0
    for a in range(len(boxes)):
        for b in range(a + 1, len(boxes)):
            ba, bb = boxes[a], boxes[b]
            ox = min(ba[0] + ba[2] / 2, bb[0] + bb[2] / 2) - max(ba[0] - ba[2] / 2, bb[0] - bb[2] / 2)
            oy = min(ba[1] + ba[3] / 2, bb[1] + bb[3] / 2) - max(ba[1] - ba[3] / 2, bb[1] - bb[3] / 2)
            if ox > 0.02 and oy > 0.02:
                n_overlap += 1
    adj_rate = 1 - len(miss) / len(edges) if edges else 1.0
    return {"adj_rate": round(adj_rate, 3), "missing_adj": miss,
            "n_overlap": n_overlap, "n_edges": len(edges)}


if __name__ == "__main__":   # g0 한 세대의 위상으로 자기교정→렌더(겹침없는 도면 확인)
    import json
    import sys
    from pathlib import Path
    _root = Path(__file__).resolve().parents[2]
    for _p in (str(_root), str(_root / "src")):
        if _p not in sys.path:
            sys.path.insert(0, _p)
    import config
    from plan2graph import geom_gen

    line = (config.release_dir("g0") / "geom.jsonl").read_text(
        encoding="utf-8").splitlines()
    g = json.loads(line[3])
    ids = list(g["rooms"])
    amax = max((g["rooms"][i].get("area_px", 0) or 0) for i in ids) or 1
    rooms = [(g["rooms"][i]["role"], (g["rooms"][i].get("area_px", 0) or 0) / amax,
              g["rooms"][i].get("n_windows", 0)) for i in ids]
    ix = {nid: k for k, nid in enumerate(ids)}
    edges = [(ix[e["from"]], ix[e["to"]]) for e in g["edges"]
             if e["from"] in ix and e["to"] in ix]
    boxes = geom_gen.generate(geom_gen.load("geom_g0"), rooms)   # 생성형 기하 AI(treemap 아님)
    v = verify(rooms, edges, boxes)
    png = geom_gen.render(rooms, boxes)
    out = Path("artifacts") / "geom_gen_sample.png"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(png)
    print(f"{g['unit_id']} rooms={len(rooms)} edges={len(edges)}  verify={v}")
    print("saved", out, len(png), "B")
