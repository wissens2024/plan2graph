#!/usr/bin/env python3
"""simplify_rooms — 방 폴리곤의 '떨림'을 편다(면적이 변하면 손대지 않는다).

왜: AI-Hub 주석 폴리곤이 **마스크 외곽선을 따라 딴 것**이라 1.2px 등간격 계단이
붙어 있다. 실측(2,000장·방 33,288개): 꼭짓점 458,673개 중 **36.6%가 잡음**,
방당 평균 13.8점(직사각형이면 4점이면 된다). 큰 방일수록 심하다 — 안방 20.8 ·
주방 20.6 · 거실 19.2점. 발코니 중엔 291점짜리도 있다.

이게 문제인 곳: ①개방 경계에서 두 방이 제각각 떨어 단차가 보인다 ②면적이 흔들린다
③wall-cycle 코덱은 코너 공유를 전제하는데 공유가 깨지고 토큰이 길어진다.

안전장치: 단순화 뒤 면적이 `--max-area-pct`(기본 0.5%)를 넘게 변하면 **그 방은
건드리지 않는다.** 유효하지 않은 폴리곤이 되면 역시 건너뛴다.

읽기 전용(기본). --apply 를 줘야 쓴다.
usage:
  python scripts/simplify_rooms.py --plans A B
  python scripts/simplify_rooms.py --sample 3000 --apply
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import random
import shutil

from shapely.geometry import Polygon

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDITS = os.path.join(ROOT, "data", "staging", "corrected", "edits")
BK = os.path.join(ROOT, "data", "staging", "corrected", "_manual_backup")


def fix(r, tol, max_pct, scale):
    pts = r.get("polygon") or []
    if len(pts) < 4:
        return None
    q = Polygon([(x, y) for x, y in pts])
    if not q.is_valid:
        q = q.buffer(0)
    if q.is_empty or q.geom_type != "Polygon":
        return None
    s = q.simplify(tol)
    if s.is_empty or s.geom_type != "Polygon" or not s.is_valid:
        return None
    n1 = len(s.exterior.coords) - 1
    if n1 < 4 or n1 >= len(pts):
        return None
    if 100.0 * abs(s.area - q.area) / max(q.area, 1.0) > max_pct:
        return "면적"
    r["polygon"] = [[round(x, 1), round(y, 1)] for x, y in list(s.exterior.coords)[:-1]]
    b = s.bounds
    r["bbox_px"] = [round(b[0], 1), round(b[1], 1), round(b[2] - b[0], 1), round(b[3] - b[1], 1)]
    r["area_px"] = round(s.area, 1)
    c = s.centroid
    r["centroid"] = [round(c.x, 1), round(c.y, 1)]
    if scale:
        r["area_m2"] = round(s.area * (float(scale) / 1000.0) ** 2, 2)
    if "perimeter_px" in r:
        r["perimeter_px"] = round(s.length, 1)
    return len(pts) - n1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", nargs="*", default=[])
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--tol", type=float, default=2.0)
    ap.add_argument("--max-area-pct", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    pool = list(args.plans)
    if args.sample:
        fs = [f[:-5] for f in os.listdir(EDITS) if f.endswith(".json")]
        random.Random(args.seed).shuffle(fs)
        pool += fs[: args.sample]

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bk = os.path.join(BK, stamp + "_simplify")
    n_plan = n_room = n_pt = n_skip = 0
    for pid in pool:
        p = os.path.join(EDITS, pid + ".json")
        if not os.path.exists(p):
            continue
        g = json.load(open(p, encoding="utf-8"))
        scale = g.get("scale_mm_per_px")
        orig = json.dumps(g.get("rooms"), ensure_ascii=False)
        ch = 0
        for rid, r in (g.get("rooms") or {}).items():
            res = fix(r, args.tol, args.max_area_pct, scale)
            if res == "면적":
                n_skip += 1
            elif isinstance(res, int):
                ch += 1
                n_pt += res
        if not ch:
            continue
        n_plan += 1
        n_room += ch
        if args.apply:
            os.makedirs(bk, exist_ok=True)
            shutil.copy(p, os.path.join(bk, pid + ".json"))
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(g, f, ensure_ascii=False)
            os.replace(tmp, p)
        elif args.plans:
            before = json.loads(orig)
            for rid, r in (g.get("rooms") or {}).items():
                a, b = len(before[rid]["polygon"]), len(r["polygon"])
                if a != b:
                    print("   %-6s id=%-4s %3d점 → %2d점" % (r.get("role"), rid, a, b))

    print("\n도면 %d · 폴리곤이 펴진 도면 %d · 방 %d개 · 꼭짓점 %d개 제거"
          % (len(pool), n_plan, n_room, n_pt))
    print("   면적이 %.1f%% 넘게 변해 건너뛴 방 %d개" % (args.max_area_pct, n_skip))
    if args.apply:
        print("백업 →", bk)
    else:
        print("[드라이런] --apply 로 적용")


if __name__ == "__main__":
    main()
