#!/usr/bin/env python3
"""contamination — 주석이 **자기 도면 것이 아닌** 평면의 정본 목록.

배경: AI-Hub 원천에서 일부 도면에 **다른 도면의 주석**이 붙어 있다. 우리는 주석을
그대로 파싱하므로, 그런 평면은 *그림과 무관한 기하*를 갖는다. 중복 검수는 그걸
"같은 도면"으로 묶고, 학습·평가는 그걸 정답으로 쓴다. 둘 다 막아야 한다.

신호 2종(둘 다 **하한**이다):
  A 크기 불일치 — 주석이 선언한 이미지 크기 ≠ 실제 PNG 크기
                  `data/staging/ann_image_mismatch.json` (scripts/ann_mismatch.py)
                  ⚠ 두 도면의 크기가 같으면 못 잡는다.
  B 픽셀 불일치 — 중복 그룹 안에서 원본 PNG 픽셀이 다른 멤버
                  `data/staging/dedup_pixel_verify.json`
                  ⚠ 사본이 없는 도면은 비교 대상이 없어 못 잡는다.

→ 둘 다 아닌 경우(같은 크기 + 사본 없음)는 **아직 검출 수단이 없다.** 벽선 정합
   검사가 필요하다(ROADMAP).

산출: `data/staging/contaminated_plans.json`  {plan_id: [사유...]}
원칙: 읽기 전용(이 목록을 쓰는 쪽이 막는다).
"""
from __future__ import annotations

import collections
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ST = os.path.join(ROOT, "data", "staging")
OUT = os.path.join(ST, "contaminated_plans.json")


def sheet_of(pid):
    return pid.split("_", 2)[2].rsplit("_u", 1)[0]


def main():
    # 모집단 = parsed 전체. 인덱스는 이미 오염을 빼고 만들어지므로 모집단이 될 수 없다.
    graphs = os.path.join(ST, "parsed", "graphs")
    plans = {f[:-5] for f in os.listdir(graphs) if f.endswith(".json")}
    idx = json.load(open(os.path.join(ST, "dedup_index.json"), encoding="utf-8"))

    size_sheets = {}
    p = os.path.join(ST, "ann_image_mismatch.json")
    if os.path.exists(p):
        for row in json.load(open(p, encoding="utf-8")):
            size_sheets[row[0]] = (tuple(row[1]), tuple(row[2]))
    else:
        print("⚠ %s 없음 — 크기 신호 생략" % p, file=sys.stderr)

    pix = set()
    p = os.path.join(ST, "dedup_pixel_verify.json")
    if os.path.exists(p):
        pix = set(json.load(open(p, encoding="utf-8"))["bad_members"])

    out = collections.defaultdict(list)
    for pid in plans:
        sh = sheet_of(pid)
        if sh in size_sheets:
            decl, real = size_sheets[sh]
            out[pid].append("ann_size_mismatch:주석 %sx%s / 실제 %sx%s"
                            % (decl[0], decl[1], real[0], real[1]))
        if pid in pix:
            out[pid].append("pixel_mismatch:그룹 내 원본 픽셀 불일치")

    json.dump(dict(sorted(out.items())), open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=0)
    both = sum(1 for v in out.values() if len(v) > 1)
    print("오염 평면 %d개 → %s" % (len(out), os.path.relpath(OUT, ROOT)))
    print("   크기 신호 %d · 픽셀 신호 %d · 둘 다 %d"
          % (sum(1 for v in out.values() if any(x.startswith("ann_size") for x in v)),
             sum(1 for v in out.values() if any(x.startswith("pixel") for x in v)), both))
    print("   영향 시트 %d개" % len({sheet_of(p) for p in out}))

    groups = idx["groups"]
    aff = [s for s, g in groups.items() if any(m in out for m in g["members"])]
    mixed = [s for s in aff if any(m not in out for m in groups[s]["members"])]
    print("   오염 그룹 %d (정상과 섞인 그룹 %d — 전파 금지 대상)" % (len(aff), len(mixed)))


if __name__ == "__main__":
    main()
