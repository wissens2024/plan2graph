#!/usr/bin/env python3
"""태그로 **문 위치만** 바로잡는다 — 옮기기만 하고 만들거나 버리지 않는다.

사용자(2026-11-13): *"주황색 제거하고 녹색 위치만 다시 잡아주고."*
태그는 **문 위치를 고치는 재료**이지 화면에 따로 그릴 것이 아니었다.

왜 보수적인가 — 첫 판에서 두 가지를 망쳤다:
  · 욕실·드레스룸 **외벽에 문을 만들었다**. 둘 다 창 태그인데 가장 가까운 창에서
    109px 떨어져 있어 "창 근처면 창" 규칙에 안 걸렸다. **태그 글자를 아직 못 읽으니
    문/창을 확실히 가를 수 없다** → 짝이 없는 태그는 만들지 않고 보류한다.
  · 사용자가 직접 확인해 준 **거실↔안방 문을 버릴 뻔했다**(추정 위치라는 이유로).
    → 아무것도 버리지 않는다. 위치가 추정이라는 사실만 표시한다.

하는 일: 태그의 **잇는 방 쌍**이 기존 문과 같으면 그 문의 position 을 태그 자리로 옮긴다.
그게 전부다. 원래 위치는 `position_parsed`, 출처는 `position_source="tag_detect"`.

읽기 전용(기본). --apply 를 줘야 쓴다.
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import shutil

ROOT = "/home/ju/plan2graph"
EDITS = os.path.join(ROOT, "data", "staging", "corrected", "edits")
BK = os.path.join(ROOT, "data", "staging", "corrected", "_manual_backup")
WIN_NEAR = 85.0        # 태그가 이 거리 안에 창이 있으면 창 태그로 본다


def run(pid, apply_=False):
    p = os.path.join(EDITS, pid + ".json")
    g = json.load(open(p, encoding="utf-8"))
    R = {str(k): v for k, v in (g.get("rooms") or {}).items()}

    def nm(r):
        return "%s(%s)" % ((R.get(str(r)) or {}).get("role"), r) if str(r) in R else "외부"

    tags = g.get("tag_openings") or []
    if not tags:
        print("%s — tag_openings 가 없다. scripts/tag_openings.py 를 먼저 돌려라." % pid)
        return
    wins = [w.get("position") for w in (g.get("windows") or []) if w.get("position")]

    door_tags, win_tags = [], []
    for t in tags:
        q = t["pos"]
        d = min((math.hypot(q[0] - w[0], q[1] - w[1]) for w in wins), default=9e9)
        (win_tags if d <= WIN_NEAR else door_tags).append(t)

    print("%s — 태그 %d개 = 창 근처 %d · 나머지 %d"
          % (pid, len(tags), len(win_tags), len(door_tags)))

    doors = list(g.get("doors") or [])
    used, unmatched = set(), []
    moved = 0
    print("   %-20s %-26s %s" % ("태그 위치", "잇는 방", "처리"))
    for t in sorted(door_tags, key=lambda v: (v["pos"][1], v["pos"][0])):
        q = t["pos"]
        rooms = [str(x) for x in (t.get("rooms") or []) if str(x) in R]
        key = frozenset(rooms)
        hit = None
        for i, d in enumerate(doors):
            if i in used:
                continue
            c = frozenset(str(x) for x in (d.get("connects") or []) if str(x) in R)
            if len(rooms) == 2 and c == key:
                hit = i
                break
        if hit is None and len(rooms) == 1:
            for i, d in enumerate(doors):
                if i in used:
                    continue
                c = [str(x) for x in (d.get("connects") or []) if str(x) in R]
                if d.get("to_outside") and c == rooms:
                    hit = i
                    break
        if hit is None:
            unmatched.append(t)
            print("   (%5.0f,%5.0f) %-26s 짝 없음 — 보류(문/창 미상)"
                  % (q[0], q[1], " + ".join(nm(x) for x in rooms) or "외부만"))
            continue
        d = doors[hit]
        used.add(hit)
        old = d.get("position")
        if old and "position_parsed" not in d:
            d["position_parsed"] = old
        dist = math.hypot(old[0] - q[0], old[1] - q[1]) if old else -1
        d["position"] = q
        d["position_source"] = "tag_detect"
        d["on_wall"] = t.get("wall")
        moved += 1
        print("   (%5.0f,%5.0f) %-26s %-5s 옮김 %5.0fpx"
              % (q[0], q[1], " + ".join(nm(x) for x in rooms), d.get("id"), dist))

    est = 0
    for i, d in enumerate(doors):
        if (i not in used and d.get("source") == "link_rebuild"
                and d.get("position_source") != "user_override"):
            d["position_source"] = "estimated"
            est += 1
    print("   → 옮김 %d · 보류 %d · 추정으로 남은 문 %d · 문 %d개"
          % (moved, len(unmatched), est, len(doors)))

    if apply_:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        bk = os.path.join(BK, stamp + "_tagdoors")
        os.makedirs(bk, exist_ok=True)
        shutil.copy(p, os.path.join(bk, pid + ".json"))
        g["doors"] = doors
        g["n_doors"] = len(doors)
        g.pop("tag_openings", None)              # 화면에 따로 그리지 않는다
        if unmatched:
            g["tag_unmatched"] = unmatched       # 글자를 읽으면 처리할 것들
        g.setdefault("provenance", {})["door_pos"] = "tag_detect"
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(g, f, ensure_ascii=False)
        os.replace(tmp, p)
        print("   백업 %s" % bk)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", nargs="*", default=[])
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    for pid in a.plans:
        run(pid, a.apply)


if __name__ == "__main__":
    main()
