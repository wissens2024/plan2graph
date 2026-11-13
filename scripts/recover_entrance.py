#!/usr/bin/env python3
"""recover_entrance — **현관의 외부 출입문(현관문)을 잉크 틈에서 전수 복원한다.**

사용자(2026-11-13): *"외부 현관문은 일괄적용에서 빠졌나? 내가 보는 모든 도면에서
기본적으로 빠졌네."*  실측으로 확인했다 — edits 30,543장 중 to_outside=True 문이
있는 도면은 4,879장, 그나마 **현관에 붙은 것은 2,427장**뿐이다(단위세대 29,304장
기준 92% 결손). 현관문이 없으면 "현관에서 몇 번째 방인가"(dist_from_entrance)와
동선 그래프 전체가 틀린다.

근거는 **현관 방의 외벽에 난 잉크 틈**이다. 실측(APT_FP_396cb308_17447720_u3):
현관 왼쪽 벽 x≈2843 이 y 699~757 구간에서 끊기고 바깥으로 스윙 호가 그려져 있다.

방법 — 새로 짜지 않고 있는 도구를 쓴다.
  1) 역할이 현관인 방의 폴리곤 변 중 **외벽인 변**만 남긴다
     = 바깥 법선으로 밀었을 때 다른 방 폴리곤 안에 들어가지 않는 변.
     이어진 같은 방향 변들은 하나로 합친다(인접 방 꼭짓점 때문에 쪼개져 있다).
  2) 그 변을 법선으로 조금씩 밀며 **잉크가 가장 짙은 오프셋**을 찾아 벽선으로 삼는다
     (폴리곤 변은 벽 잉크에서 몇 px 비켜 있다).
  3) 벽선 위를 미끄러뜨리며 tag_openings.gap_score 로 **틈**을 재고 최댓값을 문으로 삼는다.
     span = 표준 문폭 900mm / scale_mm_per_px.
  4) 최댓값이 임계 미만이면 **넣지 않는다** — 없는 문을 지어내는 것이
     빠뜨리는 것보다 훨씬 나쁘다.

보정 — 사람이 짚은 **정답 4장** (--ignore-existing 로 재검출, 2026-11-13 실측)
  APT_FP_396cb308_17447720_u3 방78  정답(2845, 728) → 검출(2847.3, 725.4)   3.5px
  APT_FP_1314cee9_17438443_u0 방10  정답(3426,1457) → 검출(3421.8,1458.6)   4.5px
  APT_FP_1314cee9_17438443_u1 방7   정답(1458,1438) → 검출(1458.1,1448.3)  10.3px
  APT_FP_0a142c11_17446941_u1 방31  정답( 888,1485) → 검출( 884.0,1484.8)   4.0px
  4장 모두 60px 안. 이 4장으로 임계를 정하면 안 된다 — 표본이 너무 작다.

검증 — 이미 to_outside 문이 있는 **2,427장**에 돌려 기존 문과 맞춰 봤다(--validate).
  임계 0.30 에서 검출율 69.1% · 기존 문 중심 60px 일치 **61.4%**(검출분) ·
  기존 문 도형(스윙 호 폴리곤) 안에 든 비율 50.1%. 어떤 임계에서도 90% 를 넘지 못한다.
  ⚠ 다만 **이 검증셋의 '정답'은 믿을 것이 못 된다**:
    · to_outside 문 5,570개 중 현관에 붙은 것은 2,546개뿐 —
      나머지는 욕실·복도·안방에 붙어 있다. to_outside 가 '바깥으로 난 문'이 아니라
      '두 번째 방을 못 찾았다'는 뜻으로 쓰인 것이다.
    · 그 위치는 스윙 호를 감싼 bbox 의 중심이라(snap_doors 는 연결 2개짜리만 옮긴다)
      68.1% 가 현관 폴리곤 경계에서 30px 넘게 떨어져 있다(중앙 41px).
    · 눈으로 확인: 4c1a843b_u1 은 기존 문이 현관 한가운데(138px 어긋남)인데
      우리 검출은 10X22 FSD 태그가 찍힌 진짜 현관문 자리였다.
  그래서 **합격선 미달 = 적용 보류**이되, 숫자 그대로가 검출기 정확도는 아니다.

드라이런(임계 0.30, 무작위 4,998장, --apply 없이) — 넣었을 도면 3,329장(66.6%)·문 3,404개.
  안 넣은 사유: 점수 미달 1,027(20.5%) · 외벽 변 없음 428(8.6%) · 이미 현관문 있음 380(7.6%)
  · 축척 없음 102(2.0%) · 변이 문폭보다 짧음 54(1.1%) · 현관 없음 2. 실패 0건.

읽기 전용(기본). --apply 를 줘야 쓴다.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import importlib.util
import json
import math
import os
import random
import shutil
import sys
import time as _time

import numpy as np
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCR = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCR)

import cv2  # noqa: E402


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCR, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


TO = _load("tag_openings")          # gap_score · project
ES = _load("edit_server")           # _build_png_index · _png_bytes · _sig_of

EDITS = os.path.join(ROOT, "data", "staging", "corrected", "edits")
BK = os.path.join(ROOT, "data", "staging", "corrected", "_manual_backup")

ENT_ROLE = "현관"
DOOR_MM = 900.0                     # 표준 문폭 — gap_score 의 span
WALL_MM = 180.0                     # 벽 두께(틈을 재는 띠의 폭)
MIN_FLANK = 0.20                    # 문 양옆 띠 잉크가 이보다 옅으면 벽이 아니다
THRESH = 0.30                       # 점수(틈×양옆) 합격선 — 보정으로 정한다


# ── PNG (edit_server 와 같은 방식: _png_index.json + png 캐시) ────────────────
_IMG_CACHE = {"sig": None, "img": None}


def gray_of(pid):
    sig = ES._sig_of(pid)
    if sig and _IMG_CACHE["sig"] == sig:
        return _IMG_CACHE["img"]
    data = ES._png_bytes(pid)
    if not data:
        return None
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
    _IMG_CACHE["sig"], _IMG_CACHE["img"] = sig, img
    return img


# ── 폴리곤 ───────────────────────────────────────────────────────────────────
def poly_of(r):
    p = r.get("polygon") or []
    if len(p) < 3:
        return None
    q = Polygon([(x, y) for x, y in p])
    if not q.is_valid:
        q = q.buffer(0)
    return q if (not q.is_empty and q.geom_type == "Polygon") else None


def ring(pts):
    """중복 꼭짓점을 걷어낸 열린 고리."""
    out = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) > 1e-6 or abs(p[1] - out[-1][1]) > 1e-6:
            out.append((float(p[0]), float(p[1])))
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) < 1e-6 and abs(out[0][1] - out[-1][1]) < 1e-6:
        out.pop()
    return out


def signed_area(pts):
    s = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return s / 2.0


def outward(a, b, area, poly):
    """변 a→b 의 **바깥쪽** 단위 법선. 부호면적으로 정하고 폴리곤으로 되짚는다."""
    ux, uy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(ux, uy)
    if L < 1e-9:
        return None, 0.0
    ux, uy = ux / L, uy / L
    nx, ny = (-uy, ux) if area < 0 else (uy, -ux)
    mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
    if poly.contains(Point(mx + nx * 1.5, my + ny * 1.5)):   # 안쪽을 가리켰다면 뒤집는다
        nx, ny = -nx, -ny
    return (nx, ny), L


# ── 외벽인 변 고르기 ─────────────────────────────────────────────────────────
def exterior_edges(pts, poly, others_u, depths):
    """각 변이 외벽인지 판정하고, 이어진 같은 방향 외벽 변들을 하나로 합친다."""
    n = len(pts)
    area = signed_area(pts)
    flags, norms = [], []
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        nrm, L = outward(a, b, area, poly)
        norms.append((nrm, L))
        if nrm is None or L < 1.0:
            flags.append(False)
            continue
        nx, ny = nrm
        ok = True
        for t in (0.2, 0.35, 0.5, 0.65, 0.8):
            cx, cy = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
            for dp in depths:
                if others_u is not None and others_u.intersects(Point(cx + nx * dp, cy + ny * dp)):
                    ok = False
                    break
            if not ok:
                break
        flags.append(ok)

    # 이어진 같은 방향(2도 이내) 외벽 변을 병합 — 인접 방 꼭짓점에 쪼개져 있다
    merged = []
    used = [False] * n
    for i in range(n):
        if not flags[i] or used[i]:
            continue
        j = i
        chain = [i]
        used[i] = True
        while True:
            k = (j + 1) % n
            if k == i or used[k] or not flags[k]:
                break
            n0, n1 = norms[j][0], norms[k][0]
            if n0 is None or n1 is None or (n0[0] * n1[0] + n0[1] * n1[1]) < 0.999:
                break
            used[k] = True
            chain.append(k)
            j = k
        a = pts[chain[0]]
        b = pts[(chain[-1] + 1) % n]
        nrm, L = outward(a, b, area, poly)
        if nrm is not None and L >= 1.0:
            merged.append((a, b, nrm, L))
    return merged


# ── 벽선 찾기 + 틈 재기 ──────────────────────────────────────────────────────
def band_mean(bw, seg, t_px, c0, c1):
    """gap_score 의 band() 와 **같은 모양**의 띠 평균 잉크 — [c0,c1] 구간만."""
    (ax, ay), (bx, by) = seg[0], seg[1]
    L = math.hypot(bx - ax, by - ay)
    if L < 1 or c1 - c0 < 1:
        return 0.0
    ux, uy = (bx - ax) / L, (by - ay) / L
    nx, ny = -uy, ux
    half = max(2.0, t_px / 2.0 + 1.0)
    n = max(3, int((c1 - c0) / 2.0))
    H, W = bw.shape
    vals = []
    for i in range(n):
        tt = c0 + (c1 - c0) * i / (n - 1)
        cx, cy = ax + ux * tt, ay + uy * tt
        for o in np.linspace(-half, half, 5):
            X = int(round(cx + nx * o))
            Y = int(round(cy + ny * o))
            if 0 <= Y < H and 0 <= X < W:
                vals.append(bw[Y, X])
    return float(np.mean(vals)) if vals else 0.0


def flank_ink(bw, seg, t_px, t, span, L, reach=0.6):
    """문 **바로 양옆**(문설주 자리)의 띠 잉크 — 길이로 가중 평균.

    두 가지를 한꺼번에 가린다.
      · '여기가 정말 벽인가' — 빈 종이의 허깨비 틈은 양옆도 비어 있다.
        변 전체 평균(gap_score 의 whole)은 틈을 포함해 묻혀 버린다
        (실측 1314cee9_u1: 전체 0.10 인데 양옆은 0.27).
      · '그 틈이 문 너비인가' — 거실로 열린 3m 개구부 한가운데에 문폭 창을 놓으면
        틈 점수는 1.000 이지만 **바로 양옆도 비어 있다**. 변 끝까지 평균 내면
        이게 안 걸린다. 그래서 양옆을 0.6×문폭 까지만 본다."""
    r = span * reach
    segs = [(max(0.0, t - span / 2.0 - r), t - span / 2.0),
            (t + span / 2.0, min(L, t + span / 2.0 + r))]
    num = den = 0.0
    for c0, c1 in segs:
        if c1 - c0 < 3.0:
            continue
        num += band_mean(bw, seg, t_px, c0, c1) * (c1 - c0)
        den += (c1 - c0)
    return (num / den) if den > 0 else 0.0


def best_candidate(bw, a, b, nrm, L, scale, span, t_px, step, min_flank):
    """변 둘레 법선 오프셋 × 변 위 위치를 모두 훑어 **가장 큰 틈**을 돌려준다.

    오프셋을 잉크 최대로 먼저 고정하면 안 된다 — 틈이 없는 줄(문틀 선·벽 해치)이
    잉크 최대라 오히려 문을 피해 간다(실측 1314cee9_u1: 잉크 0.98 인 줄의 틈 0.005).
    또 **바깥쪽 오프셋은 스윙 호가 틈을 메워** 점수를 깎는다(396cb308_u3:
    안쪽 0.970 ↔ 바깥쪽 0.294). 그래서 안/바깥을 가리지 않고 다 재고 최댓값을 쓴다.
    틈 점수가 동점(1.000)일 때는 **양옆 벽이 더 짙은 쪽**을 고른다."""
    lo, hi = -100.0 / scale, 320.0 / scale        # 폴리곤 변 둘레(mm 기준) 탐색
    ts = []
    t = span / 2.0
    while t <= L - span / 2.0 + 1e-6:
        ts.append(t)
        t += step
    if not ts:
        ts = [L / 2.0]
    out = None
    for o in np.arange(lo, hi + 1e-6, max(1.0, 25.0 / scale)):
        o = float(o)
        ax, ay = a[0] + nrm[0] * o, a[1] + nrm[1] * o
        bx, by = b[0] + nrm[0] * o, b[1] + nrm[1] * o
        seg = [(ax, ay), (bx, by)]
        ux, uy = (bx - ax) / L, (by - ay) / L
        for t in ts:
            q = (ax + ux * t, ay + uy * t)
            gs = TO.gap_score(bw, seg, q, t_px, span)
            fi = flank_ink(bw, seg, t_px, t, span, L)
            if fi < min_flank:
                continue                           # 양옆이 비었다 = 벽이 아니다
            # **틈 점수만 쓰면 안 된다** — 국소 잉크가 0 이면 무조건 1.000 이라
            # 빈 종이와 진짜 문이 동점이 된다(실측: 틀린 변 1.000 vs 맞는 변 0.737).
            # 양옆 벽이 짙을수록 신뢰한다 = 상대(틈) × 절대(양옆).
            sc = gs * fi
            if out is None or (sc, gs) > (out[4], out[0]):
                # 자리는 **폴리곤 변 위**(off=0)로 돌려준다 — 오프셋은 잉크를 재려고
                # 비켜 간 것일 뿐, 문은 방 경계에 놓여야 한다(실측 4장 평균 14.1→10.3px).
                ux0 = (b[0] - a[0]) / L
                uy0 = (b[1] - a[1]) / L
                out = (gs, (a[0] + ux0 * t, a[1] + uy0 * t), o, fi, sc)
    return out


def detect(g, bw, min_flank=MIN_FLANK):
    """도면 하나에서 현관방별 최선 후보. [{rid, pos, gap, flank, edge_len, off}]"""
    scale = float(g.get("scale_mm_per_px") or 0)
    if not scale:
        return [], "no_scale"
    rooms = {str(k): v for k, v in (g.get("rooms") or {}).items()}
    ents = [k for k, r in rooms.items() if (r.get("role") or "") == ENT_ROLE]
    if not ents:
        return [], "no_entrance"
    polys = {}
    for rid, r in rooms.items():
        q = poly_of(r)
        if q is not None:
            polys[rid] = q
    span = DOOR_MM / scale
    t_px = min(20.0, max(4.0, WALL_MM / scale))
    step = max(1.5, span / 10.0)
    # 바깥으로 얼마나 찔러 보는가. 1100~1500mm 까지 깊게 찔러 봤더니 오히려
    # 나빠졌다 — 진짜 현관문 벽 너머에 PD·발코니가 걸려 그 변이 통째로 '내벽'이
    # 됐다(실측 83805b13_u0: 맞는 변이 사라지고 틀린 변만 남았다). 750mm 까지만.
    depths = [d / scale for d in (250.0, 450.0, 750.0)]
    res = []
    for rid in ents:
        poly = polys.get(rid)
        if poly is None:
            res.append({"rid": rid, "why": "no_polygon"})
            continue
        others = [q for k, q in polys.items() if k != rid]
        others_u = unary_union(others) if others else None
        pts = ring(rooms[rid]["polygon"])
        edges = exterior_edges(pts, poly, others_u, depths)
        if not edges:
            res.append({"rid": rid, "why": "no_exterior_edge"})
            continue
        best = None
        for a, b, nrm, L in edges:
            if L < span * 0.9:
                continue
            c = best_candidate(bw, a, b, nrm, L, scale, span, t_px, step, min_flank)
            if c is None:
                continue
            gs, q, off, flank, sc = c
            if best is None or sc > best["score"]:
                best = {"rid": rid, "pos": [round(q[0], 1), round(q[1], 1)],
                        "score": round(sc, 4),
                        "gap": round(gs, 4), "flank": round(flank, 3),
                        "edge_len": round(L, 1), "off": round(off, 1)}
        if best is None:
            res.append({"rid": rid, "why": "short_or_no_wall",
                        "n_edges": len(edges), "span": round(span, 1)})
        else:
            res.append(best)
    return res, None


# ── 한 장 처리 ───────────────────────────────────────────────────────────────
def run(pid, thresh=THRESH, apply_=False, ignore_existing=False, verbose=False,
        bk=None, min_flank=MIN_FLANK):
    p = os.path.join(EDITS, pid + ".json")
    if not os.path.exists(p):
        return {"why": "no_edit"}
    g = json.load(open(p, encoding="utf-8"))
    rooms = {str(k): v for k, v in (g.get("rooms") or {}).items()}
    ents = [k for k, r in rooms.items() if (r.get("role") or "") == ENT_ROLE]
    if not ents:
        return {"why": "no_entrance"}
    have = set()
    for d in (g.get("doors") or []):
        if d.get("to_outside"):
            for c in (d.get("connects") or []):
                have.add(str(c))
    todo = list(ents) if ignore_existing else [r for r in ents if r not in have]
    if not todo:
        return {"why": "already_has_door"}
    img = gray_of(pid)
    if img is None:
        return {"why": "no_png"}
    bw = (img < 128).astype(np.uint8)
    res, why = detect(g, bw, min_flank)
    if why:
        return {"why": why}
    res = [r for r in res if r["rid"] in todo]
    hits = [r for r in res if "pos" in r and r["score"] >= thresh]
    miss = [r for r in res if "pos" not in r or r["score"] < thresh]
    out = {"hits": hits, "miss": miss, "all": [r for r in res if "pos" in r]}
    if verbose:
        print("%s — 현관 %s" % (pid, ", ".join(todo)))
        for r in res:
            if "pos" in r:
                print("   방%-4s (%7.1f,%7.1f)  점수 %.3f = 틈 %.3f × 양옆 %.3f  변 %6.1fpx  오프셋 %+5.1f  %s"
                      % (r["rid"], r["pos"][0], r["pos"][1], r["score"], r["gap"], r["flank"],
                         r["edge_len"], r["off"], "채택" if r["score"] >= thresh else "기각"))
            else:
                print("   방%-4s  %s" % (r["rid"], r.get("why")))
    if apply_ and hits:
        used = {str(d.get("id")) for d in (g.get("doors") or [])}
        bk = bk or os.path.join(
            BK, datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_entrance")
        os.makedirs(bk, exist_ok=True)
        shutil.copy(p, os.path.join(bk, pid + ".json"))
        for r in hits:
            did = "d_ent"
            k = 2
            while did in used:
                did, k = "d_ent%d" % k, k + 1
            used.add(did)
            rid = r["rid"]
            cid = int(rid) if str(rid).lstrip("-").isdigit() else rid
            g.setdefault("doors", []).append({
                "id": did, "connects": [cid], "via": "door", "position": r["pos"],
                "to_outside": True, "is_entrance": True,
                "width_px": None, "width_m": None, "width_source": "unknown",
                "source": "ink_gap", "position_source": "ink_gap",
                "gap_score": r["gap"], "flank_ink": r["flank"],
                "ink_score": r["score"]})
        g.setdefault("provenance", {})["entrance_door"] = "ink_gap"
        tmp = p + ".%d.tmp" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(g, f, ensure_ascii=False)
        os.replace(tmp, p)
        if verbose:
            print("   → 현관문 %d개 기록 · 백업 %s" % (len(hits), bk))
    return out


# ── 검증: 이미 to_outside 문이 있는 도면에서 위치를 맞혀 보는 ────────────────
def validate(pool, tol=60.0, progress=0, min_flank=MIN_FLANK):
    rows, fails = [], []
    t0 = _time.time()
    for i, pid in enumerate(pool, 1):
        if progress and i % progress == 0:
            el = _time.time() - t0
            print("[검증] %d/%d  %.2f장/초  경과 %.1f분  실패 %d"
                  % (i, len(pool), i / max(el, 1e-9), el / 60.0, len(fails)), flush=True)
        try:
            g = json.load(open(os.path.join(EDITS, pid + ".json"), encoding="utf-8"))
            rooms = {str(k): v for k, v in (g.get("rooms") or {}).items()}
            gt, geo = {}, {}
            for d in (g.get("doors") or []):
                if not d.get("to_outside") or not d.get("position"):
                    continue
                for c in (d.get("connects") or []):
                    if (rooms.get(str(c)) or {}).get("role") == ENT_ROLE:
                        gt.setdefault(str(c), []).append(d["position"])
                        # 기존 문의 **도형**(스윙 호를 감싼 폴리곤/bbox)도 모은다 —
                        # 중심은 호 한가운데라 벽에서 수십 px 들어가 앉아 있다.
                        gg = None
                        if d.get("polygon") and len(d["polygon"]) >= 3:
                            try:
                                gg = Polygon([(x, y) for x, y in d["polygon"]]).buffer(0)
                            except Exception:
                                gg = None
                        if gg is None and d.get("bbox_px"):
                            bx, by, bw_, bh = d["bbox_px"]
                            gg = Polygon([(bx, by), (bx + bw_, by), (bx + bw_, by + bh),
                                          (bx, by + bh)])
                        if gg is not None and not gg.is_empty:
                            geo.setdefault(str(c), []).append(gg)
            if not gt:
                continue
            r = run(pid, thresh=0.0, ignore_existing=True, min_flank=min_flank)
            if r.get("why"):
                for rid in gt:
                    rows.append([pid, rid, None, None, r["why"], None, None, None, None])
                continue
            got = {x["rid"]: x for x in r.get("all", [])}
            for rid, poss in gt.items():
                c = got.get(rid)
                if c is None:
                    rows.append([pid, rid, None, None, "no_candidate", None, None, None, None])
                    continue
                dmin = min(math.hypot(c["pos"][0] - q[0], c["pos"][1] - q[1]) for q in poss)
                gs_ = geo.get(rid) or []
                dgeo = (round(min(q.distance(Point(c["pos"][0], c["pos"][1])) for q in gs_), 1)
                        if gs_ else None)
                rows.append([pid, rid, c["score"], round(dmin, 1), "",
                             c["pos"], dgeo, c["gap"], c["flank"]])
        except Exception as exc:
            fails.append((pid, type(exc).__name__, str(exc)[:200]))
    return rows, fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", nargs="*", default=[])
    ap.add_argument("--sample", type=int, default=0, help="0 이면 --plans 만. 전체는 --sample -1")
    ap.add_argument("--seed", type=int, default=9)
    ap.add_argument("--thresh", type=float, default=THRESH)
    ap.add_argument("--tol", type=float, default=60.0)
    ap.add_argument("--min-flank", type=float, default=MIN_FLANK)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--validate", action="store_true", help="기존 to_outside 문으로 정밀도 측정")
    ap.add_argument("--ignore-existing", action="store_true",
                    help="이미 to_outside 문이 있어도 검출한다(보정·검증용. --apply 와 함께 쓰지 말 것)")
    ap.add_argument("--pool-file", default=None, help="대상 plan_id 목록 JSON/텍스트")
    ap.add_argument("--exclude-file", default=None)
    ap.add_argument("--shard", default=None)
    ap.add_argument("--progress", type=int, default=0)
    ap.add_argument("--stats-json", default=None)
    ap.add_argument("--sweep", default=None, help="검증 때 임계 스윕 (쉼표 구분)")
    a = ap.parse_args()

    ES._build_png_index()

    pool = list(a.plans)
    if a.pool_file:
        txt = open(a.pool_file, encoding="utf-8").read().strip()
        pool += (json.loads(txt) if txt.startswith("[")
                 else [l.strip() for l in txt.splitlines() if l.strip()])
    if a.sample:
        fs = [f[:-5] for f in os.listdir(EDITS) if f.endswith(".json")]
        random.Random(a.seed).shuffle(fs)
        pool += fs if a.sample < 0 else fs[: a.sample]      # -1 = 전체(한 장도 빼지 않는다)
    ex = set()
    if a.exclude_file and os.path.exists(a.exclude_file):
        ex = {l.strip() for l in open(a.exclude_file, encoding="utf-8") if l.strip()}
    n_ex = sum(1 for q in set(pool) if q in ex)
    pool = [q for q in pool if q not in ex]
    if n_ex:
        print("보호 대상 %d장 제외" % n_ex)
    # 같은 sheet(sig)끼리 모아 둔다 — PNG 디코딩을 sig 당 한 번만 한다
    pool = sorted(set(pool), key=lambda q: (ES._sig_of(q) or "", q))
    if a.shard:
        # **이어진 덩어리**로 자른다(k % sn 이 아니다) — 같은 sheet 의 세대들이
        # 한 샤드에 남아야 PNG 를 sig 당 한 번만 디코딩한다.
        si, sn = (int(x) for x in a.shard.split("/"))
        q0 = len(pool) * si // sn
        q1 = len(pool) * (si + 1) // sn
        pool = pool[q0:q1]

    if a.validate:
        rows, fails = validate(pool, a.tol, a.progress, a.min_flank)
        ths = [float(x) for x in a.sweep.split(",")] if a.sweep else [a.thresh]
        print("검증 대상 현관 %d개 (도면 %d장)" % (len(rows), len(set(r[0] for r in rows))))
        print(" %6s %9s %11s %11s %10s" % ("임계", "검출율", "일치/전체", "일치/검출", "중앙거리"))
        for th in ths:
            det = [r for r in rows if r[2] is not None and r[2] >= th]
            ok = [r for r in det if r[3] <= a.tol]
            ds = sorted(r[3] for r in det)
            med = ds[len(ds) // 2] if ds else float("nan")
            print(" %6.2f %8.1f%% %10.1f%% %10.1f%% %9.1fpx"
                  % (th, 100.0 * len(det) / max(1, len(rows)),
                     100.0 * len(ok) / max(1, len(rows)),
                     100.0 * len(ok) / max(1, len(det)), med))
        if fails:
            kinds = collections.Counter(k for _, k, _ in fails)
            print("   ! 실패 %d건 - %s"
                  % (len(fails), " · ".join("%s %d" % kv for kv in kinds.most_common())))
            for pid, k, m in fails[:20]:
                print("      %s  %s  %s" % (pid, k, m))
        else:
            print("   실패 0건")
        if a.stats_json:
            json.dump({"rows": rows, "fails": fails},
                      open(a.stats_json, "w"), ensure_ascii=False)
        return

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bk = os.path.join(BK, stamp + "_entrance")
    why = collections.Counter()
    fails = []
    n_added = n_plan = 0
    gaps = []
    t0 = _time.time()
    for i, pid in enumerate(pool, 1):
        if a.progress and i % a.progress == 0:
            el = _time.time() - t0
            print("[진행] %d/%d  %.2f장/초  경과 %.1f분  넣은 문 %d  실패 %d"
                  % (i, len(pool), i / max(el, 1e-9), el / 60.0, n_added, len(fails)), flush=True)
        try:
            r = run(pid, a.thresh, a.apply, ignore_existing=a.ignore_existing,
                    verbose=bool(a.plans), bk=bk, min_flank=a.min_flank)
        except Exception as exc:
            fails.append((pid, type(exc).__name__, str(exc)[:200]))
            continue
        if r.get("why"):
            why[r["why"]] += 1
            if a.plans:
                print("%s — 건너뜀: %s" % (pid, r["why"]))
            continue
        if r["hits"]:
            n_plan += 1
            n_added += len(r["hits"])
            gaps += [h["score"] for h in r["hits"]]
        for m in r["miss"]:
            why["below_thresh" if "pos" in m else m.get("why", "?")] += 1
    if True:
        gs = sorted(gaps)
        print("대상 %d장 · 현관문 넣은 도면 %d장 · 넣은 문 %d개  (임계 %.2f)"
              % (len(pool), n_plan, n_added, a.thresh))
        if gs:
            print("   점수 분포 — 최소 %.3f  25%% %.3f  중앙 %.3f  75%% %.3f  최대 %.3f"
                  % (gs[0], gs[len(gs) // 4], gs[len(gs) // 2], gs[3 * len(gs) // 4], gs[-1]))
        print("   안 넣은 사유 — %s"
              % (" · ".join("%s %d" % kv for kv in why.most_common()) or "없음"))
    if fails:
        kinds = collections.Counter(k for _, k, _ in fails)
        print("   ! 실패 %d건 - %s"
              % (len(fails), " · ".join("%s %d" % kv for kv in kinds.most_common())))
        for pid, k, m in fails[:20]:
            print("      %s  %s  %s" % (pid, k, m))
    else:
        print("   실패 0건")
    if a.stats_json:
        json.dump({"pool": len(pool), "plans": n_plan, "added": n_added,
                   "thresh": a.thresh, "why": dict(why), "fails": fails,
                   "backup": bk if a.apply else None},
                  open(a.stats_json, "w"), ensure_ascii=False)
    print("[드라이런] --apply 로 적용" if not a.apply else "적용 완료 · 백업 -> " + bk)


if __name__ == "__main__":
    main()
