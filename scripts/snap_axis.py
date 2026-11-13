#!/usr/bin/env python3
"""snap_axis — 도면 **전체 좌표를 공통 축선에 맞춘다**. 비뚤배뚤을 한 번에 없앤다.

사용자 지적(2026-11-13): *"경계는 직선이어야 하는데 우둘투둘·비뚤배뚤이다.
사선도 아니고 그냥 비틀어지는 선이다."*

평면도는 본래 **몇 개의 축선(벽 중심선) 위에 모든 것이 놓인 그림**이다. 그런데
AI-Hub 주석은 방마다 따로 외곽선을 추적해서, 같은 벽인데도 방마다 좌표가 1~10px씩
다르다. 방 하나씩 펴는 방식으로는 잔재가 남는다.

방법: 한 도면의 모든 x 좌표를 모아 간격 `tol` 이내면 한 덩어리로 묶고, 그 덩어리의
**대표값(최빈값, 없으면 중앙값)** 으로 전부 바꾼다. y 도 같게 한다.
→ 같은 벽에 있던 좌표들이 한 값으로 모이면서 경계가 직선이 되고,
   두 방이 맞닿은 곳의 어긋남(겹침·틈)도 같이 사라진다.

안전장치: 방마다 면적이 `--max-area-pct` 를 넘게 변하거나 폴리곤이 깨지면 **그 방은
원래대로 되돌린다**(도면 전체를 버리지 않는다).

읽기 전용(기본). --apply 를 줘야 쓴다.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import random
import shutil

from shapely.geometry import Polygon


def cluster(vals, tol):
    """정렬해 간격 tol 이내를 한 덩어리로. {원래값: 대표값}"""
    out = {}
    if not vals:
        return out
    cnt = collections.Counter(vals)
    uniq = sorted(cnt)
    group = [uniq[0]]
    for v in uniq[1:]:
        if v - group[-1] <= tol:
            group.append(v)
        else:
            rep = max(group, key=lambda x: (cnt[x], -abs(x - group[len(group) // 2])))
            for x in group:
                out[x] = rep
            group = [v]
    rep = max(group, key=lambda x: (cnt[x], -abs(x - group[len(group) // 2])))
    for x in group:
        out[x] = rep
    return out


def process(g, tol, max_pct):
    rooms = g.get("rooms") or {}
    scale = g.get("scale_mm_per_px")
    xs, ys = [], []
    for r in rooms.values():
        for x, y in (r.get("polygon") or []):
            xs.append(round(x, 1))
            ys.append(round(y, 1))
    mx, my = cluster(xs, tol), cluster(ys, tol)
    moved = kept = reverted = 0
    for rid, r in rooms.items():
        pts = r.get("polygon") or []
        if len(pts) < 4:
            continue
        new = []
        for x, y in pts:
            nx, ny = mx.get(round(x, 1), x), my.get(round(y, 1), y)
            new.append([nx, ny])
        # 연속 중복점 제거
        ded = []
        for p in new:
            if not ded or ded[-1] != p:
                ded.append(p)
        while len(ded) > 1 and ded[0] == ded[-1]:
            ded.pop()
        if len(ded) < 4:
            reverted += 1
            continue
        q0 = Polygon([(x, y) for x, y in pts]).buffer(0)
        q = Polygon([(x, y) for x, y in ded])
        if not q.is_valid:
            q = q.buffer(0)
        if q.is_empty or q.geom_type != "Polygon":
            reverted += 1
            continue
        if 100.0 * abs(q.area - q0.area) / max(q0.area, 1.0) > max_pct:
            reverted += 1
            continue
        n0 = len(pts)
        r["polygon"] = [[round(x, 1), round(y, 1)] for x, y in list(q.exterior.coords)[:-1]]
        b = q.bounds
        r["bbox_px"] = [round(b[0], 1), round(b[1], 1),
                        round(b[2] - b[0], 1), round(b[3] - b[1], 1)]
        r["area_px"] = round(q.area, 1)
        c = q.centroid
        r["centroid"] = [round(c.x, 1), round(c.y, 1)]
        if scale:
            r["area_m2"] = round(q.area * (float(scale) / 1000.0) ** 2, 2)
        if "perimeter_px" in r:
            r["perimeter_px"] = round(q.length, 1)
        moved += 1
        kept += n0 - len(r["polygon"])
    return moved, kept, reverted, len(set(mx.values())), len(set(my.values()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", nargs="*", default=[])
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--tol", type=float, default=6.0, help="이 간격 이내 좌표를 한 축선으로")
    ap.add_argument("--max-area-pct", type=float, default=3.0)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    EDITS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "staging", "corrected", "edits")
    BK = os.path.join(os.path.dirname(EDITS), "_manual_backup")
    pool = list(args.plans)
    if args.sample:
        fs = [f[:-5] for f in os.listdir(EDITS) if f.endswith(".json")]
        random.Random(args.seed).shuffle(fs)
        pool += fs[: args.sample]

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bk = os.path.join(BK, stamp + "_snapaxis")
    tp = tr = tpt = trv = 0
    for pid in pool:
        p = os.path.join(EDITS, pid + ".json")
        if not os.path.exists(p):
            continue
        g = json.load(open(p, encoding="utf-8"))
        mv, pts, rev, nx, ny = process(g, args.tol, args.max_area_pct)
        if args.plans:
            print("%s  방 %d 정리 · 꼭짓점 %d 감소 · 되돌림 %d · 축선 x%d y%d"
                  % (pid, mv, pts, rev, nx, ny))
        if not mv:
            continue
        tp += 1
        tr += mv
        tpt += pts
        trv += rev
        if args.apply:
            os.makedirs(bk, exist_ok=True)
            shutil.copy(p, os.path.join(bk, pid + ".json"))
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(g, f, ensure_ascii=False)
            os.replace(tmp, p)

    print("\n도면 %d · 고친 도면 %d · 방 %d · 꼭짓점 %d개 감소 · 되돌린 방 %d"
          % (len(pool), tp, tr, tpt, trv))
    print("백업 → " + bk if args.apply else "[드라이런] --apply 로 적용")


if __name__ == "__main__":
    main()
