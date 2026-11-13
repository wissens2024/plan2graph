#!/usr/bin/env python3
"""merge_dup_openings — 중복 사본이 가진 개구부를 가져와 합친다.

사용자(2026-11-13): "APT_FP_091800b0_17446941_u1 여긴 기하를 다시 생성해줘."

그 도면은 AI-Hub 원천에 SPA(방 폴리곤)밖에 없어 문 1개·창 0개였다. 그런데
**같은 도면의 중복 사본**(dedup 그룹)에 문 6개·창 10개가 들어 있었다. 같은 그림을
두 번 주석한 것이니, 지어내는 대신 **가져오면 된다.**

왜 좌표만 베끼나 — 방 id 는 사본마다 다르다. id 를 맞추려 들면 틀린다.
좌표계는 같으므로(범위·축척이 일치하는 그룹만 다룬다) **개구부 좌표만** 옮기고,
어느 방을 잇는지는 `rebuild_links` 가 기하로 다시 풀게 둔다. 이미 그 일을 하는
코드가 있으니 두 번 짜지 않는다.

안전장치
  · 두 도면의 **방 폴리곤 합집합 IoU >= --min-iou** 일 때만 옮긴다(같은 그림인가).
  · 대상에 이미 그 자리(--near px 이내)에 같은 종류 개구부가 있으면 건너뛴다.
  · 가져온 개구부에는 `source="dup_merge"`, `from_plan=<사본 id>` 를 박는다.
    받은 값도 우리가 만든 값도 아닌 **옮겨온 값**이므로 출처를 남긴다.

읽기 전용(기본). --apply 를 줘야 쓴다.
"""
from __future__ import annotations

import argparse
import copy
import datetime
import json
import os
import shutil
import sys

from shapely.geometry import Polygon
from shapely.ops import unary_union

ROOT = "/home/ju/plan2graph"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

EDITS = os.path.join(ROOT, "data", "staging", "corrected", "edits")
BK = os.path.join(ROOT, "data", "staging", "corrected", "_manual_backup")


def load(pid):
    return json.load(open(os.path.join(EDITS, pid + ".json"), encoding="utf-8"))


def union_of(g):
    qs = []
    for r in (g.get("rooms") or {}).values():
        p = r.get("polygon") or []
        if len(p) >= 3:
            q = Polygon([(x, y) for x, y in p])
            if not q.is_valid:
                q = q.buffer(0)
            if not q.is_empty:
                qs.append(q)
    return unary_union(qs) if qs else None


def near(a, b, tol):
    return a and b and ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 <= tol


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("source")
    ap.add_argument("--min-iou", type=float, default=0.95)
    ap.add_argument("--near", type=float, default=25.0)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    tg, sg = load(a.target), load(a.source)
    ua, ub = union_of(tg), union_of(sg)
    if ua is None or ub is None:
        print("폴리곤 없음"), sys.exit(1)
    iou = ua.intersection(ub).area / max(ua.union(ub).area, 1e-9)
    print("방 합집합 IoU %.4f  (대상 %.0fpx2 · 사본 %.0fpx2)" % (iou, ua.area, ub.area))
    if iou < a.min_iou:
        print("같은 그림으로 보기 어렵다 — 중단 (--min-iou %.2f)" % a.min_iou)
        sys.exit(1)

    tdoors = list(tg.get("doors") or [])
    twins = list(tg.get("windows") or [])
    add_d, add_w, skip = [], [], 0
    for d in (sg.get("doors") or []):
        p = d.get("position")
        if any(near(p, e.get("position"), a.near) for e in tdoors):
            skip += 1
            continue
        nd = copy.deepcopy(d)
        nd["id"] = "m_" + str(d.get("id"))
        nd["connects"] = []                 # rebuild_links 가 기하로 다시 푼다
        nd["source"] = "dup_merge"
        nd["from_plan"] = a.source
        add_d.append(nd)
    for w in (sg.get("windows") or []):
        p = w.get("position")
        if any(near(p, e.get("position"), a.near) for e in twins):
            skip += 1
            continue
        nw = copy.deepcopy(w)
        nw["id"] = "m_" + str(w.get("id"))
        nw["belongs_to"] = None             # rebuild_links 가 다시 푼다
        nw["source"] = "dup_merge"
        nw["from_plan"] = a.source
        add_w.append(nw)

    print("가져올 문 %d · 창 %d · 이미 있어 건너뜀 %d"
          % (len(add_d), len(add_w), skip))
    for d in add_d:
        print("   문 %-8s %s  폭 %s" % (d["id"],
              [round(v) for v in d["position"]] if d.get("position") else None,
              d.get("width_m")))
    for w in add_w:
        print("   창 %-8s %s  폭 %s" % (w["id"],
              [round(v) for v in w["position"]] if w.get("position") else None,
              w.get("width_m")))

    if not a.apply:
        print("[드라이런] --apply 로 적용")
        return
    tg["doors"] = tdoors + add_d
    tg["windows"] = twins + add_w
    tg["n_doors"] = len(tg["doors"])
    tg["n_windows"] = len(tg["windows"])
    pv = tg.setdefault("provenance", {})
    pv["openings"] = "dup_merge"
    pv["openings_from"] = a.source
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bk = os.path.join(BK, stamp + "_dupmerge")
    os.makedirs(bk, exist_ok=True)
    p = os.path.join(EDITS, a.target + ".json")
    shutil.copy(p, os.path.join(bk, a.target + ".json"))
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tg, f, ensure_ascii=False)
    os.replace(tmp, p)
    print("적용 완료 · 백업 -> " + bk)
    print("다음: rebuild_links 로 방 배정을 다시 풀고, snap_doors 로 벽에 붙여라.")


if __name__ == "__main__":
    main()
