#!/usr/bin/env python3
"""seed_edits — 미보정 도면의 보정본 틀을 parsed 에서 만든다.

사용자가 아직 아무도 손대지 않은 도면을 가리키며 "기하연결 다시 생성해줘" 할 때,
`corrected/edits/` 에 파일이 없어 보정 체인을 돌릴 대상이 없다.
`build_corrected_auto.py` 는 코퍼스 전체를 도는 빌더라 한 장에는 과하다.

parsed 와 edits 는 **스키마가 완전히 같다**(키 집합 동일 · `corrected` 플래그만 다름).
그래서 parsed 를 그대로 옮기고 체인(snap_axis -> rectify -> rebuild_links -> snap_doors)을
돌리면 다른 도면과 같은 상태가 된다.

출처를 남긴다: `meta.seeded_from_parsed` 에 시각을 박는다 — 사람이 보정한 것도,
전파받은 것도 아니라 **기계가 parsed 에서 새로 깐 것**임을 나중에 구분할 수 있어야 한다.

이미 edits 가 있으면 **거부한다**(--force 가 없으면). 사람 보정을 덮어쓰면 안 된다.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil

ROOT = "/home/ju/plan2graph"
PARSED = os.path.join(ROOT, "data", "staging", "parsed", "graphs")
EDITS = os.path.join(ROOT, "data", "staging", "corrected", "edits")
BK = os.path.join(ROOT, "data", "staging", "corrected", "_manual_backup")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plans", nargs="+")
    ap.add_argument("--force", action="store_true",
                    help="이미 있는 edits 를 덮어쓴다 — 사람 보정이 날아갈 수 있다")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bk = os.path.join(BK, stamp + "_seed")
    n = 0
    for pid in a.plans:
        src = os.path.join(PARSED, pid + ".json")
        dst = os.path.join(EDITS, pid + ".json")
        if not os.path.exists(src):
            print("   X %-34s parsed 없음" % pid)
            continue
        if os.path.exists(dst) and not a.force:
            print("   X %-34s 이미 edits 가 있다 — 덮어쓰려면 --force" % pid)
            continue
        g = json.load(open(src, encoding="utf-8"))
        print("   %-34s 방 %d · 문 %d · 창 %d · 엣지 %d%s"
              % (pid, len(g.get("rooms") or {}), len(g.get("doors") or []),
                 len(g.get("windows") or []), len(g.get("edges") or []),
                 "  (덮어씀)" if os.path.exists(dst) else ""))
        if not a.apply:
            continue
        g["corrected"] = True
        meta = g.setdefault("meta", {})
        meta["seeded_from_parsed"] = stamp
        if os.path.exists(dst):
            os.makedirs(bk, exist_ok=True)
            shutil.copy2(dst, os.path.join(bk, pid + ".json"))
        tmp = dst + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(g, f, ensure_ascii=False)
        os.replace(tmp, dst)
        n += 1
    print("\n%s %d장" % ("깐 보정본" if a.apply else "[드라이런] 대상", n if a.apply else 0))
    if a.apply and n:
        print("다음: snap_axis -> rectify_rooms -> rebuild_links -> snap_doors -> audit_plan")


if __name__ == "__main__":
    main()
