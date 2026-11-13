#!/usr/bin/env python3
"""rectify_rooms — **직사각형이어야 할 방을 직사각형으로 되돌린다.**

사용자(2026-11-13): *"직사각형도 있고 각각을 너무 꺾었다. 굳이 이렇게까지 모양을 만들
이유가 없는데. 창틀도 꺾고, 내외벽을 꺾었어. 이런 거 그냥 찾아서 모두 직사각형으로 하면
안 되나?"* — *"창문이나 문쪽을 이상하게 꺾어."*

원인: AI-Hub/V2V 가 방 마스크 외곽선을 그대로 땄다. 마스크가 **창호·문 기호를 피해**
돌아가면서 방 폴리곤에 홈이 생긴다. 실측 `APT_FP_0a142c11_17446941_u1`(spa=v2v_img):
발코니 112점·사선변 52 · 발코니 133점·사선변 52 · 안방 38점 · 주방 39점.

⚠ **진짜 L자는 건드리지 않는다.** 같은 도면의 복도는 bbox 채움 49% 로 실제 L자이고,
한국 평면의 34.3% 가 비직사각이다([[drawing-completeness-goal]] 계열 측정). 그래서
**bbox 를 거의 채우는 방만** 직사각형으로 되돌린다.

규칙
  · bbox 채움률 ≥ `--min-fill`(기본 0.88) 이면 **bbox 로 교체**
  · 교체로 다른 방과 **새 겹침이 2px² 넘게 생기면 하지 않는다**
  · 채움률 높은 방부터 처리해, 확실한 것이 먼저 자리를 잡게 한다
  · 원래 폴리곤은 `polygon_parsed` 에 남긴다(되돌릴 수 있게)

읽기 전용(기본). --apply 를 줘야 쓴다.
usage:
  python scripts/rectify_rooms.py --sample 2000
  python scripts/rectify_rooms.py --sample -1 --apply       # 전체
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

from shapely.geometry import Polygon, box

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDITS = os.path.join(ROOT, "data", "staging", "corrected", "edits")
BK = os.path.join(ROOT, "data", "staging", "corrected", "_manual_backup")


def poly_of(r):
    p = r.get("polygon") or []
    if len(p) < 3:
        return None
    q = Polygon([(x, y) for x, y in p])
    if not q.is_valid:
        q = q.buffer(0)
    return q if (not q.is_empty and q.geom_type == "Polygon") else None


def q_overlap(a, b):
    try:
        return a.intersection(b).area
    except Exception:
        return 0.0


def run(pid, min_fill=0.88, max_ov=2.0, apply_=False, verbose=True, bk=None):
    p = os.path.join(EDITS, pid + ".json")
    if not os.path.exists(p):
        return None
    g = json.load(open(p, encoding="utf-8"))
    rooms = g.get("rooms") or {}
    scale = g.get("scale_mm_per_px")
    scale = float(scale) if scale else None
    cur = {}
    for rid, r in rooms.items():
        q = poly_of(r)
        if q is not None:
            cur[str(rid)] = q
    cand = []
    for rid, q in cur.items():
        b = q.bounds
        ba = (b[2] - b[0]) * (b[3] - b[1])
        if ba <= 0:
            continue
        fill = q.area / ba
        if fill >= min_fill:
            cand.append((fill, rid, box(*b)))
    cand.sort(reverse=True)

    done, skipped = [], []
    for fill, rid, rect in cand:
        bad = 0.0
        for oid, oq in cur.items():
            if oid == rid:
                continue
            try:
                bad += max(0.0, rect.intersection(oq).area - q_overlap(cur[rid], oq))
            except Exception:
                pass
        if bad > max_ov:
            skipped.append((rid, fill, bad))
            continue
        cur[rid] = rect
        done.append((rid, fill, len(rooms[rid]["polygon"])))

    if verbose:
        print("%s — 직사각형으로 되돌린 방 %d · 겹침 때문에 건너뜀 %d"
              % (pid, len(done), len(skipped)))
        for rid, fill, n0 in done:
            print("   %-8s(%-3s) 채움 %3.0f%% · 꼭짓점 %3d → 4"
                  % (rooms[rid].get("role"), rid, 100 * fill, n0))
        for rid, fill, bad in skipped:
            print("   %-8s(%-3s) 채움 %3.0f%% — 새 겹침 %.0f px2 때문에 건너뜀"
                  % (rooms[rid].get("role"), rid, 100 * fill, bad))
        left = [(rooms[r].get("role"), r, cur[r].area /
                 ((cur[r].bounds[2] - cur[r].bounds[0]) * (cur[r].bounds[3] - cur[r].bounds[1])))
                for r in cur if r not in [d[0] for d in done]]
        if left:
            print("   그대로 둔 방(진짜 비직사각): "
                  + " · ".join("%s(%s) %.0f%%" % (a, b, 100 * c) for a, b, c in left))

    if apply_ and done:
        bk = bk or os.path.join(
            BK, datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_rectify")
        os.makedirs(bk, exist_ok=True)
        shutil.copy(p, os.path.join(bk, pid + ".json"))
        for rid, fill, n0 in done:
            r = rooms[rid]
            if "polygon_parsed" not in r:
                r["polygon_parsed"] = r["polygon"]
            q = cur[rid]
            r["polygon"] = [[round(x, 1), round(y, 1)] for x, y in list(q.exterior.coords)[:-1]]
            b = q.bounds
            r["bbox_px"] = [round(b[0], 1), round(b[1], 1),
                            round(b[2] - b[0], 1), round(b[3] - b[1], 1)]
            r["area_px"] = round(q.area, 1)
            c = q.centroid
            r["centroid"] = [round(c.x, 1), round(c.y, 1)]
            if scale:
                r["area_m2"] = round(q.area * (scale / 1000.0) ** 2, 2)
            if "perimeter_px" in r:
                r["perimeter_px"] = round(q.length, 1)
            r["shape_source"] = "rectified"
        tmp = p + ".%d.tmp" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(g, f, ensure_ascii=False)
        os.replace(tmp, p)
        if verbose:
            print("   백업 %s" % bk)
    return len(done), len(skipped)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", nargs="*", default=[])
    ap.add_argument("--sample", type=int, default=0,
                    help="0 이면 --plans 만. 전체는 --sample -1")
    ap.add_argument("--min-fill", type=float, default=0.88)
    ap.add_argument("--max-overlap", type=float, default=2.0)
    ap.add_argument("--seed", type=int, default=9)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--shard", default=None,
                    help="I/N - 대상을 N 등분해 I 번째만 돈다(병렬 실행용)")
    ap.add_argument("--plans-file", default=None,
                    help="한 줄에 하나씩 적힌 plan_id 만 돈다")
    ap.add_argument("--exclude-file", default=None,
                    help="한 줄에 하나씩 적힌 plan_id 는 손대지 않는다")
    ap.add_argument("--progress", type=int, default=0)
    ap.add_argument("--stats-json", default=None)
    a = ap.parse_args()
    pool = list(a.plans)
    if a.sample:
        fs = [f[:-5] for f in os.listdir(EDITS) if f.endswith(".json")]
        random.Random(a.seed).shuffle(fs)
        pool += fs if a.sample < 0 else fs[: a.sample]

    if a.plans_file:
        pool += [l.strip() for l in open(a.plans_file, encoding="utf-8") if l.strip()]

    ex = set()
    if a.exclude_file and os.path.exists(a.exclude_file):
        ex = {l.strip() for l in open(a.exclude_file, encoding="utf-8") if l.strip()}
    n_ex = sum(1 for p in pool if p in ex)
    pool = [p for p in pool if p not in ex]
    if n_ex:
        print("보호 대상 %d장 제외" % n_ex)

    if a.shard:
        si, sn = (int(x) for x in a.shard.split("/"))
        pool = [q for k, q in enumerate(sorted(set(pool))) if k % sn == si]

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bk = os.path.join(BK, stamp + "_rectify")
    tot_d = tot_s = n = 0
    plans_changed = 0
    FAILED = []
    _t0 = _time.time()
    for _i, pid in enumerate(pool, 1):
        if a.progress and _i % a.progress == 0:
            el = _time.time() - _t0
            print("[진행] %d/%d  %.1f장/초  경과 %.1f분  실패 %d"
                  % (_i, len(pool), _i / max(el, 1e-9), el / 60.0, len(FAILED)),
                  flush=True)
        try:                             # 한 장이 깨져도 나머지는 돈다
            r = run(pid, a.min_fill, a.max_overlap, a.apply,
                    verbose=bool(a.plans), bk=bk)
        except Exception as exc:
            FAILED.append((pid, type(exc).__name__, str(exc)[:200]))
            continue
        if r:
            tot_d += r[0]
            tot_s += r[1]
            n += 1
            if r[0]:
                plans_changed += 1
    if not a.plans:
        print("도면 %d · 바뀐 도면 %d · 직사각형으로 되돌린 방 %d · 겹침 때문에 건너뜀 %d"
              % (n, plans_changed, tot_d, tot_s))
    if FAILED:
        kinds = collections.Counter(k for _, k, _ in FAILED)
        print("   ! 실패 %d건 - %s"
              % (len(FAILED), " · ".join("%s %d" % kv for kv in kinds.most_common())))
        for pid, k, m in FAILED[:20]:
            print("      %s  %s  %s" % (pid, k, m))
    else:
        print("   실패 0건")
    if a.stats_json:
        with open(a.stats_json, "w", encoding="utf-8") as f:
            json.dump({"pool": len(pool), "plans": n, "plans_changed": plans_changed,
                       "rectified": tot_d, "skipped_overlap": tot_s,
                       "failed": FAILED, "backup": bk if a.apply else None},
                      f, ensure_ascii=False)
    print("[드라이런] --apply 로 적용" if not a.apply else "적용 완료 · 백업 → " + bk)


if __name__ == "__main__":
    main()
