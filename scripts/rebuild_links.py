#!/usr/bin/env python3
"""rebuild_links — **연결층을 보정된 기하에서 처음부터 다시 만든다.**

사용자 결정(2026-11-13): *"이미 AI-Hub가 제공하는 것도 있고 우리가 만든 것도 있을 텐데…
사실 무시하고 우리가 새로 만들어도 되는 것 아닌가. 그들이 주는 값은 이것만 활용하고
우리가 새롭게 만들었다, 이런 식이 나을 것 같다."*

확인한 사실: AI-Hub 라벨은 **순수 COCO**(`segmentation`/`bbox`/`category_id`/`attributes`)다.
`관계` 필드가 하나도 없다. 즉 **AI-Hub가 준 것**은
  · 방 폴리곤 + 역할(`공간_*`)
  · 벽체·창호·출입문 폴리곤 + 종류(`여닫이문`/`미닫이문`/`미닫이창`…)
  · 기구 bbox + rotation · OCR 라벨
**우리가 만든 것**은 그 위의 연결층 전부 — `walls[].rooms`, `walls[].openings`,
`doors[].connects`, `edges`, `privacy_transition`, `dist_from_entrance`.

왜 다시 만들어야 하나: 연결층은 **파싱 때 한 번, 보정 전 폴리곤으로** 계산되고 그 뒤
갱신되지 않는다. 실측(APT_FP_396cb308_17447720_u3): 문0이 올라탄 벽 w14 는 길이 21px 짜리
조각인데 `rooms:[70,72]`(침실+침실)이고, `doors[0].connects`가 그걸 그대로 베꼈다.
에디터는 이 필드를 화면에 띄우지 않으니 **사람이 검수해도 잡히지 않았다.**
방-방 직결을 고치려고 만든 프로그램인데 정작 그 원인을 손댈 수 없었던 셈이다.

방법(기하가 유일한 근거):
  1) **벽 양쪽을 찔러본다** — 벽 선분을 여러 지점에서 법선 방향으로 `두께/2 + 여유`만큼
     벗어난 점이 어느 방 안에 들어가는지 다수결. → `walls[].rooms` 재산출
  2) **문은 올라탄 벽의 양쪽**을 잇는다. 벽에서 못 풀면 문 상자를 키워 넓이순 두 방.
  3) **중복 문 제거** — 같은 상자가 벽 조각마다 한 레코드씩 생겼다(문1=w19·문2=w20).
     상자 기준으로 묶어 **한 문만** 남긴다. 상자 없는 빈 레코드는 버린다.
  4) **엣지 재생성** — 문 엣지는 문에서, 오픈 엣지는 `type=open` 벽에서. 같은 방 쌍이면
     문이 이긴다. `privacy_transition`·`dist_from_entrance`는 새 그래프로 다시 계산.

폭이 좁은 문(기본 25px 미만 = 약 420mm)은 **지우지 않고 `suspect: true`로 표시**한다.
사람이 보고 판단할 몫이다.

읽기 전용(기본). --apply 를 줘야 쓴다.
usage:
  python scripts/rebuild_links.py --plans APT_FP_396cb308_17447720_u3
  python scripts/rebuild_links.py --sample 2000
  python scripts/rebuild_links.py --plans A B --apply
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import math
import os
import random
import shutil
import sys
import time as _time

from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDITS = os.path.join(ROOT, "data", "staging", "corrected", "edits")
BK = os.path.join(ROOT, "data", "staging", "corrected", "_manual_backup")
# 사용자 확정값은 git 에 들어가는 자리에 둔다 — data/staging 은 .gitignore 대상이라
# 거기 두면 사람이 본 사실이 버전관리에서 빠진다.
OVR = os.path.join(ROOT, "data", "link_overrides.json")
OVR_OLD = os.path.join(ROOT, "data", "staging", "corrected", "_link_overrides.json")
sys.path.insert(0, os.path.join(ROOT, "src"))

try:
    from plan2graph.geomgraph import PRIVACY
except Exception:                                    # 단독 실행 대비
    PRIVACY = {}

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import role_rules
except Exception:
    role_rules = None

FRACS = (0.2, 0.35, 0.5, 0.65, 0.8)
MARGIN = 4.0          # 벽면에서 더 들어가는 여유(px)
PAD = 10.0            # 벽으로 못 풀 때 문 상자를 키우는 폭(px)
THIN_DOOR_PX = 25.0   # 이보다 좁은 문은 의심 표시
OPEN_MIN = 20.0       # 벽이 덮지 않은 구간이 이보다 길면 개방으로 본다
WIN_PAD = 14.0        # 창 상자를 키우는 폭(px) — 벽을 건너가도록
OUTDOOR_ROLES = ("발코니", "실외기실", "테라스")   # 집 안이 아닌 공간
AXIS_LEN = 300.0      # 이보다 긴 벽 레코드는 제도 축선으로 보고 막힘에서 제외
WIN_RATIO = 0.30      # 두 번째 방의 겹침이 첫 번째의 이 비율 미만이면 스친 것


def polys_of(g):
    out = {}
    for rid, r in (g.get("rooms") or {}).items():
        pts = r.get("polygon") or []
        if len(pts) < 3:
            continue
        q = Polygon([(x, y) for x, y in pts])
        if not q.is_valid:
            q = q.buffer(0)
        if not q.is_empty and q.geom_type == "Polygon":
            out[str(rid)] = q
    return out


def hit(polys, x, y, slack=2.0):
    p = Point(x, y)
    for rid, q in polys.items():
        if q.contains(p):
            return rid
    best, bd = None, slack
    for rid, q in polys.items():
        d = q.distance(p)
        if d < bd:
            best, bd = rid, d
    return best


def probe_at(seg, t_px, polys, frac=None, pt=None, reach=14.0):
    """벽의 한 지점에서 법선 양쪽을 찔러 (왼쪽 방, 오른쪽 방). 벽면에서 바깥으로
    조금씩 물러나며 처음 들어간 방을 고른다 — 두꺼운 벽도 작은 틈도 건넌다."""
    (x0, y0), (x1, y1) = seg[0], seg[1]
    dx, dy = x1 - x0, y1 - y0
    L = math.hypot(dx, dy)
    if L < 1e-6:
        return None, None
    nx, ny = -dy / L, dx / L
    if pt is not None:                               # 주어진 점을 벽에 투영
        t = ((pt[0] - x0) * dx + (pt[1] - y0) * dy) / (L * L)
        t = max(0.0, min(1.0, t))
    else:
        t = 0.5 if frac is None else frac
    px, py = x0 + dx * t, y0 + dy * t
    out = []
    base = t_px / 2.0 + 1.0
    for sign in (1.0, -1.0):
        got = None
        off = base
        while off <= base + reach:
            got = hit(polys, px + nx * off * sign, py + ny * off * sign, slack=1.0)
            if got:
                break
            off += 1.5
        out.append(got)
    return out[0], out[1]


def wall_pairs(seg, t_px, polys):
    """벽을 따라 나오는 방 쌍들 — 지점마다 다를 수 있다. {frozenset: 지지 지점 수}"""
    cnt = collections.Counter()
    seen = collections.Counter()
    for f in (0.12, 0.3, 0.5, 0.7, 0.88):
        a, b = probe_at(seg, t_px, polys, frac=f)
        for r in (a, b):
            if r:
                seen[r] += 1
        if a and b and a != b:
            cnt[frozenset((a, b))] += 1
    return cnt, seen


def open_runs(polys, walls, scale, open_min):
    """맞닿았지만 막히지 않은 구간이 open_min 이상인 방 쌍. {frozenset: 남은 길이}

    막힌 구간 = (두께 있는 벽) − (개방 기록). 축선이 개방 위를 지나가도 가리지 못한다.
    """
    solid, opened = [], []
    for w in walls:
        s = w.get("segment") or []
        if len(s) < 2:
            continue
        t_mm = w.get("thickness_mm")
        try:
            ln = LineString([tuple(s[0]), tuple(s[1])])
        except Exception:
            continue
        if (w.get("type") or "") == "open" or not t_mm:
            # 개방 폭은 가장 두꺼운 벽보다 넓게 — 안 그러면 벽 버퍼를 다 못 걷어낸다.
            # cap_style=2(평평)로 선 끝을 넘어 번지지 않게 한다.
            opened.append(ln.buffer(12.0, cap_style=2))
        elif ln.length > AXIS_LEN:
            # 길이 AXIS_LEN 초과 = 도면을 가로지르는 **제도 축선**이다(벽이 아니다).
            # 실측: w81 은 y=784.2 위 569.7px 'exterior' 레코드인데 주방 아랫변·알파룸
            # 아랫변·거실 윗변·복도 윗변을 한꺼번에 지나며 6개 방에 걸친다. 현관↔복도
            # 틈 11px 도 이 선이 '벽'으로 덮어 현관이 집과 끊겼다.
            # 진짜 외벽이 길어도 외부와는 방-방 쌍이 없으므로 빼도 안전하다.
            continue
        else:
            t_px = (float(t_mm) / scale) if scale else 7.0
            solid.append(ln.buffer(t_px / 2.0 + 2.0))
    blocked = None
    if solid:
        blocked = unary_union(solid)
        if opened:
            blocked = blocked.difference(unary_union(opened))
    out = {}
    ids = list(polys)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            pa, pb = polys[a], polys[b]
            if pa.distance(pb) > 1.5:
                continue
            try:
                shared = pa.exterior.intersection(pb.buffer(1.5))
            except Exception:
                continue
            if shared.is_empty or shared.length < open_min:
                continue
            rest = shared
            if blocked is not None and not blocked.is_empty:
                try:
                    rest = shared.difference(blocked)
                except Exception:
                    pass
            if (not rest.is_empty) and rest.length >= open_min:
                out[frozenset((a, b))] = rest.length
    return out


def load_overrides():
    for p in (OVR, OVR_OLD):
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            continue
    return {}


def rebuild(g, thin_px=THIN_DOOR_PX, ovr=None):
    rooms = {str(k): v for k, v in (g.get("rooms") or {}).items()}
    polys = polys_of(g)
    scale = float(g.get("scale_mm_per_px") or 0) or None
    rep = {"wall_rooms": 0, "door_pairs": 0, "dropped": 0, "merged": 0,
           "unresolved": 0, "thin": 0, "door_log": [], "edge_before": len(g.get("edges") or [])}

    # ── 1) 벽마다 양쪽 방을 다시 정한다 ───────────────────────────────
    wall_rooms = {}
    for w in (g.get("walls") or []):
        seg = w.get("segment") or []
        if len(seg) < 2:
            continue
        t_mm = w.get("thickness_mm")
        t_px = (float(t_mm) / scale) if (scale and t_mm) else 7.0
        pairs, seen = wall_pairs(seg, t_px, polys)
        new = [r for r, _ in seen.most_common()]
        old = [str(x) for x in (w.get("rooms") or [])]
        wall_rooms[str(w.get("id"))] = (new, pairs, seg, t_px)
        if sorted(old) != sorted(new):
            rep["wall_rooms"] += 1
        w["rooms"] = [int(x) if x.isdigit() else x for x in new]

    # ── 2) 문을 상자로 묶어 중복을 없애고, 벽 양쪽으로 잇는다 ─────────
    groups = collections.OrderedDict()
    for d in (g.get("doors") or []):
        b = d.get("bbox_px")
        if not b:
            rep["dropped"] += 1
            rep["door_log"].append((d.get("id"), "상자 없음 → 버림", None, None))
            continue
        key = tuple(round(float(v) / 2.0) for v in b)
        groups.setdefault(key, []).append(d)

    new_doors = []
    for key, grp in groups.items():
        if len(grp) > 1:
            rep["merged"] += len(grp) - 1
        pair, how = None, ""
        b0 = grp[0]["bbox_px"]
        ctr = (b0[0] + b0[2] / 2.0, b0[1] + b0[3] / 2.0)

        # 순서: 문 폴리곤(스윙 호) → 문 상자 → 벽.
        # 2026-11-13 사용자 지상진실로 검증: 벽을 먼저 믿으면 틀린다.
        #   d6 은 상자로 풀면 발코니(89)+현관(78)(=사용자 확인 "발코니와 현관에 문이 있다")
        #   인데 벽 w65(욕실74·현관78)로 덮으면 "현관↔욕실"이 되고, 사용자는 그 문이
        #   없다고 명시했다. 벽 레코드는 문이 실제로 난 자리를 모른다.
        hits1 = []

        def _pick(geom):
            hs = sorted(((geom.intersection(q).area, rid) for rid, q in polys.items()
                         if geom.intersection(q).area > 1.0), reverse=True)
            if len(hs) == 1 and not hits1:
                hits1.append(hs[0][1])
            return [hs[0][1], hs[1][1]] if len(hs) >= 2 else None

        for d in grp:                                  # ① 스윙 호가 열리는 두 방
            pts = d.get("polygon") or []
            if len(pts) < 3:
                continue
            try:
                q = Polygon([(x, y) for x, y in pts])
                if not q.is_valid:
                    q = q.buffer(0)
                got = _pick(q.buffer(6.0))
            except Exception:
                got = None
            if got:
                pair, how = got, "스윙"
                break
        if pair is None:                               # ② 문 상자를 키워가며
            for pad in (PAD, PAD * 2, PAD * 3):
                got = _pick(box(b0[0] - pad, b0[1] - pad,
                                b0[0] + b0[2] + pad, b0[1] + b0[3] + pad))
                if got:
                    pair, how = got, "상자+%d" % int(pad)
                    break
        if pair is None:                               # ③ 마지막으로 벽 법선
            cand = []
            for d in grp:
                ow = d.get("on_wall")
                for wid in (ow if isinstance(ow, list) else [ow]):
                    if not wid:
                        continue
                    ent = wall_rooms.get(str(wid))
                    if not ent:
                        continue
                    _, _, seg, t_px = ent
                    try:
                        dist0 = LineString([tuple(seg[0]), tuple(seg[1])]).distance(Point(*ctr))
                    except Exception:
                        dist0 = 1e9
                    cand.append((dist0, str(wid), seg, t_px))
            for _, wid, seg, t_px in sorted(cand, key=lambda c: c[0]):
                a, bb = probe_at(seg, t_px, polys, pt=ctr, reach=20.0)
                if a and bb and a != bb:
                    pair, how = [a, bb], "벽 " + wid
                    break
        keep = dict(grp[0])
        keep["on_wall"] = [str(d.get("on_wall")) for d in grp if d.get("on_wall")] or None
        if keep["on_wall"] and len(keep["on_wall"]) == 1:
            keep["on_wall"] = keep["on_wall"][0]
        old = sorted(str(x) for x in (grp[0].get("connects") or []))
        if pair:
            keep["connects"] = [int(x) if x.isdigit() else x for x in pair]
            if old != sorted(pair):
                rep["door_pairs"] += 1
            rep["door_log"].append((keep.get("id"), how, old, list(pair)))
        elif pair is None and len(hits1) == 1:
            # 한쪽만 방 = **외부로 나가는 문**(현관 출입문). 버리면 안 된다.
            # 실측(도면 1,500장): is_entrance 문 1,987개 중 579개(29%)가 이 경우다.
            # 도면화에는 현관 출입문이 반드시 있어야 하고 피난 규정에도 필요하다.
            keep["connects"] = [int(hits1[0]) if hits1[0].isdigit() else hits1[0]]
            keep["to_outside"] = True
            keep["is_entrance"] = True
            rep["outside_door"] = rep.get("outside_door", 0) + 1
        else:
            keep["connects"] = []
            keep["suspect"] = True
            keep["suspect_reason"] = "어느 두 방을 잇는지 기하로 풀리지 않는다"
            rep["unresolved"] += 1
            rep["door_log"].append((keep.get("id"), "못 품", old, None))
        wpx = keep.get("width_px")
        if wpx is not None and float(wpx) < thin_px:
            keep["suspect"] = True
            keep["suspect_reason"] = "폭 %.0fpx (약 %dmm) — 문으로 보기 좁다" % (
                float(wpx), int(float(wpx) * (scale or 0)))
            rep["thin"] += 1
        else:
            keep.pop("suspect", None)
            keep.pop("suspect_reason", None)
        keep["link_source"] = "geometry_rebuild"
        new_doors.append(keep)
    g["doors"] = new_doors

    # ── 3) 엣지를 다시 만든다 (문 엣지가 오픈 엣지를 이긴다) ──────────
    edges = {}
    for d in new_doors:
        c = [str(x) for x in (d.get("connects") or [])]
        if len(c) != 2 or c[0] == c[1]:
            continue
        edges[frozenset(c)] = {"from": c[0], "to": c[1], "via": "door",
                               "boundary": "door", "door_id": d.get("id"),
                               "door_swing_deg": d.get("orientation_deg"),
                               "door_pos": d.get("position")}
    # (a) 창호 — 발코니는 벽을 끼고 창(실제로는 미닫이문)으로 이어진다.
    # 실측: win12 는 침실70(2183px2)+발코니86(1906px2) 양쪽에 걸쳐 있는데
    # 벽 레코드에 open 이 없어 엣지가 생기지 않았다. 창을 근거에 넣는다.
    # 외벽 창(한 방만 닿음)은 통로가 아니므로 제외하고, 두 번째 방의 겹침이
    # 첫 번째의 WIN_RATIO 미만이면 스쳐 지난 것으로 보고 버린다.
    for w in (g.get("windows") or []):
        b = w.get("bbox_px")
        if not b or not b[2] or not b[3]:
            continue
        z = box(b[0] - WIN_PAD, b[1] - WIN_PAD,
                b[0] + b[2] + WIN_PAD, b[1] + b[3] + WIN_PAD)
        hits = sorted(((z.intersection(q).area, rid) for rid, q in polys.items()
                       if z.intersection(q).area > 1.0), reverse=True)
        if len(hits) < 2 or hits[1][0] < hits[0][0] * WIN_RATIO:
            continue
        k = frozenset((hits[0][1], hits[1][1]))
        if k in edges:
            continue
        x, y = sorted(k)
        # 창 연결은 오픈공간이 아니다 — 사용자 지적(2026-11-13):
        # "알파룸과 발코니는 창문으로 연결되어 있다. 오픈 공간(점선) 아니다."
        edges[k] = {"from": x, "to": y, "via": "window", "boundary": "window",
                    "door_id": None, "door_swing_deg": None, "door_pos": None,
                    "window_id": str(w.get("id"))}

    # 순서가 중요하다 — 창이 벽-개방 레코드를 **이긴다**.
    # 사용자 지상진실(2026-11-13): "알파룸과 발코니는 창문으로 연결되어 있다.
    # 오픈 공간(점선) 아니다." 그런데 그 경계에는 type=open 벽 레코드(w88)도 있다.
    # 창이 난 자리를 개방으로 적으면 통행 가능성을 모르는 것을 안다고 적는 셈이다.

    # (b) 개방으로 검출된 벽 레코드 — 두꺼운 경계를 끼고 떨어진 쌍(방↔발코니)을 잡는다
    for w in (g.get("walls") or []):
        if (w.get("type") or "") != "open" and float(w.get("thickness_mm") or 1) != 0:
            continue
        ent = wall_rooms.get(str(w.get("id")))
        if not ent:
            continue
        for k in ent[1]:
            if k in edges:
                continue
            x, y = sorted(k)
            edges[k] = {"from": x, "to": y, "via": "open", "boundary": "open",
                        "door_id": None, "door_swing_deg": None, "door_pos": None,
                        "open_from": "wall:" + str(w.get("id"))}
    # (c) 벽 레코드가 없는 경계 — 맞닿은 길이에서 막힌 구간을 뺀 나머지
    for k, L in open_runs(polys, g.get("walls") or [], scale, OPEN_MIN).items():
        if k in edges:
            continue
        x, y = sorted(k)
        edges[k] = {"from": x, "to": y, "via": "open", "boundary": "open",
                    "door_id": None, "door_swing_deg": None, "door_pos": None,
                    "open_from": "geometry", "open_len_px": round(L, 1)}

    # (d) 현관은 문이 없으면 개방이다 — 사용자 규칙(2026-11-13):
    # "현관복도는 일반적으로 미닫이 또는 인테리어 공사를 안 하면 오픈 공간이다."
    # 실측: 현관↔복도 맞닿음 102.9px 인데 내벽 4개가 91.5px 를 덮어 11.4px 만
    # 남아 기준에 못 들었고 현관이 집과 끊겼다. 문이 검출되면 그 문을 쓰고,
    # 없을 때만 이 규칙이 돈다. 발코니·실외기실은 집 안이 아니므로 제외한다.
    ents = [rid for rid, r in rooms.items() if r.get("role") == "현관"]
    for ent in ents:
        if ent not in polys:
            continue
        inside = False
        for k, e in edges.items():
            if ent not in k:
                continue
            other = [x for x in k if x != ent]
            orole = (rooms.get(other[0]) if other else {} or {}).get("role")
            if orole not in OUTDOOR_ROLES:
                inside = True
                break
        if inside:
            continue
        best, bl = None, 0.0
        for rid, q in polys.items():
            if rid == ent or (rooms.get(rid) or {}).get("role") in OUTDOOR_ROLES:
                continue
            if polys[ent].distance(q) > 2.0:
                continue
            try:
                L = polys[ent].exterior.intersection(q.buffer(1.5)).length
            except Exception:
                continue
            if L > bl:
                best, bl = rid, L
        if best and bl >= OPEN_MIN:
            k = frozenset((ent, best))
            x, y = sorted(k)
            edges[k] = {"from": x, "to": y, "via": "open", "boundary": "open",
                        "door_id": None, "door_swing_deg": None, "door_pos": None,
                        "open_from": "entrance_rule", "open_len_px": round(bl, 1)}
            rep["entrance_open"] = rep.get("entrance_open", 0) + 1

    # (e) 연결 증거가 **하나도 없는 방** — 추론으로 잇고 추론분을 따로 센다.
    # 조용히 메우지 않는다: open_from="fallback_rule" 로 표시해 전수로 셀 수 있게 한다.
    # 원인은 대개 작은 수납공간(펜트리·드레스룸·욕실)의 문 검출 누락이다.
    linked = set()
    for k in edges:
        linked |= set(k)
    for rid in list(polys):
        if rid in linked:
            continue
        best, bl = None, 0.0
        for oid, q in polys.items():
            if oid == rid or polys[rid].distance(q) > 2.0:
                continue
            try:
                L = polys[rid].exterior.intersection(q.buffer(1.5)).length
            except Exception:
                continue
            if L > bl:
                best, bl = oid, L
        if best and bl >= OPEN_MIN:
            k = frozenset((rid, best))
            if k in edges:
                continue
            x, y = sorted(k)
            edges[k] = {"from": x, "to": y, "via": "open", "boundary": "open",
                        "door_id": None, "door_swing_deg": None, "door_pos": None,
                        "open_from": "fallback_rule", "open_len_px": round(bl, 1)}
            linked |= k
            rep["fallback_open"] = rep.get("fallback_open", 0) + 1
        else:
            rep["no_evidence"] = rep.get("no_evidence", 0) + 1

    # (f) 역할 쌍 규칙 — 기하는 '어느 두 방'까지, '무엇으로'는 표가 정한다.
    # role_rules.py 에 사용자가 확인해 준 사실만 적혀 있다(과잉 일반화 금지).
    if role_rules is not None:
        for k, e in list(edges.items()):
            ra = (rooms.get(str(e["from"])) or {}).get("role")
            rb = (rooms.get(str(e["to"])) or {}).get("role")
            want = role_rules.via_for(ra, rb)
            if want and want != e.get("via"):
                # 문 레코드가 실제로 있으면 그 증거를 규칙보다 앞세운다
                if e.get("via") == "door" and e.get("door_id"):
                    continue
                e["via"] = want
                e["boundary"] = want
                e["via_from"] = "role_rule"
                rep["via_fixed"] = rep.get("via_fixed", 0) + 1
            if e.get("via") == "door" and role_rules.door_forbidden(ra, rb):
                e["suspect"] = True
                e["suspect_reason"] = "%s↔%s 는 문으로 이어질 수 없다" % (ra, rb)
                rep["forbidden_door"] = rep.get("forbidden_door", 0) + 1
                did = e.get("door_id")
                for d in new_doors:
                    if d.get("id") == did:
                        d["suspect"] = True
                        d["suspect_reason"] = e["suspect_reason"]

    # (g) 사용자 확정값 — 사람이 도면을 직접 보고 알려준 사실이 마지막에 이긴다.
    o = (ovr or {}).get(str(g.get("plan_id")) or "") or {}
    for pair in (o.get("forbid") or []):
        k = frozenset(str(x) for x in pair[:2])
        if k in edges:
            del edges[k]
            rep["ovr_forbid"] = rep.get("ovr_forbid", 0) + 1
    for item in (o.get("force") or []):
        a, b = str(item[0]), str(item[1])
        via = item[2] if len(item) > 2 else "door"
        k = frozenset((a, b))
        x, y = sorted(k)
        rec = {"from": x, "to": y, "via": via, "boundary": via,
               "door_id": None, "door_swing_deg": None, "door_pos": None,
               "via_from": "user_override"}
        if via == "window":
            # 창 엣지에는 **어느 창인지** 반드시 박는다. 비워 두면 그 두 방에 속한
            # 창이 전부 이 엣지에 붙어 화면에 선이 여러 개 생긴다(실측: 거실에 3개).
            best, bd = None, 1e18
            for w in (g.get("windows") or []):
                wp = w.get("position")
                if not wp or str(w.get("belongs_to")) not in (a, b):
                    continue
                d2 = 0.0
                for rid in (a, b):
                    c = polys.get(rid)
                    if c is not None:
                        d2 += c.distance(Point(wp[0], wp[1]))
                if d2 < bd:
                    best, bd = w.get("id"), d2
            rec["window_id"] = best
        edges[k] = rec
        rep["ovr_force"] = rep.get("ovr_force", 0) + 1

    # (h) 끊긴 조각을 본체에 붙인다 — 붙인 건수를 따로 센다.
    def _comps():
        adj2 = collections.defaultdict(set)
        for kk in edges:
            a2, b2 = tuple(kk)
            adj2[a2].add(b2)
            adj2[b2].add(a2)
        seen, out2 = set(), []
        for rid in polys:
            if rid in seen:
                continue
            st, c = [rid], set()
            while st:
                x = st.pop()
                if x in c:
                    continue
                c.add(x)
                st.extend(adj2[x] - c)
            seen |= c
            out2.append(c)
        return out2

    ent_ids = {rid for rid, r in rooms.items() if r.get("role") == "현관"}
    guard = 0
    while guard < 40:
        guard += 1
        cs = _comps()
        if len(cs) < 2:
            break
        main = None
        for c in cs:
            if c & ent_ids:
                main = c
                break
        if main is None:
            main = max(cs, key=len)
        others = [c for c in cs if c is not main]
        best = None                       # (길이, 조각방, 본체방)
        for c in others:
            for rid in c:
                for oid in main:
                    if rid not in polys or oid not in polys:
                        continue
                    if polys[rid].distance(polys[oid]) > 25.0:
                        continue
                    try:
                        L = polys[rid].exterior.intersection(
                            polys[oid].buffer(max(2.0, polys[rid].distance(polys[oid]) + 1.5))).length
                    except Exception:
                        continue
                    if best is None or L > best[0]:
                        best = (L, rid, oid)
        if best is None or best[0] < OPEN_MIN:
            break
        L, rid, oid = best
        k = frozenset((rid, oid))
        x, y = sorted(k)
        edges[k] = {"from": x, "to": y, "via": "open", "boundary": "open",
                    "door_id": None, "door_swing_deg": None, "door_pos": None,
                    "open_from": "component_merge", "open_len_px": round(L, 1)}
        rep["comp_merge"] = rep.get("comp_merge", 0) + 1

    # (i) 창의 belongs_to 도 다시 푼다 — doors[].connects 와 같은 함정이다.
    # 코덱은 창을 "그 방 사이클의 어느 변 위, 몇 % 지점"으로 적는다
    # (window = 외벽(1방 belongs_to)). 그래서 belongs_to 가 틀리면 엉뚱한 방 벽에 붙고,
    # 이미 지워진 방을 가리키면 붙일 변이 없어 **증발한다**.
    # 실측(창 12,708개·도면 1,200장): 일치 90.4% · 최대겹침 아님 4.4% ·
    # 없는 방 3.9%(증발) · 전혀 다른 방 1.2% · 어디에도 안 닿음 0.1%.
    # 창의 64.5%가 외벽 창(1방), 35.4%는 두 방 이상에 닿는다.
    for w in (g.get("windows") or []):
        b = w.get("bbox_px")
        if not b or not b[2] or not b[3]:
            continue
        z = box(b[0] - WIN_PAD, b[1] - WIN_PAD,
                b[0] + b[2] + WIN_PAD, b[1] + b[3] + WIN_PAD)
        hits = sorted(((z.intersection(q).area, rid) for rid, q in polys.items()
                       if z.intersection(q).area > 1.0), reverse=True)
        old = str(w.get("belongs_to"))
        if not hits:
            if old not in polys:
                w["suspect"] = True
                w["suspect_reason"] = "어느 방에도 닿지 않는다"
                rep["win_orphan"] = rep.get("win_orphan", 0) + 1
            continue
        # 두 방에 걸친 창(방↔발코니)은 **실내 쪽**을 적는다 — 발코니는 창이 나는 쪽이 아니다.
        # ⚠ 둘 다 실외 계열이면(발코니↔실외기실) 규칙이 못 가른다. 그때는 **원래 값을
        # 그대로 둔다** — 넓은 쪽을 집으면 실외기실 창이 발코니 창이 돼버린다
        # (실측 win5: 실외기실(11) → 발코니(36) 로 잘못 옮겨졌다).
        pick = hits[0][1]
        ra = (rooms.get(hits[0][1]) or {}).get("role")
        rb = (rooms.get(hits[1][1]) or {}).get("role") if len(hits) >= 2 else None
        if len(hits) >= 2 and ra in OUTDOOR_ROLES and rb not in OUTDOOR_ROLES:
            pick = hits[1][1]
        elif len(hits) >= 2 and ra in OUTDOOR_ROLES and rb in OUTDOOR_ROLES:
            if old in polys:
                pick = old          # 둘 다 실외 — 건드리지 않는다
        if pick != old:
            w["belongs_to_parsed"] = w.get("belongs_to")
            w["belongs_to"] = int(pick) if pick.isdigit() else pick
            w["belongs_to_source"] = "geometry_rebuild"
            rep["win_fixed"] = rep.get("win_fixed", 0) + 1

    # (j) 문 엣지인데 **문 레코드가 없는 것**에 레코드를 만들어 준다.
    # 왜: wallcycle_codec.canonicalize 는 g["doors"] 레코드만 돈다. open 루프는
    # via=="open" 만 본다. 그래서 확정값·규칙으로 만든 door 엣지는 레코드가 없으면
    # **door 로도 open 으로도 토큰에 안 들어가고 통째로 증발한다.**
    # 실측(APT_FP_01613b7f_17446927_u0): edges 의 door 7개 중 canon door 는 3개뿐이었다.
    # 위치는 두 방이 맞닿은 구간의 가운데로 잡고, 폭은 그 맞닿은 길이로 둔다
    # (검출된 문이 아니라 **우리가 만든 문**이므로 source 로 구분한다).
    have = set()
    for d in new_doors:
        c = [str(x) for x in (d.get("connects") or [])]
        if len(c) == 2:
            have.add(frozenset(c))
    seq = 0
    for k, e in edges.items():
        if e.get("via") != "door" or k in have:
            continue
        a2, b2 = tuple(k)
        if a2 not in polys or b2 not in polys:
            continue
        pa, pb = polys[a2], polys[b2]
        try:
            band = pa.buffer(14.0).intersection(pb.buffer(14.0))
            c = band.centroid
            shared = pa.exterior.intersection(pb.buffer(max(2.0, pa.distance(pb) + 1.5))).length
        except Exception:
            continue
        if c.is_empty:
            continue
        seq += 1
        # ⚠ 폭은 **모른다**. 맞닿은 경계 길이를 폭으로 쓰면 측정값처럼 보이는 가짜가 된다
        # (실측: 그렇게 만든 5개 중 3개가 상·하한에 그대로 걸렸다 — 1.576m·0.525m).
        # 위치만 적고 폭은 비워 둔다. 원형 부호 OCR 로 채울 자리다.
        rec = {"id": "dx%d" % seq, "connects": [int(x) if x.isdigit() else x for x in (a2, b2)],
               "via": "door", "position": [round(c.x, 1), round(c.y, 1)],
               "bbox_px": None, "width_px": None, "width_m": None,
               "source": "link_rebuild", "link_source": "geometry_rebuild",
               "width_source": "unknown",
               "shared_len_px": round(shared, 1) if shared else None,
               "from_edge": e.get("via_from") or e.get("open_from") or "rule"}
        new_doors.append(rec)
        e["door_id"] = rec["id"]
        e["door_pos"] = rec["position"]
        rep["door_made"] = rep.get("door_made", 0) + 1
    g["doors"] = new_doors
    g["n_doors"] = len(new_doors)

    # 위계 전이 · 현관 거리
    for e in edges.values():
        pa = PRIVACY.get((rooms.get(e["from"]) or {}).get("role"), "other")
        pb = PRIVACY.get((rooms.get(e["to"]) or {}).get("role"), "other")
        e["privacy_transition"] = "%s_to_%s" % (pa, pb)
    adj = collections.defaultdict(set)
    for e in edges.values():
        adj[e["from"]].add(e["to"])
        adj[e["to"]].add(e["from"])
    start = [rid for rid, r in rooms.items() if r.get("role") == "현관"]
    dist = {}
    dq = collections.deque((s, 0) for s in start)
    for s in start:
        dist[s] = 0
    while dq:
        cur, dd = dq.popleft()
        for nb in adj[cur]:
            if nb not in dist:
                dist[nb] = dd + 1
                dq.append((nb, dd + 1))
    for rid, r in rooms.items():
        # 키가 없던 방(복도복원·사람이 나눈 방)도 반드시 채운다 — 빼먹으면 None 으로 남는다
        r["dist_from_entrance"] = dist.get(rid)
    for e in edges.values():
        e["dist_from_entrance"] = dist.get(e["from"])

    out = []
    for e in edges.values():
        e["from"] = int(e["from"]) if str(e["from"]).isdigit() else e["from"]
        e["to"] = int(e["to"]) if str(e["to"]).isdigit() else e["to"]
        out.append(e)
    g["edges"] = out

    # 방마다 door_ids 가 있으면 맞춰준다
    for rid, r in rooms.items():
        if "door_ids" in r:
            r["door_ids"] = [d.get("id") for d in new_doors
                             if rid in [str(x) for x in (d.get("connects") or [])]]
    g["n_doors"] = len(new_doors)
    g["n_edges"] = len(out)
    g.setdefault("provenance", {})["links"] = "geometry_rebuild"
    reach = collections.defaultdict(set)
    for e in out:
        reach[str(e["from"])].add(str(e["to"]))
        reach[str(e["to"])].add(str(e["from"]))
    rep["isolated"] = [rid for rid in rooms if rid not in reach]
    rep.setdefault("entrance_open", 0)
    rep.setdefault("fallback_open", 0)
    rep.setdefault("no_evidence", 0)
    rep.setdefault("via_fixed", 0)
    rep.setdefault("forbidden_door", 0)
    rep.setdefault("ovr_forbid", 0)
    rep.setdefault("ovr_force", 0)
    rep.setdefault("comp_merge", 0)
    rep.setdefault("win_fixed", 0)
    rep.setdefault("win_orphan", 0)
    rep.setdefault("door_made", 0)
    rep.setdefault("outside_door", 0)
    rep["inferred"] = sum(1 for e in out
                          if e.get("open_from") in ("entrance_rule", "fallback_rule")
                          and e.get("via_from") != "user_override")
    rep["unreachable"] = [rid for rid in rooms
                          if rid not in dist and rid not in start]
    rep["edge_after"] = len(out)
    rep["doors_after"] = len(new_doors)
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", nargs="*", default=[])
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--thin-px", type=float, default=THIN_DOOR_PX)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--shard", default=None,
                    help="I/N - 대상을 N 등분해 I 번째만 돈다(병렬 실행용)")
    ap.add_argument("--progress", type=int, default=0,
                    help=">0 이면 그 수만큼 처리할 때마다 진행을 한 줄 찍는다")
    ap.add_argument("--stats-json", default=None, help="집계를 JSON 으로 쓴다")
    args = ap.parse_args()

    pool = list(args.plans)
    if args.sample:
        fs = [f[:-5] for f in os.listdir(EDITS) if f.endswith(".json")]
        random.Random(args.seed).shuffle(fs)
        pool += fs if args.sample < 0 else fs[: args.sample]

    if args.shard:                       # I/N - 같은 일을 나눠 동시에 돈다
        si, sn = (int(x) for x in args.shard.split("/"))
        pool = [q for k, q in enumerate(sorted(set(pool))) if k % sn == si]

    ovr = load_overrides()
    if ovr:
        print("사용자 확정값 %d장 불러옴" % len(ovr))
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bk = os.path.join(BK, stamp + "_links")
    T = collections.Counter()
    FAILED = []                          # (plan_id, 예외종류, 메시지)
    _t0 = _time.time()
    for _i, pid in enumerate(pool, 1):
        if args.progress and _i % args.progress == 0:
            el = _time.time() - _t0
            print("[진행] %d/%d  %.1f장/초  경과 %.1f분  실패 %d"
                  % (_i, len(pool), _i / max(el, 1e-9), el / 60.0, len(FAILED)),
                  flush=True)
        p = os.path.join(EDITS, pid + ".json")
        if not os.path.exists(p):
            FAILED.append((pid, "FileNotFound", ""))
            continue
        try:                             # 한 장이 깨져도 나머지는 돈다
            g = json.load(open(p, encoding="utf-8"))
            rooms = {str(k): v for k, v in (g.get("rooms") or {}).items()}
            rep = rebuild(g, args.thin_px, ovr)
        except Exception as exc:
            FAILED.append((pid, type(exc).__name__, str(exc)[:200]))
            T["failed"] += 1
            continue
        for k in ("wall_rooms", "door_pairs", "dropped", "merged", "unresolved", "thin"):
            T[k] += rep[k]
        T["isolated"] += len(rep["isolated"])
        T["entrance_open"] += rep.get("entrance_open", 0)
        T["fallback_open"] += rep.get("fallback_open", 0)
        T["no_evidence"] += rep.get("no_evidence", 0)
        T["via_fixed"] += rep.get("via_fixed", 0)
        T["forbidden_door"] += rep.get("forbidden_door", 0)
        T["ovr_forbid"] += rep.get("ovr_forbid", 0)
        T["ovr_force"] += rep.get("ovr_force", 0)
        T["comp_merge"] += rep.get("comp_merge", 0)
        T["win_fixed"] += rep.get("win_fixed", 0)
        T["win_orphan"] += rep.get("win_orphan", 0)
        T["door_made"] += rep.get("door_made", 0)
        T["outside_door"] += rep.get("outside_door", 0)
        T["edges"] += rep["edge_after"]
        T["inferred"] += rep["inferred"]
        T["unreachable"] += len(rep["unreachable"])
        if rep["isolated"]:
            T["plans_isolated"] += 1
        if rep["unreachable"]:
            T["plans_unreachable"] += 1
        T["plans"] += 1
        if rep["door_pairs"] or rep["merged"] or rep["dropped"]:
            T["plans_changed"] += 1
        if args.plans:
            nm = lambda r: "%s(%s)" % ((rooms.get(str(r)) or {}).get("role"), r)
            print("== %s" % pid)
            print("   벽 %d개 양쪽 방 재산출 · 문 %d→%d(중복 %d 합침, 빈 %d 버림) · "
                  "배선 %d개 변경 · 못 품 %d · 좁은 문 %d"
                  % (rep["wall_rooms"], rep["doors_after"] + rep["merged"] + rep["dropped"],
                     rep["doors_after"], rep["merged"], rep["dropped"],
                     rep["door_pairs"], rep["unresolved"], rep["thin"]))
            print("   엣지 %d → %d" % (rep["edge_before"], rep["edge_after"]))
            if rep["isolated"]:
                print("   ⚠ 고립된 방: " + " · ".join(nm(r) for r in rep["isolated"]))
            if rep["unreachable"]:
                print("   ⚠ 현관에서 못 닿는 방: "
                      + " · ".join(nm(r) for r in rep["unreachable"]))
            for did, how, old, new in rep["door_log"]:
                if new is None:
                    print("   %-5s %-14s %s" % (did, how,
                          " + ".join(nm(x) for x in (old or [])) or "-"))
                else:
                    mark = "   " if sorted(old) == sorted(new) else "  ▶"
                    print("%s %-5s %-14s %-26s → %s" % (mark, did, how,
                          " + ".join(nm(x) for x in old) or "-",
                          " + ".join(nm(x) for x in new)))
        if args.apply:
            tmp = None
            try:
                os.makedirs(bk, exist_ok=True)
                shutil.copy(p, os.path.join(bk, pid + ".json"))
                tmp = p + ".%d.tmp" % os.getpid()
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(g, f, ensure_ascii=False)
                os.replace(tmp, p)
            except Exception as exc:     # 쓰다 실패하면 원본은 그대로 둔다
                FAILED.append((pid, "write:" + type(exc).__name__, str(exc)[:200]))
                T["failed"] += 1
                try:
                    if tmp:
                        os.remove(tmp)
                except Exception:
                    pass

    print("\n도면 %d · 바뀐 도면 %d" % (T["plans"], T["plans_changed"]))
    print("   벽 양쪽 방 재산출 %d · 문 배선 변경 %d · 중복 문 합침 %d · "
          "빈 문 버림 %d · 못 푼 문 %d · 좁은 문 표시 %d"
          % (T["wall_rooms"], T["door_pairs"], T["merged"],
             T["dropped"], T["unresolved"], T["thin"]))
    print("   끊긴 조각 붙임 %d건 · 창 belongs_to 재산출 %d건 · 떠 있는 창 %d건"
          % (T["comp_merge"], T["win_fixed"], T["win_orphan"]))
    print("   레코드 없던 문 엣지에 문 레코드 생성 %d건(폭은 미상) · "
          "외부로 나가는 문 보존 %d건" % (T["door_made"], T["outside_door"]))
    print("   현관 개방 규칙 %d건 · 증거없음 보완 %d건 · 끝내 못 이은 방 %d개"
          % (T["entrance_open"], T["fallback_open"], T["no_evidence"]))
    if T["ovr_forbid"] or T["ovr_force"]:
        print("   사용자 확정값 적용 — 지움 %d · 넣음 %d"
              % (T["ovr_forbid"], T["ovr_force"]))
    print("   역할쌍 규칙으로 종류 교정 %d건 · 금지 쌍의 문 표시 %d건"
          % (T["via_fixed"], T["forbidden_door"]))
    if T["edges"]:
        print("   엣지 %d개 중 추론 %d개 (%.1f%%) — 나머지는 문·창·벽 증거"
              % (T["edges"], T["inferred"], 100.0 * T["inferred"] / T["edges"]))
    print("   고립된 방 %d개(도면 %d) · 현관에서 못 닿는 방 %d개(도면 %d)"
          % (T["isolated"], T["plans_isolated"], T["unreachable"], T["plans_unreachable"]))
    if FAILED:
        kinds = collections.Counter(k for _, k, _ in FAILED)
        print("   ! 실패 %d장 - %s"
              % (len(FAILED), " · ".join("%s %d" % kv for kv in kinds.most_common())))
        for pid, k, m in FAILED[:20]:
            print("      %s  %s  %s" % (pid, k, m))
    else:
        print("   실패 0장")
    if args.stats_json:
        with open(args.stats_json, "w", encoding="utf-8") as f:
            json.dump({"counts": dict(T), "n_pool": len(pool),
                       "failed": FAILED, "backup": bk if args.apply else None},
                      f, ensure_ascii=False)
    print("백업 → " + bk if args.apply else "[드라이런] --apply 로 적용")


if __name__ == "__main__":
    main()
