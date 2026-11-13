#!/usr/bin/env python3
"""확정한 대표를 중복 그룹의 형제에 전파한다 (CLI).

사용자가 중복 검수 화면에서 "같은 도면"이라고 확인한 그룹에 대해,
대표 보정본을 형제 사본에 복사한다. 평행이동·미러 변환 + 정렬검증(2px)을 거친다.

mode="unify" : 다르게 보정된 형제까지 덮어쓴다(백업 후). 이미 같으면 건너뛴다.
mode="fill"  : 보정이 없는 형제만 채운다.

usage:
  python scripts/do_propagate.py <sig> <source_plan_id> [--mode unify] [--apply]
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = "/home/ju/plan2graph"
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

from plan2graph import dedup_review          # noqa: E402
from plan2graph.topoedit import ROLE_COLOR   # noqa: E402

ST = os.path.join(ROOT, "data", "staging")
CTX = {"GRAPHS": os.path.join(ST, "parsed", "graphs"),
       "EDITS": os.path.join(ST, "corrected", "edits"),
       "INDEX": os.path.join(ST, "dedup_index.json"),
       "ROLE_COLOR": ROLE_COLOR,
       "PNG": lambda *a, **k: None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sig")
    ap.add_argument("source")
    ap.add_argument("--mode", default="unify", choices=("fill", "unify"))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    idx = dedup_review.load_index(CTX)
    grp = (idx.get("groups") or {}).get(a.sig)
    if not grp:
        print("그룹 없음: %s" % a.sig)
        print("비슷한 키:", [k for k in (idx.get("groups") or {}) if k.startswith(a.sig[:8])][:5])
        return
    mem = grp if isinstance(grp, list) else (grp.get("members") or grp.get("plans") or [])
    ids = [m if isinstance(m, str) else (m.get("plan_id") or m.get("gid")) for m in mem]
    print("그룹 %s · 사본 %d장" % (a.sig[:8], len(ids)))
    for i in ids:
        print("   %s%s" % (i, "  ← 원본" if i == a.source else ""))
    if a.source not in ids:
        print("⚠ 원본이 그룹에 없다. 중단.")
        return
    if not a.apply:
        print("\n[드라이런] --apply 로 실행")
        return
    r = dedup_review.propagate(CTX, a.sig, a.source, mode=a.mode)
    print()
    for k, v in r.items():
        print("   %-14s %s" % (k, v))


if __name__ == "__main__":
    main()
