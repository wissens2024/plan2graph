#!/usr/bin/env python3
"""snap_doors — 문을 두 방 사이의 벽 위로 끌어다 놓는다.

사용자(2026-11-13): "나머지는 문의 위치만 조정해줘" — 같은 지적이 도면마다 반복됐다.

원인은 하나다. AI-Hub 문 주석의 bbox_px 는 여닫이 호(swing arc)를 감싼 상자다.
그 중심은 경첩도 벽도 아니라 호의 한가운데라서, 문 위치가 늘 방 안쪽으로 20~30px
들어가 앉는다. 실측(APT_FP_396cb308_17447720_u3) 9개 문 중 7개가 그랬다.

규칙: 문이 잇는 두 방의 맞닿은 띠(서로의 버퍼가 겹치는 영역 = 벽) 위에서
현재 위치와 가장 가까운 점으로 옮긴다. 방 폴리곤은 직사각화를 거쳐 믿을 만하다.

손대지 않는 것
  · position_source 가 user_override / tag_detect — 사람이 확인했거나 태그로 잡았다
  · 두 방을 잇지 않는 문(외부문) — 어느 벽인지 띠로 정의되지 않는다
  · 옮길 거리가 --max-move 를 넘는 문 — 배선 자체가 의심스러우니 건드리지 않고 센다

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
import time as _time

from shapely.geometry import Point, Polygon
from shapely.ops import nearest_points

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDITS = os.path.join(ROOT, "data", "staging", "corrected", "edits")
BK = os.path.join(ROOT, "data", "staging", "corrected", "_manual_backup")

KEEP = ("user_override", "tag_detect")
MAX_MOVE = 60.0


def poly_of(r):
    p = r.get("polygon") or []
    if len(p) < 3:
        return None
    q = Polygon([(x, y) for x, y in p])
    if not q.is_valid:
        q = q.buffer(0)
    return q if (not q.is_empty and q.geom_type == "Polygon") else None


def run(pid, max_move=MAX_MOVE, apply_=False, verbose=False, bk=None):
    p = os.path.join(EDITS, pid + ".json")
    if not os.path.exists(p):
        return None
    g = json.load(open(p, encoding="utf-8"))
    rooms = {str(k): v for k, v in (g.get("rooms") or {}).items()}
    polys = {}
    for rid, r in rooms.items():
        q = poly_of(r)
        if q is not None:
            polys[rid] = q

    moved = far = skipped = noband = 0
    rows = []
    for d in (g.get("doors") or []):
        pos = d.get("position")
        cn = [str(x) for x in (d.get("connects") or []) if str(x) in polys]
        if not pos or len(cn) != 2:
            continue
        if d.get("position_source") in KEEP:
            skipped += 1
            continue
        pa, pb = polys[cn[0]], polys[cn[1]]
        gap = pa.distance(pb)
        br = max(6.0, gap / 2.0 + 3.0)
        try:
            band = pa.buffer(br).intersection(pb.buffer(br))
        except Exception:
            band = None
        if band is None or band.is_empty:
            noband += 1
            continue
        pt = Point(pos[0], pos[1])
        if band.contains(pt):
            continue
        new = nearest_points(band, pt)[0]
        dist = pt.distance(new)
        if dist > max_move:
            far += 1
            rows.append((d.get("id"), cn, pos, None, dist, "너무 멂 — 그대로"))
            continue
        rows.append((d.get("id"), cn, pos, [round(new.x, 1), round(new.y, 1)], dist, ""))
        if "position_parsed" not in d:
            d["position_parsed"] = pos
        d["position"] = [round(new.x, 1), round(new.y, 1)]
        d["position_source"] = "wall_snap"
        moved += 1

    if moved:
        byid = {d.get("id"): d for d in (g.get("doors") or [])}
        for e in (g.get("edges") or []):
            dd = byid.get(e.get("door_id"))
            if dd is not None:
                e["door_pos"] = dd.get("position")

    if verbose:
        def nm(r):
            return "%s(%s)" % ((rooms.get(r) or {}).get("role"), r)
        print("%s — 벽 위로 옮긴 문 %d · 너무 멀어 보류 %d · 사람·태그라 건너뜀 %d · 띠 없음 %d"
              % (pid, moved, far, skipped, noband))
        for did, cn, old, new, dist, note in rows:
            print("   %-6s %-26s (%6.0f,%6.0f) -> %s  %4.0fpx %s"
                  % (did, " + ".join(nm(c) for c in cn), old[0], old[1],
                     ("(%6.0f,%6.0f)" % tuple(new)) if new else "       -      ",
                     dist, note))

    if apply_ and moved:
        bk = bk or os.path.join(
            BK, datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_snapdoors")
        os.makedirs(bk, exist_ok=True)
        shutil.copy(p, os.path.join(bk, pid + ".json"))
        tmp = p + ".%d.tmp" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(g, f, ensure_ascii=False)
        os.replace(tmp, p)
        if verbose:
            print("   백업 %s" % bk)
    return moved, far, skipped, noband


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", nargs="*", default=[])
    ap.add_argument("--sample", type=int, default=0, help="0 이면 --plans 만. 전체는 --sample -1")
    ap.add_argument("--max-move", type=float, default=MAX_MOVE)
    ap.add_argument("--seed", type=int, default=9)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--shard", default=None)
    ap.add_argument("--exclude-file", default=None)
    ap.add_argument("--progress", type=int, default=0)
    ap.add_argument("--stats-json", default=None)
    a = ap.parse_args()

    pool = list(a.plans)
    if a.sample:
        fs = [f[:-5] for f in os.listdir(EDITS) if f.endswith(".json")]
        random.Random(a.seed).shuffle(fs)
        pool += fs if a.sample < 0 else fs[: a.sample]
    ex = set()
    if a.exclude_file and os.path.exists(a.exclude_file):
        ex = {l.strip() for l in open(a.exclude_file, encoding="utf-8") if l.strip()}
    n_ex = sum(1 for q in pool if q in ex)
    pool = [q for q in pool if q not in ex]
    if n_ex:
        print("보호 대상 %d장 제외" % n_ex)
    if a.shard:
        si, sn = (int(x) for x in a.shard.split("/"))
        pool = [q for k, q in enumerate(sorted(set(pool))) if k % sn == si]

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bk = os.path.join(BK, stamp + "_snapdoors")
    T = [0, 0, 0, 0]
    n = changed = 0
    FAILED = []
    t0 = _time.time()
    for i, pid in enumerate(pool, 1):
        if a.progress and i % a.progress == 0:
            el = _time.time() - t0
            print("[진행] %d/%d  %.1f장/초  경과 %.1f분  실패 %d"
                  % (i, len(pool), i / max(el, 1e-9), el / 60.0, len(FAILED)), flush=True)
        try:
            r = run(pid, a.max_move, a.apply, verbose=bool(a.plans), bk=bk)
        except Exception as exc:
            FAILED.append((pid, type(exc).__name__, str(exc)[:200]))
            continue
        if r:
            n += 1
            for k in range(4):
                T[k] += r[k]
            if r[0]:
                changed += 1
    if not a.plans:
        print("도면 %d · 바뀐 도면 %d · 벽 위로 옮긴 문 %d · 너무 멀어 보류 %d · "
              "사람·태그라 건너뜀 %d · 띠 없음 %d" % (n, changed, T[0], T[1], T[2], T[3]))
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
            json.dump({"plans": n, "changed": changed, "moved": T[0], "far": T[1],
                       "kept": T[2], "noband": T[3], "failed": FAILED,
                       "backup": bk if a.apply else None}, f, ensure_ascii=False)
    print("[드라이런] --apply 로 적용" if not a.apply else "적용 완료 · 백업 -> " + bk)


if __name__ == "__main__":
    main()
