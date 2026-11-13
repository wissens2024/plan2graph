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
usage:
  python scripts/snap_axis.py --sample 2000 --tol 4
  python scripts/snap_axis.py --sample -1 --tol 4 --apply     # 전체
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import random
import shutil
import time as _time

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
        ring = list(q.exterior.coords)[:-1]
        lean = drop_collinear(ring)            # 같은 직선 위 군더더기 제거(무손실)
        if len(lean) >= 3:
            q2 = Polygon(lean)
            if not q2.is_valid:
                q2 = q2.buffer(0)
            if (q2.geom_type == "Polygon" and not q2.is_empty
                    and abs(q2.area - q.area) <= 0.5):
                q = q2
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



def drop_collinear(pts, eps=0.05):
    """같은 직선 위에 얹힌 가운데 점을 뺀다. 도형은 그대로다."""
    if len(pts) < 4:
        return pts
    out = list(pts)
    changed = True
    while changed and len(out) > 3:
        changed = False
        n = len(out)
        for i in range(n):
            a, b, c = out[(i - 1) % n], out[i], out[(i + 1) % n]
            cross = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
            base = max(abs(c[0] - a[0]), abs(c[1] - a[1]), 1e-9)
            if cross / base <= eps:
                del out[i]
                changed = True
                break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", nargs="*", default=[])
    ap.add_argument("--sample", type=int, default=0,
                    help="0 이면 --plans 만. 전체는 --sample -1")
    ap.add_argument("--tol", type=float, default=6.0, help="이 간격 이내 좌표를 한 축선으로")
    ap.add_argument("--max-area-pct", type=float, default=3.0)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--shard", default=None,
                    help="I/N - 대상을 N 등분해 I 번째만 돈다(병렬 실행용)")
    ap.add_argument("--plans-file", default=None,
                    help="한 줄에 하나씩 적힌 plan_id 만 돈다")
    ap.add_argument("--exclude-file", default=None,
                    help="한 줄에 하나씩 적힌 plan_id 는 손대지 않는다")
    ap.add_argument("--progress", type=int, default=0)
    ap.add_argument("--stats-json", default=None)
    args = ap.parse_args()

    EDITS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "staging", "corrected", "edits")
    BK = os.path.join(os.path.dirname(EDITS), "_manual_backup")
    pool = list(args.plans)
    if args.sample:
        fs = [f[:-5] for f in os.listdir(EDITS) if f.endswith(".json")]
        random.Random(args.seed).shuffle(fs)
        # ⚠ --sample -1(전체) 은 fs[:-1] 이 되어 **한 장을 빼먹는다**. 분기한다.
        pool += fs if args.sample < 0 else fs[: args.sample]

    if args.plans_file:
        pool += [l.strip() for l in open(args.plans_file, encoding="utf-8") if l.strip()]

    ex = set()
    if args.exclude_file and os.path.exists(args.exclude_file):
        ex = {l.strip() for l in open(args.exclude_file, encoding="utf-8") if l.strip()}
    n_ex = sum(1 for p in pool if p in ex)
    pool = [p for p in pool if p not in ex]
    if n_ex:
        print("보호 대상 %d장 제외" % n_ex)

    if args.shard:                       # I/N - 같은 일을 나눠 동시에 돈다
        si, sn = (int(x) for x in args.shard.split("/"))
        pool = [q for k, q in enumerate(sorted(set(pool))) if k % sn == si]

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bk = os.path.join(BK, stamp + "_snapaxis")
    tp = tr = tpt = trv = 0
    FAILED = []
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
            mv, pts, rev, nx, ny = process(g, args.tol, args.max_area_pct)
        except Exception as exc:
            FAILED.append((pid, type(exc).__name__, str(exc)[:200]))
            continue
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
                try:
                    if tmp:
                        os.remove(tmp)
                except Exception:
                    pass

    print("\n도면 %d · 고친 도면 %d · 방 %d · 꼭짓점 %d개 감소 · 되돌린 방 %d"
          % (len(pool), tp, tr, tpt, trv))
    if FAILED:
        kinds = collections.Counter(k for _, k, _ in FAILED)
        print("   ! 실패 %d건 - %s"
              % (len(FAILED), " · ".join("%s %d" % kv for kv in kinds.most_common())))
        for pid, k, m in FAILED[:20]:
            print("      %s  %s  %s" % (pid, k, m))
    else:
        print("   실패 0건")
    if args.stats_json:
        with open(args.stats_json, "w", encoding="utf-8") as f:
            json.dump({"pool": len(pool), "plans_changed": tp, "rooms": tr,
                       "vtx_drop": tpt, "reverted": trv,
                       "failed": FAILED, "backup": bk if args.apply else None},
                      f, ensure_ascii=False)
    print("백업 → " + bk if args.apply else "[드라이런] --apply 로 적용")


if __name__ == "__main__":
    main()
