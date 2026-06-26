"""Verifier-guided generation (논문 Figure 1 뒤단) — geometric + regulatory verify + best-of-N.

생성 geomgraph(canon_to_graph 출력) → typed nx.Graph → 법규(rules_legal) + 기하(strict clean) 검증
→ 통과만 채택(rejection sampling). repair는 graph_repair로 사전 적용.
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src"), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import networkx as nx
from shapely.geometry import Polygon

from plan2graph.rules_legal import check_legal
from plan2graph.topology import EXTERIOR
from plan2graph.graph_repair import repair_graph

try:
    import render_geomclean as _RG  # strict geometric verifier (도면답게)
    _is_clean = _RG.is_clean
except Exception:
    _is_clean = None


def geomgraph_to_nx(g: dict) -> nx.Graph:
    """생성 geomgraph dict → typed 인접 그래프(법규 검증용). 노드=방(type·area·n_windows), 엣지=문 인접."""
    G = nx.Graph(scale=g.get("scale_mm_per_px"))
    rooms = g.get("rooms") or {}

    def _nid(k):
        return int(k) if str(k).lstrip("-").isdigit() else k

    for k, r in rooms.items():
        poly = r.get("polygon") or []
        try:
            area = round(Polygon(poly).area, 1) if len(poly) >= 3 else 0.0
        except Exception:
            area = 0.0
        role = r.get("role") or r.get("base") or "기타"
        nwin = r.get("n_windows")
        if nwin is None:
            nwin = len(r.get("window_ids") or [])
        G.add_node(_nid(k), type=role, area_px=area, n_windows=nwin,
                   is_entrance=(role == "현관"))
    for d in (g.get("doors") or []):
        cn = d.get("connects") or d.get("rooms")
        if cn and len(cn) == 2:
            a, b = _nid(cn[0]), _nid(cn[1])
            if a in G and b in G:
                G.add_edge(a, b, via="door", door_type=d.get("subtype"))
    # 현관 → 외부(egress 경로용)
    for n, d in list(G.nodes(data=True)):
        if d.get("is_entrance"):
            G.add_edge(n, EXTERIOR, via="entrance")
    return G


def verify_plan(g: dict) -> dict:
    """단일 생성물 검증. geometric(strict clean) + regulatory(법규). 반환 pass/fail + 위반."""
    geom_ok = True
    if _is_clean is not None:
        try:
            geom_ok, _ = _is_clean(g)
        except Exception:
            geom_ok = False
    try:
        legal = check_legal(geomgraph_to_nx(g))
    except Exception as e:
        legal = {"passed": False, "violations": [{"rule": "legal_err", "msg": str(e)[:80]}],
                 "applied_rules": [], "scale_available": False}
    return {"geom_ok": bool(geom_ok), "legal_ok": bool(legal["passed"]),
            "both_ok": bool(geom_ok) and bool(legal["passed"]),
            "legal_violations": legal.get("violations", []),
            "legal_applied": legal.get("applied_rules", [])}


_HAB_ROLES = {"거실", "침실", "안방"}


def _room_exterior_edges(rooms, rid):
    """방 rid의 외벽 변(다른 방과 공유 안 하는 변) — 창 추가 위치 후보."""
    from collections import Counter
    cnt = Counter()
    for k, r in rooms.items():
        poly = r.get("polygon") or []
        n = len(poly)
        for i in range(n):
            a = (round(float(poly[i][0]), 1), round(float(poly[i][1]), 1))
            b = (round(float(poly[(i + 1) % n][0]), 1), round(float(poly[(i + 1) % n][1]), 1))
            if a != b:
                cnt[tuple(sorted([a, b]))] += 1
    ext = []
    poly = rooms[rid].get("polygon") or []
    n = len(poly)
    for i in range(n):
        a = (round(float(poly[i][0]), 1), round(float(poly[i][1]), 1))
        b = (round(float(poly[(i + 1) % n][0]), 1), round(float(poly[(i + 1) % n][1]), 1))
        if a != b and cnt[tuple(sorted([a, b]))] == 1:
            ext.append((a, b))
    return ext


def legal_repair(g):
    """규제 위반(채광 L1)을 *도면에 반영*: 창 없는 거실·침실의 외벽에 채광창 추가.
    → AI 생성물에 규제 피드백을 적용해 개선(neuro-symbolic). 반환=수행한 조치 목록."""
    import math
    actions = []
    rooms = g.get("rooms") or {}
    g.setdefault("windows", [])
    wid = len(g["windows"])
    for k, r in rooms.items():
        if r.get("role") not in _HAB_ROLES:
            continue
        if len(r.get("window_ids") or []) >= 1 or r.get("n_windows"):
            continue
        ext = _room_exterior_edges(rooms, k)
        if not ext:
            actions.append({"room": k, "role": r.get("role"), "ok": False,
                            "msg": f"{r.get('role')}#{k}: 외벽 없음(내부 방) → 창 추가 불가"})
            continue
        a, b = max(ext, key=lambda e: (e[0][0] - e[1][0]) ** 2 + (e[0][1] - e[1][1]) ** 2)
        mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
        wlen = max(24.0, min(80.0, math.hypot(b[0] - a[0], b[1] - a[1]) * 0.6))
        ang = 90.0 if abs(b[0] - a[0]) < abs(b[1] - a[1]) else 0.0
        wname = f"winR{wid}"
        g["windows"].append({"id": wname, "belongs_to": k, "position": [mx, my],
                             "width_px": round(wlen, 1), "on_wall": None, "orientation_deg": ang})
        r.setdefault("window_ids", []).append(wname)
        actions.append({"room": k, "role": r.get("role"), "ok": True,
                        "msg": f"{r.get('role')}#{k}: 외벽에 채광창 추가"})
        wid += 1
    return actions


def _room_segs(rooms, rid):
    """방 rid 폴리곤의 변(x1,y1,x2,y2) 목록."""
    poly = rooms[rid].get("polygon") or []
    n = len(poly); out = []
    for i in range(n):
        out.append((float(poly[i][0]), float(poly[i][1]),
                    float(poly[(i + 1) % n][0]), float(poly[(i + 1) % n][1])))
    return out


def _adjacent_door_pos(segs_a, segs_b, tol=3.0, minlen=8.0):
    """두 방이 공유하는 벽 구간(collinear+overlap) 있으면 문 위치(중점) 반환, 없으면 None."""
    for (ax1, ay1, ax2, ay2) in segs_a:
        for (bx1, by1, bx2, by2) in segs_b:
            if abs(ax1 - ax2) < 1 and abs(bx1 - bx2) < 1 and abs(ax1 - bx1) < tol:   # 둘 다 수직, 같은 x
                lo = max(min(ay1, ay2), min(by1, by2)); hi = min(max(ay1, ay2), max(by1, by2))
                if hi - lo > minlen:
                    return [round((ax1 + bx1) / 2, 1), round((lo + hi) / 2, 1)]
            if abs(ay1 - ay2) < 1 and abs(by1 - by2) < 1 and abs(ay1 - by1) < tol:   # 둘 다 수평, 같은 y
                lo = max(min(ax1, ax2), min(bx1, bx2)); hi = min(max(ax1, ax2), max(bx1, bx2))
                if hi - lo > minlen:
                    return [round((lo + hi) / 2, 1), round((ay1 + by1) / 2, 1)]
    return None


def egress_repair(g):
    """규제 위반(동선 L3)을 *도면에 반영*: 현관에서 도달 못 하는 고립 실을,
    벽을 공유하는 '연결된 실'과 문(door)으로 이어 피난 동선을 복원. 반환=조치 목록."""
    actions = []
    rooms = g.get("rooms") or {}
    if not rooms:
        return actions
    G = geomgraph_to_nx(g)
    if EXTERIOR not in G:
        actions.append({"ok": False, "msg": "현관 없음 → 외부 피난경로 자체가 없어 동선 보정 불가"})
        return actions
    g.setdefault("doors", [])
    did = len(g["doors"])
    keymap = {}                                   # G 노드(int) → room key(str)
    for rk in rooms:
        nid = int(rk) if str(rk).lstrip("-").isdigit() else rk
        keymap[nid] = rk
    scache = {rk: _room_segs(rooms, rk) for rk in rooms}
    for _ in range(len(rooms) + 2):
        reach = nx.node_connected_component(G, EXTERIOR)
        iso = [n for n in G.nodes() if n != EXTERIOR and n not in reach]
        if not iso:
            break
        progressed = False
        for n in iso:
            rk_n = keymap.get(n)
            if rk_n is None:
                continue
            for m_node in list(reach):
                if m_node == EXTERIOR or keymap.get(m_node) is None:
                    continue
                rk_m = keymap[m_node]
                pos = _adjacent_door_pos(scache[rk_n], scache[rk_m])
                if pos:
                    g["doors"].append({"id": f"dR{did}", "connects": [n, m_node], "via": "door", "position": pos})
                    G.add_edge(n, m_node, via="door"); did += 1; progressed = True
                    actions.append({"ok": True,
                                    "msg": f"{rooms[rk_n].get('role')}#{n} ↔ {rooms[rk_m].get('role')}#{m_node} 문 추가(동선 연결)"})
                    break
            if progressed:
                break
        if not progressed:
            actions.append({"ok": False, "msg": "벽 공유 없는 고립 실 잔존 → 추가 연결 불가(배치 결함)"})
            break
    return actions


def best_of_n(gen_fn, n: int = 8, repair: bool = True):
    """rejection sampling: n개 생성→repair→verify. 채택(both_ok)·통계·best 반환.
    gen_fn() → geomgraph dict (또는 None). 논문 §4.5 draw-budget.
    """
    cand = []
    stat = dict(total=0, decoded=0, geom_pass=0, legal_pass=0, both_pass=0)
    for _ in range(n):
        stat["total"] += 1
        try:
            g = gen_fn()
        except Exception:
            g = None
        if not g or not (g.get("rooms")):
            continue
        stat["decoded"] += 1
        if repair:
            try:
                repair_graph(g, drop_bad=True, declash="wall")
            except Exception:
                pass
        v = verify_plan(g)
        if v["geom_ok"]:
            stat["geom_pass"] += 1
        if v["legal_ok"]:
            stat["legal_pass"] += 1
        if v["both_ok"]:
            stat["both_pass"] += 1
        cand.append((g, v))
    accepted = [(g, v) for g, v in cand if v["both_ok"]]
    # best: both_ok 우선, 없으면 geom_ok, 없으면 위반 최소
    if accepted:
        best = accepted[0]
    else:
        cand.sort(key=lambda gv: (not gv[1]["geom_ok"], len(gv[1]["legal_violations"])))
        best = cand[0] if cand else None
    p = stat["both_pass"] / stat["total"] if stat["total"] else 0.0
    stat["pass_rate"] = round(100 * p, 1)
    stat["expected_draws"] = round(1.0 / p, 1) if p > 0 else None
    return dict(accepted=accepted, best=best, candidates=cand, stat=stat)
