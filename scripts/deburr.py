#!/usr/bin/env python3
"""deburr — 방 경계의 **우둘투둘만** 없앤다. 단차는 남긴다.

사용자 지적(2026-11-13): *"직선으로, 단차와 우둘투둘은 다르지 않나?"* — 맞다.
  · **단차**     벽이 실제로 꺾인 것. 일정 길이 이상 유지된다. → **남긴다**
  · **우둘투둘** 한 직선이어야 할 구간이 1.2px 씩 떠는 것. 한두 점 튀었다 돌아온다. → **지운다**

앞서 쓴 straighten_shared 는 허용범위 안이면 단차까지 펴버렸다. 잘못된 설계였다.

방법: 폴리곤을 축방향 구간으로 끊어, 각 구간의 좌표를 run-length 로 묶는다.
짧은 run(= `--min-run` px 미만)은 이웃한 긴 run 의 값으로 흡수한다. 긴 run 은 그대로 둔다.
→ 1.2px 떨림은 사라지고, 실제 꺾임(길게 유지되는 값 변화)은 보존된다.

읽기 전용(기본). --apply 를 줘야 쓴다.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import random
import shutil

from shapely.geometry import Polygon


def deburr(pts, min_run, max_jump):
    """축방향 좌표의 짧은 run 을 이웃 긴 run 으로 흡수. (새 점들, 바뀐 개수)"""
    n = len(pts)
    out = [list(p) for p in pts]
    for axis in (0, 1):                      # 0=x 를 맞춤(세로변), 1=y 를 맞춤(가로변)
        other = 1 - axis
        # 연속 구간을 같은 좌표값끼리 묶는다
        runs = []                            # [시작i, 끝i, 값, 길이]
        i = 0
        while i < n:
            j = i
            v = out[i][axis]
            while j + 1 < n and abs(out[(j + 1) % n][axis] - v) <= 0.6:
                j += 1
            lo = min(out[k][other] for k in range(i, j + 1))
            hi = max(out[k][other] for k in range(i, j + 1))
            runs.append([i, j, v, hi - lo])
            i = j + 1
        # 폴리곤은 고리다 — 첫 run 과 마지막 run 의 값이 같으면 한 구간으로 잇는다.
        # 이걸 안 하면 시작점이 구간 중간에 떨어진 경우를 놓친다(창가 변이 그랬다).
        if len(runs) > 1 and abs(runs[0][2] - runs[-1][2]) <= 0.6:
            s0, e0, v0, l0 = runs[0]
            sL, eL, vL, lL = runs[-1]
            runs = [[sL, e0, v0, max(l0, lL)]] + runs[1:-1]
        if len(runs) < 3:
            continue
        for ri, (s, e, v, ln) in enumerate(runs):
            # ★ 판정 기준은 길이가 아니라 **되돌아오는가**다.
            #   A → B → A 로 돌아오면 우둘투둘(창호 멀리언 따라 파인 것 등) → 편다
            #   A → B 로 가서 안 돌아오면 벽이 실제로 꺾인 단차 → 남긴다
            prev = runs[ri - 1]                       # 고리이므로 음수 인덱스가 곧 이전
            nxt = runs[(ri + 1) % len(runs)]
            cands = []
            if prev and nxt and abs(prev[2] - nxt[2]) <= 0.6 and abs(prev[2] - v) <= max_jump:
                cands.append((max(prev[3], nxt[3]), prev[2]))   # 되돌아옴 = 길이 무관
            if not cands and ln < min_run:
                for rj in (ri - 1, ri + 1):                     # 끝단의 짧은 꼬리
                    if 0 <= rj < len(runs):
                        _s2, _e2, v2, ln2 = runs[rj]
                        if ln2 >= min_run and abs(v2 - v) <= max_jump:
                            cands.append((ln2, v2))
            if not cands:
                continue
            tgt = max(cands)[1]
            rng = range(s, e + 1) if s <= e else list(range(s, n)) + list(range(0, e + 1))
            for k in rng:
                out[k][axis] = tgt
    moved = sum(1 for a, b in zip(pts, out) if a[0] != b[0] or a[1] != b[1])
    return out, moved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", nargs="*", default=[])
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--min-run", type=float, default=25.0,
                    help="이 길이(px) 이상 유지되면 단차로 보고 남긴다")
    ap.add_argument("--max-jump", type=float, default=8.0,
                    help="이 거리(px) 이내의 떨림만 흡수한다")
    ap.add_argument("--max-area-pct", type=float, default=1.0)
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
    bk = os.path.join(BK, stamp + "_deburr")
    n_plan = n_room = n_pt = n_skip = 0
    for pid in pool:
        p = os.path.join(EDITS, pid + ".json")
        if not os.path.exists(p):
            continue
        g = json.load(open(p, encoding="utf-8"))
        scale = g.get("scale_mm_per_px")
        ch = 0
        for rid, r in (g.get("rooms") or {}).items():
            pts = r.get("polygon") or []
            if len(pts) < 6:
                continue
            new, moved = deburr(pts, args.min_run, args.max_jump)
            if not moved:
                continue
            q0 = Polygon([(x, y) for x, y in pts]).buffer(0)
            q = Polygon([(x, y) for x, y in new])
            if not q.is_valid:
                q = q.buffer(0)
            if q.is_empty or q.geom_type != "Polygon":
                n_skip += 1
                continue
            if 100.0 * abs(q.area - q0.area) / max(q0.area, 1.0) > args.max_area_pct:
                n_skip += 1
                continue
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
            ch += 1
            n_pt += moved
            if args.plans:
                print("   %-7s id=%-4s 꼭짓점 %d개 폄 (%d점 → %d점)"
                      % (r.get("role"), rid, moved, len(pts), len(r["polygon"])))
        if ch:
            n_plan += 1
            n_room += ch
            if args.apply:
                os.makedirs(bk, exist_ok=True)
                shutil.copy(p, os.path.join(bk, pid + ".json"))
                tmp = p + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(g, f, ensure_ascii=False)
                os.replace(tmp, p)

    print("\n도면 %d · 편 도면 %d · 방 %d · 꼭짓점 %d개 이동 · 건너뜀 %d"
          % (len(pool), n_plan, n_room, n_pt, n_skip))
    print("백업 → " + bk if args.apply else "[드라이런] --apply 로 적용")


if __name__ == "__main__":
    main()
