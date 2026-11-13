#!/usr/bin/env python3
"""door_ambiguity — **문이 배선되지 않는 곳**을 찾는다 = 연결공간이 빠진 자리.

사용자 지적(2026-11-13): *"드레스룸이 문제다. 화장실·안방·드레스룸이 파우더룸 없이
붙으면 문이 문제더라. 그래서 파우더룸을 넣고 안방–(개방)–파우더룸–(문)–욕실·드레스룸으로
연결한다. 다만 **도면마다 구조가 다르니 확인해야 하고, 문이 중요하다.**"*

그래서 파우더룸(또는 복도·전실) 삽입의 기준은 **벽이 아니라 문의 배선 가능성**이다.
벽만 보고 지우면 안 된다(실제로 그렇게 지웠다가 되돌렸다 — 그룹 71fbdaec).

검출하는 세 가지
  A **중복 배선** — 같은 자리(bbox)의 문이 서로 다른 방 쌍으로 2번 이상 기록됨
                    → 세 방이 한 점에서 만나 어느 쌍인지 못 정한 자리다
  B **안 닿는 쌍** — 문이 잇는다는 두 방의 폴리곤이 실제로는 떨어져 있음
  C **3방 접합**   — 한 문 주변 r px 안에 서로 다른 방이 3개 이상

읽기 전용. usage:
  python scripts/door_ambiguity.py --sample 2000
  python scripts/door_ambiguity.py --plans A B C
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import random

from shapely.geometry import Polygon, box

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDITS = os.path.join(ROOT, "data", "staging", "corrected", "edits")
NEAR = 14.0          # 문 주변 탐색 반경(px)
TOUCH = 20.0


def poly(r):
    pts = r.get("polygon") or []
    if len(pts) < 3:
        return None
    q = Polygon([(x, y) for x, y in pts])
    return q if q.is_valid else q.buffer(0)


def check(path):
    g = json.load(open(path, encoding="utf-8"))
    rooms = {rid: r for rid, r in (g.get("rooms") or {}).items()}
    polys = {rid: poly(r) for rid, r in rooms.items()}
    doors = g.get("doors") or []
    out = []

    by_box = collections.defaultdict(list)
    for d in doors:
        b = d.get("bbox_px")
        if not b:
            continue
        key = tuple(round(v) for v in b)
        by_box[key].append(tuple(d.get("connects") or []))
    for key, pairs in by_box.items():
        uniq = {tuple(sorted(map(str, p))) for p in pairs if len(p) == 2}
        if len(uniq) > 1:
            out.append(("A_중복배선", key, [[rooms.get(x, {}).get("role", x) for x in p]
                                         for p in uniq]))

    for d in doors:
        c = d.get("connects") or []
        b = d.get("bbox_px")
        if len(c) != 2 or not b:
            continue
        pa, pb = polys.get(str(c[0])), polys.get(str(c[1]))
        if pa is None or pb is None or pa.is_empty or pb.is_empty:
            continue
        if pa.distance(pb) > TOUCH:
            out.append(("B_안닿음", tuple(round(v) for v in b),
                        [rooms.get(str(c[0]), {}).get("role"),
                         rooms.get(str(c[1]), {}).get("role"),
                         round(pa.distance(pb), 1)]))

    for d in doors:
        b = d.get("bbox_px")
        if not b:
            continue
        zone = box(b[0] - NEAR, b[1] - NEAR, b[0] + b[2] + NEAR, b[1] + b[3] + NEAR)
        hit = [rooms[rid].get("role") for rid, q in polys.items()
               if q is not None and not q.is_empty and q.distance(zone) <= 0.1]
        if len(hit) >= 3:
            out.append(("C_3방접합", tuple(round(v) for v in b), sorted(hit)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", nargs="*", default=[])
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    pool = list(args.plans)
    if args.sample:
        fs = [f[:-5] for f in os.listdir(EDITS) if f.endswith(".json")]
        random.Random(args.seed).shuffle(fs)
        pool += fs[: args.sample]

    tally = collections.Counter()
    plans_with = collections.Counter()
    rolepairs = collections.Counter()
    rows = []
    for pid in pool:
        p = os.path.join(EDITS, pid + ".json")
        if not os.path.exists(p):
            continue
        try:
            res = check(p)
        except Exception:  # noqa: BLE001
            tally["오류"] += 1
            continue
        kinds = {k for k, _, _ in res}
        for k in kinds:
            plans_with[k] += 1
        for k, b, info in res:
            tally[k] += 1
            if k == "A_중복배선":
                for pr in info:
                    rolepairs[tuple(sorted(str(x) for x in pr))] += 1
        if res and args.plans:
            print("==", pid)
            for k, b, info in res:
                print("   %-10s %s %s" % (k, b, info))
        rows.append([pid, [[k, list(b), info] for k, b, info in res]])

    print("\n검사 %d장" % len(pool))
    for k in ("A_중복배선", "B_안닿음", "C_3방접합"):
        print("   %-10s 건수 %5d · 해당 도면 %4d장 (%.1f%%)"
              % (k, tally[k], plans_with[k], 100.0 * plans_with[k] / max(len(pool), 1)))
    if rolepairs:
        print("   중복배선에 많이 걸린 방 쌍:")
        for pr, n in rolepairs.most_common(8):
            print("      %-22s %d" % (" / ".join(pr), n))
    if args.out:
        json.dump(rows, open(args.out, "w", encoding="utf-8"), ensure_ascii=False)
        print("→", args.out)


if __name__ == "__main__":
    main()
