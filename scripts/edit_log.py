#!/usr/bin/env python3
"""edit_log — 보정본을 **누가 · 무엇을 · 왜** 고쳤는지 한 줄씩 남긴다.

왜 필요한가: `corrected/edits` 파일만 보면 "값이 이렇다"는 알 수 있어도
**그 값이 원래 그랬는지, 사람이 고친 건지, 우리가 만든 건지** 알 수 없다.
출처 회계(DATA.md §8.12)의 원칙 — *받은 값 / 기계가 만든 값 / 사람이 만든 값을
분리해 기록한다* — 를 보정 단계에도 적용한다.

**알바와 Claude의 교정은 같은 칸(`우리 교정`)에 넣는다**(2026-11-13 사용자 지시).
사람이 눈으로 판단한 결과라는 점에서 같기 때문이다. 다만 **고친 것(correct)과
새로 만든 것(create)** 은 반드시 구분한다 — 그게 논문에 쓸 기여의 경계다.

기록 위치: `data/staging/corrected/_edit_log.jsonl` (한 줄 = 한 작업, append-only)

필드
  ts        시각(ISO, 초)
  actor     human_annotator | user | claude      ← 전부 '우리 교정'으로 집계
  kind      correct(기존 값을 고침) | create(없던 값을 만듦) | propagate(사본에 복사)
  plan_id   대상 도면 (propagate 는 출처 도면)
  target    room id 등
  field     role | polygon | ...
  before/after
  rule      근거 (예: CORRECTION_GUIDE §3-A)
  backup    되돌릴 경로
  n         영향 장수 (propagate)
"""
from __future__ import annotations

import datetime
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "data", "staging", "corrected", "_edit_log.jsonl")
ACTORS = ("human_annotator", "user", "claude")
KINDS = ("correct", "create", "propagate")


def log(actor, kind, plan_id, *, target=None, field=None, before=None, after=None,
        rule=None, backup=None, n=None, note=None, ts=None, path=LOG):
    if actor not in ACTORS:
        raise ValueError("actor must be one of %s" % (ACTORS,))
    if kind not in KINDS:
        raise ValueError("kind must be one of %s" % (KINDS,))
    rec = {"ts": ts or datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
           "actor": actor, "kind": kind, "plan_id": plan_id}
    for k, v in (("target", target), ("field", field), ("before", before), ("after", after),
                 ("rule", rule), ("backup", backup), ("n", n), ("note", note)):
        if v is not None:
            rec[k] = v
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def read(path=LOG):
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def summary(path=LOG):
    import collections
    rows = read(path)
    by = collections.Counter((r["actor"], r["kind"]) for r in rows)
    plans = collections.defaultdict(set)
    for r in rows:
        plans[r["kind"]].add(r["plan_id"])
    return rows, by, plans


if __name__ == "__main__":
    rows, by, plans = summary()
    print("기록 %d건" % len(rows))
    for (a, k), n in sorted(by.items()):
        print("   %-16s %-10s %4d" % (a, k, n))
    for k, s in sorted(plans.items()):
        print("   %-10s 대상 도면 %d개" % (k, len(s)))
