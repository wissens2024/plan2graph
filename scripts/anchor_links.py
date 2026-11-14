#!/usr/bin/env python3
"""anchor_links — 기하연결 **선을 어디에 그릴지**를 미리 계산해 넣는다.

사용자(2026-11-14): "거실과 복도를 오픈 공간으로 연결하는 꼭지점이 굳이 침실1 벽에
있을 필요는 없잖아. 그리고 각 침실1,2 의 창문과 외부창문이 X 자고 할 이유도 없잖아.
직선으로 하지."

둘 다 데이터가 아니라 **그리는 방식** 문제였다.

 ① 개방점이 엉뚱한 자리에 선다
    rebuild_links 는 두 방이 맞닿은 **구간 전체의 중점**을 pos 로 쓴다. 그런데 방
    폴리곤에는 1px 짜리 꼬리가 흔하다(파서가 벽을 방으로 조금 물고 들어간다).
    실측 a02e435f u0: 복도(25) 폴리곤이 x 2531~2532 로 거실-침실1 벽을 타고
    y 2323 까지 내려간다. 공유 구간이 y 1628~2323 이 되고 중점 y 1975 는 그
    1px 꼬리 위 — 화면에서는 침실1 벽에 점이 박힌다.
    -> **양쪽 방이 둘 다 두꺼운 자리**로 옮긴다. 공유선을 훑으며 각 점에서
       양쪽으로 수직 탐침을 넣어 min(A깊이, B깊이) 가 최대인 곳을 고른다.

 ② 선이 X 로 교차한다
    에디터는 개구부에서 **방 중심까지 직선**을 긋는다. 발코니3 처럼 침실 둘을
    가로질러 긴 방은 중심이 두 침실 사이에 있어서, 침실1 창과 침실2 창이 같은
    점을 향하며 교차한다.
    -> 벽은 축에 붙어 있다. 개구부가 놓인 **벽의 방향을 띠에서 읽고**, 선을 그
       벽에 **수직으로만** 긋는다. 방 안으로 들어갈 수 있는 깊이까지만 간다.
       수직선은 서로 교차할 수 없다.

읽기 전용(기본). --apply 를 줘야 쓴다.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import shutil

from shapely.geometry import Polygon, Point, box

ROOT = "/home/ju/plan2graph"
EDITS = os.path.join(ROOT, "data", "staging", "corrected", "edits")
BK = os.path.join(ROOT, "data", "staging", "corrected", "_manual_backup")

MIN_LEN = 24.0      # 이보다 짧으면 안 그리느니만 못하다
FRAC = 0.62         # 들어갈 수 있는 깊이의 몇 할까지


def poly(r):
    p = r.get("polygon") or []
    if len(p) < 3:
        return None
    g = Polygon(p).buffer(0)
    return g if (not g.is_empty and g.geom_type == "Polygon") else (
        max(g.geoms, key=lambda q: q.area) if g.geom_type == "MultiPolygon" else None)


def depth(P, p, d, cap=700.0, skip=70.0):
    """p 에서 방향 d 로 가며 (방에 들어가는 거리, 나오는 거리) 를 돌려준다.

    개구부 점은 **벽 위**에 있다. 벽 두께만큼은 어느 폴리곤에도 안 들어간다
    (파서는 방 사이에 틈을 남긴다). 그래서 먼저 들어가는 지점을 찾고,
    거기서부터 나가는 지점을 이분 탐색한다.
    """
    t0 = None
    s = 1.0
    while s <= skip:
        if P.contains(Point(p[0] + d[0] * s, p[1] + d[1] * s)):
            t0 = s
            break
        s += 2.0
    if t0 is None:
        return 0.0, 0.0
    lo, hi = t0, cap
    while hi - lo > 2.0:
        mid = (lo + hi) / 2.0
        if P.contains(Point(p[0] + d[0] * mid, p[1] + d[1] * mid)):
            lo = mid
        else:
            hi = mid
    return t0, lo


def band_axis(A, B, p=None, near=110.0):
    """두 방 사이 띠의 긴 방향. 'h' = 벽이 가로로 뻗음 -> 선은 세로로.

    ⚠ 방 폴리곤에는 1px 짜리 꼬리가 흔하다. 띠 **전체**로 축을 재면 그 꼬리가
    세로로 길어서 축이 뒤집힌다(실측 a02e435f: 침실1-복도 가 'v' 로 나왔는데
    문은 가로벽 y1815 에 있다). 그래서 개구부 둘레만 잘라서 잰다.
    """
    # ⚠ 반경을 **전체 최소거리**로 잡으면 안 된다. 두 방이 어디 한 군데서라도
    # 맞닿아 있으면 gap=0 이 되어 띠가 그 접점에만 생기고, 정작 문이 있는 자리는
    # 띠 밖으로 빠진다. 실측 a02e435f: 안방-거실은 x1799 에서 맞닿아 gap 0 ->
    # 띠가 y1819~2329 에만 생기고 문(1709,1812)은 안 들어왔다. 침실1-복도도 같다.
    # -> **개구부 자리의 틈**으로 반경을 잡는다.
    if p is not None:
        q = Point(p)
        gap = max(A.distance(q), B.distance(q)) * 2.0
    else:
        gap = A.distance(B)
    r = max(6.0, gap / 2.0 + 6.0)
    bd = A.buffer(r).intersection(B.buffer(r))
    if bd.is_empty:
        return None, None
    # 110px 로도 1px 꼬리가 섞여 축이 뒤집히는 자리가 있었다(실측 a02e435f:
    # 침실1-복도 · 안방-거실). **개구부 바로 둘레**부터 보고, 너무 작으면 넓힌다.
    use = bd
    if p is not None:
        for rad in (45.0, near):
            clip = box(p[0] - rad, p[1] - rad, p[0] + rad, p[1] + rad).intersection(bd)
            if not clip.is_empty and clip.area > 20.0:
                use = clip
                break
    x0, y0, x1, y1 = use.bounds
    return ("h" if (x1 - x0) >= (y1 - y0) else "v"), bd


def anchors(A, B, p, axis):
    """벽에 **수직**으로, 양쪽 방 안으로 들어갈 수 있는 만큼만.

    수직선끼리는 교차할 수 없다 — 사용자가 지적한 X 자가 구조적으로 사라진다.
    """
    dirs = [(0.0, -1.0), (0.0, 1.0)] if axis == "h" else [(-1.0, 0.0), (1.0, 0.0)]
    out = []
    for Q in (A, B):
        best = (0.0, None, 0.0)
        for d in dirs:
            t0, t1 = depth(Q, p, d)
            if t1 - t0 > best[0]:
                best = (t1 - t0, d, t0)
        span, d, t0 = best
        if d is None or span < MIN_LEN:
            out.append(None)
            continue
        L = t0 + max(MIN_LEN, span * FRAC)
        out.append([round(p[0] + d[0] * L, 1), round(p[1] + d[1] * L, 1)])
    return out


def open_point(A, B, bd):
    """양쪽이 둘 다 두꺼운 자리 — min(A깊이, B깊이) 가 최대인 점."""
    x0, y0, x1, y1 = bd.bounds
    horiz = (x1 - x0) >= (y1 - y0)
    n = 41
    best = (-1.0, None)
    for i in range(n):
        t = (i + 0.5) / n
        p = ((x0 + (x1 - x0) * t, (y0 + y1) / 2.0) if horiz
             else ((x0 + x1) / 2.0, y0 + (y1 - y0) * t))
        if not bd.contains(Point(p)):
            continue
        dirs = [(0.0, -1.0), (0.0, 1.0)] if horiz else [(-1.0, 0.0), (1.0, 0.0)]
        da = max(t1 - t0 for t0, t1 in (depth(A, p, d) for d in dirs))
        db = max(t1 - t0 for t0, t1 in (depth(B, p, d) for d in dirs))
        s = min(da, db)
        if s > best[0]:
            best = (s, p)
    return ([round(best[1][0], 1), round(best[1][1], 1)], best[0]) if best[1] else (None, 0.0)


def run(pid, apply, bk):
    f = os.path.join(EDITS, pid + ".json")
    g = json.load(open(f, encoding="utf-8"))
    R = {str(k): v for k, v in (g.get("rooms") or {}).items()}
    P = {k: poly(v) for k, v in R.items()}
    P = {k: v for k, v in P.items() if v is not None}
    doors = {str(d.get("id")): d for d in (g.get("doors") or [])}
    wins = {str(w.get("id")): w for w in (g.get("windows") or [])}
    n_moved = n_anch = 0
    for e in (g.get("edges") or []):
        a, b = str(e["from"]), str(e["to"])
        if a not in P or b not in P:
            continue
        A, B = P[a], P[b]
        axis, bd = band_axis(A, B, None)
        if axis is None:
            continue
        via = e.get("via")
        if via == "door":
            src = doors.get(str(e.get("door_id")))
            p = (src or {}).get("position") or e.get("door_pos")
        elif via == "window":
            src = wins.get(str(e.get("window_id")))
            p = (src or {}).get("position")
        else:
            src = None
            p = e.get("pos")
        if via == "open":
            np_, clear = open_point(A, B, bd)
            if np_ and (not p or abs(np_[0] - p[0]) > 2 or abs(np_[1] - p[1]) > 2):
                e["pos_old"] = p
                e["pos"] = np_
                n_moved += 1
            p = e.get("pos") or p
        if not p:
            continue
        axis2, _ = band_axis(A, B, p)
        axis = axis2 or axis
        ca, cb = anchors(A, B, p, axis)
        e["draw_axis"] = axis
        e["draw_anchors"] = {a: ca, b: cb}
        n_anch += 1
    if apply:
        os.makedirs(bk, exist_ok=True)
        shutil.copy2(f, os.path.join(bk, pid + ".json"))
        tmp = f + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(g, fh, ensure_ascii=False)
        os.replace(tmp, f)
    return n_moved, n_anch


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    plans = a.plans or ([os.path.basename(x)[:-5]
                         for x in sorted(glob.glob(os.path.join(EDITS, "*.json")))]
                        if a.all else [])
    bk = os.path.join(BK, datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_anchor")
    T = [0, 0, 0]
    for pid in plans:
        try:
            m, n = run(pid, a.apply, bk)
        except Exception as ex:
            print("   X %-34s %s" % (pid, ex))
            T[2] += 1
            continue
        T[0] += m
        T[1] += n
        if len(plans) <= 10:
            print("   %-34s 개방점 옮김 %d · 앵커 %d" % (pid, m, n))
    print("\n도면 %d장 · 개방점 옮김 %d · 앵커 단 엣지 %d · 실패 %d%s"
          % (len(plans), T[0], T[1], T[2], "" if a.apply else "   [드라이런]"))


if __name__ == "__main__":
    main()
