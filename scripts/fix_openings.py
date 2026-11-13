#!/usr/bin/env python3
"""fix_openings — 문·창의 **폭을 벽 방향으로 다시 재고** 미터값을 채운다.

사용자 질문(2026-11-13): *"창문 위치와 크기, 문의 위치와 크기가 정보로 담길 수 있을까?
기하 정보가 엉터리인데 이 부분 보정을 우리가 새롭게 할 수 있을까?"*

담겨 **있다**. 그런데 크기 값이 틀렸다. 실측(도면 600장):
  · 창 6,470개 · 문 4,651개 **전부(100%) 짧은 변을 `width_px` 로** 기록했다.
  · `width_m` 은 **100% 비어 있다** — 축척을 아는데 안 채웠다.
  · 창의 81.4% 는 길고 얇은 상자인데, 그 짧은 변 중앙값이 **364mm = 벽 두께**다.
    긴 변 중앙값은 1,557mm — 이게 실제 창 폭이다.
  · 현실적인 폭 범위에 드는 비율: 창 짧은변 19.4% vs 긴변 **95.5%**,
    문 짧은변 77.5% vs 긴변 **93.3%**.
    (문의 80.3% 는 여닫이 스윙 호라서 상자가 거의 정사각 — 짧은 변도 폭에 가까웠다.
     나머지 미닫이·얇은 상자에서 짧은 변이 벽 두께로 잘못 들어갔다.)

그래서 min/max 를 고르는 게 아니라 **벽 방향으로 투영한 길이**를 폭으로 쓴다.
  · `on_wall` 이 있으면 그 벽 선분의 방향, 없으면 상자의 긴 축
  · 폭  = 그 방향으로의 길이 (폴리곤이 있으면 폴리곤을 투영)
  · 깊이 = 수직 방향 길이 (미닫이·창은 벽 두께, 스윙 문은 호의 반경이라 의미 없음)
  · `width_m` = 폭 × 축척 ÷ 1000, `orientation_deg` = 벽 방향

안전장치: 계산한 폭이 현실 범위를 벗어나면 **값을 쓰되 `suspect` 로 표시**한다
(문 0.5~1.6m · 창 0.3~4.0m). 조용히 고치지 않는다.
원래 값은 `width_px_parsed` 에 남겨 감사(audit)할 수 있게 한다.

읽기 전용(기본). --apply 를 줘야 쓴다.
usage:
  python scripts/fix_openings.py --plans APT_FP_396cb308_17447720_u3
  python scripts/fix_openings.py --sample 2000
  python scripts/fix_openings.py --sample 0 --apply        # 전체
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
import statistics

DOOR_M = (0.5, 1.6)
WIN_M = (0.3, 4.0)


def extent(pts, ux, uy):
    """점들을 (ux,uy) 방향으로 투영한 길이."""
    vs = [x * ux + y * uy for x, y in pts]
    return max(vs) - min(vs)


def corners(b):
    return [(b[0], b[1]), (b[0] + b[2], b[1]),
            (b[0] + b[2], b[1] + b[3]), (b[0], b[1] + b[3])]


def wall_dir(walls, wid):
    for w in walls:
        if str(w.get("id")) != str(wid):
            continue
        s = w.get("segment") or []
        if len(s) < 2:
            return None
        dx, dy = s[1][0] - s[0][0], s[1][1] - s[0][1]
        L = math.hypot(dx, dy)
        if L < 1e-6:
            return None
        return dx / L, dy / L
    return None


def fix_one(o, walls, scale, lo_m, hi_m):
    b = o.get("bbox_px")
    if not b or not b[2] or not b[3]:
        return None
    pts = [tuple(p) for p in (o.get("polygon") or [])] or corners(b)
    d = None
    ws = o.get("on_wall")
    if isinstance(ws, list):
        ws = ws[0] if ws else None
    if ws:
        d = wall_dir(walls, ws)
    if d is None:                                  # 벽을 모르면 상자의 긴 축
        d = (1.0, 0.0) if b[2] >= b[3] else (0.0, 1.0)
    ux, uy = d
    width = extent(pts, ux, uy)
    depth = extent(pts, -uy, ux)
    if width < depth:                              # 벽 방향이 뒤집혔으면 바로잡는다
        width, depth = depth, width
        ux, uy = -uy, ux
    old = o.get("width_px")
    if "width_px_parsed" not in o:
        o["width_px_parsed"] = old
    o["width_px"] = round(width, 1)
    o["depth_px"] = round(depth, 1)
    o["width_m"] = round(width * scale / 1000.0, 3) if scale else None
    o["depth_m"] = round(depth * scale / 1000.0, 3) if scale else None
    o["orientation_deg"] = round(math.degrees(math.atan2(uy, ux)) % 180.0, 1)
    o["size_source"] = "geometry_rebuild"
    wm = o["width_m"]
    if wm is not None and not (lo_m <= wm <= hi_m):
        o["suspect_size"] = True
        o["suspect_size_reason"] = "폭 %.2fm — 현실 범위(%.1f~%.1fm) 밖" % (wm, lo_m, hi_m)
        return ("범위밖", old, o["width_px"], wm)
    o.pop("suspect_size", None)
    o.pop("suspect_size_reason", None)
    return ("고침" if old is None or abs((old or 0) - width) > 0.6 else "같음",
            old, o["width_px"], wm)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", nargs="*", default=[])
    ap.add_argument("--sample", type=int, default=0,
                    help="0 이면 --plans 만. 전체는 --sample -1")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    EDITS = os.path.join(ROOT, "data", "staging", "corrected", "edits")
    BK = os.path.join(ROOT, "data", "staging", "corrected", "_manual_backup")

    pool = list(args.plans)
    if args.sample:
        fs = [f[:-5] for f in os.listdir(EDITS) if f.endswith(".json")]
        random.Random(args.seed).shuffle(fs)
        pool += fs if args.sample < 0 else fs[: args.sample]

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bk = os.path.join(BK, stamp + "_openings")
    T = collections.Counter()
    before = {"문": [], "창": []}
    after = {"문": [], "창": []}
    for pid in pool:
        p = os.path.join(EDITS, pid + ".json")
        if not os.path.exists(p):
            continue
        g = json.load(open(p, encoding="utf-8"))
        sc = g.get("scale_mm_per_px")
        sc = float(sc) if sc else None
        walls = g.get("walls") or []
        ch = 0
        for kind, key, rng in (("문", "doors", DOOR_M), ("창", "windows", WIN_M)):
            for o in (g.get(key) or []):
                if sc:
                    b = o.get("bbox_px")
                    if b and o.get("width_px") is not None:
                        before[kind].append(float(o["width_px"]) * sc / 1000.0)
                r = fix_one(o, walls, sc, *rng)
                if not r:
                    T[kind + " 상자없음"] += 1
                    continue
                tag, old, new, wm = r
                T[kind + " " + tag] += 1
                if wm is not None:
                    after[kind].append(wm)
                if tag != "같음":
                    ch += 1
        if not ch:
            continue
        T["도면"] += 1
        if args.apply:
            os.makedirs(bk, exist_ok=True)
            shutil.copy(p, os.path.join(bk, pid + ".json"))
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(g, f, ensure_ascii=False)
            os.replace(tmp, p)
        elif args.plans:
            print("== %s" % pid)
            for kind, key in (("문", "doors"), ("창", "windows")):
                for o in (g.get(key) or [])[:40]:
                    if o.get("size_source"):
                        print("   %s %-6s 폭 %5.1fpx → %5.1fpx (%5.2fm) · 깊이 %4.1fpx%s"
                              % (kind, o.get("id"), float(o.get("width_px_parsed") or 0),
                                 o["width_px"], o.get("width_m") or 0, o.get("depth_px") or 0,
                                 "  ⚠" + o.get("suspect_size_reason", "")
                                 if o.get("suspect_size") else ""))

    print("\n대상 도면 %d · 값이 바뀐 도면 %d" % (len(pool), T["도면"]))
    for k in sorted(T):
        if k != "도면":
            print("   %-12s %d" % (k, T[k]))
    for kind in ("문", "창"):
        if not after[kind]:
            continue
        lo, hi = (DOOR_M if kind == "문" else WIN_M)
        okb = 100.0 * sum(1 for v in before[kind] if lo <= v <= hi) / max(len(before[kind]), 1)
        oka = 100.0 * sum(1 for v in after[kind] if lo <= v <= hi) / max(len(after[kind]), 1)
        print("   %s 폭 중앙값 %.2fm → %.2fm · 현실범위 %.1f%% → %.1f%%"
              % (kind, statistics.median(before[kind]) if before[kind] else 0,
                 statistics.median(after[kind]), okb, oka))
    print("백업 → " + bk if args.apply else "[드라이런] --apply 로 적용")


if __name__ == "__main__":
    main()
