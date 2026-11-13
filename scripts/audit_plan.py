#!/usr/bin/env python3
"""audit_plan — **완료를 선언하기 전에** 모든 엣지·방의 근거를 표로 뽑는다.

사용자(2026-11-14): "왜 이렇게 실수가 많아."

패턴이 있었다. 내가 **고친 자리만** 확인하고 완료라고 말했다. 방금 넣은 문이
태그 위에 앉았는지는 봤지만 나머지 엣지를 도면과 대조하지는 않았다. 그래서
사용자가 체크리스트 노릇을 했다 — 안방-욕실, 드레스룸-욕실, 펜트리-복도,
현관 밖으로 나간 문, 점선 호로 그린 문, 울퉁불퉁한 폴리곤.

이 스크립트는 **약한 근거를 눈에 보이게** 만든다. 판단은 사람이 하되,
무엇을 봐야 하는지는 기계가 빠짐없이 제시한다.

엣지마다 매기는 근거 등급
  확정   사용자가 짚었다 (via_from=user_override 또는 문 position_source=user_override)
  태그   원형 태그에서 왔다 (position_source=tag_detect)
  틈     두 방 사이 벽에 **측정된 잉크 틈**이 있다
  벽     문 레코드는 있으나 그 자리 벽에 틈이 안 보인다  ← 봐야 한다
  추론   role_rule 이 만들었다. 증거 아님                ← 봐야 한다
  미확인 개방 엣지인데 벽에 틈도 없다                     ← 봐야 한다

방마다
  문 개수 · 개방 개수 · 창 개수, 그리고 **문이 하나도 없는 방**을 표시한다.

⚠ 잉크 틈 측정의 한계(실측으로 확인): 두 방 폴리곤이 **맞닿아 있으면**(벽 두께 0)
띠가 방 안쪽 흰 공간을 재서 틈처럼 나온다. 그래서 틈 결과에 '겹침주의'를 같이 찍는다.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

import numpy as np
from PIL import Image
from shapely.geometry import Polygon

ROOT = "/home/ju/plan2graph"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
import scripts.edit_server as es   # noqa: E402

EDITS = os.path.join(ROOT, "data", "staging", "corrected", "edits")


def poly_of(r):
    p = r.get("polygon") or []
    if len(p) < 3:
        return None
    q = Polygon([(x, y) for x, y in p])
    return q.buffer(0) if not q.is_valid else q


def gap_on_wall(ink, pa, pb, scale):
    """두 방 사이 띠를 훑어 (최대 연속 틈 mm, 평균 잉크, 띠 두께 px) 를 낸다."""
    gap = pa.distance(pb)
    half = max(9.0, gap / 2.0 + 4.0)
    band = pa.buffer(half).intersection(pb.buffer(half))
    if band.is_empty:
        return None
    x0, y0, x1, y1 = band.bounds
    horiz = (x1 - x0) >= (y1 - y0)
    xi0, yi0 = max(0, int(x0)), max(0, int(y0))
    xi1, yi1 = min(ink.shape[1], int(x1) + 1), min(ink.shape[0], int(y1) + 1)
    if xi1 - xi0 < 3 or yi1 - yi0 < 3:
        return None
    sub = ink[yi0:yi1, xi0:xi1]
    p = sub.mean(axis=0) if horiz else sub.mean(axis=1)
    best = run = 0
    for v in p:
        if v < 0.35:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best * scale, float(p.mean()), gap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    a = ap.parse_args()
    g = json.load(open(os.path.join(EDITS, a.plan + ".json"), encoding="utf-8"))
    R = {str(k): v for k, v in (g.get("rooms") or {}).items()}
    scale = float(g.get("scale_mm_per_px") or 0) or None
    unit = "mm" if scale else "px"      # 축척 없는 도면도 px 로 재야 한다
    k = scale or 1.0
    DOOR_MIN = 500.0 if scale else 45.0
    polys = {k: poly_of(v) for k, v in R.items()}
    polys = {k: v for k, v in polys.items() if v is not None}

    es._build_png_index()
    b = es._png_bytes(a.plan)
    ink = ((np.asarray(Image.open(io.BytesIO(b)).convert("L")) < 128).astype(np.float32)
           if b is not None else None)

    def nm(r):
        r = str(r)
        return "%s(%s)" % ((R.get(r) or {}).get("role"), r) if r in R else "외부"

    dby = {str(d.get("id")): d for d in (g.get("doors") or [])}
    if not scale:
        print("⚠ 축척(scale_mm_per_px)이 없다 — 틈은 px 로 잰다. "
              "폭·면적도 산출 불가다.")
    print("%s — 방 %d · 문 %d · 창 %d · 엣지 %d"
          % (a.plan, len(R), len(g.get("doors") or []), len(g.get("windows") or []),
             len(g.get("edges") or [])))
    print()
    print("  %-38s %-6s %-8s %-9s %s" % ("엣지", "종류", "근거", "틈", "비고"))
    need = []
    for e in sorted((g.get("edges") or []), key=lambda e: (e.get("via") or "", str(e.get("from")))):
        fa, fb = str(e["from"]), str(e["to"])
        via = e.get("via")
        d = dby.get(str(e.get("door_id") or ""))
        src = (d or {}).get("position_source")
        if e.get("via_from") == "user_override" or src == "user_override":
            grade = "확정"
        elif src == "tag_detect":
            grade = "태그"
        elif e.get("via_from") == "role_rule":
            grade = "추론"
        else:
            grade = None
        gapmm = note = ""
        if ink is not None and fa in polys and fb in polys:
            r = gap_on_wall(ink, polys[fa], polys[fb], k)
            if r:
                gapmm = "%5.0f%s" % (r[0], unit)
                if r[2] < 1.0:
                    note = "겹침주의(벽두께 0)"
                if grade is None:
                    grade = ("틈" if r[0] >= DOOR_MIN
                             else ("벽" if via == "door" else "미확인"))
        if grade is None:
            grade = "?"
        flag = grade in ("추론", "벽", "미확인", "?")
        if flag:
            need.append((nm(fa), nm(fb), via, grade))
        print("  %-38s %-6s %-8s %-9s %s%s"
              % (nm(fa) + " - " + nm(fb), via, grade, gapmm,
                 note, "   <= 봐야 한다" if flag else ""))

    print()
    deg = {k: [0, 0, 0] for k in R}
    for e in (g.get("edges") or []):
        for k in (str(e["from"]), str(e["to"])):
            if k in deg:
                deg[k][{"door": 0, "open": 1, "window": 2}.get(e.get("via"), 1)] += 1
    for d in (g.get("doors") or []):
        if d.get("to_outside"):
            for k in (d.get("connects") or []):
                if str(k) in deg:
                    deg[str(k)][0] += 1
    print("  %-16s %s" % ("방", "문 / 개방 / 창"))
    for k in sorted(deg, key=int):
        a0, b0, c0 = deg[k]
        bad = (a0 + b0 + c0) == 0
        print("  %-16s %d / %d / %d%s" % (nm(k), a0, b0, c0,
                                          "   <= 고립(아무 연결 없음)" if bad else
                                          ("   <= 창만 있다" if (a0 + b0) == 0 else
                                           ("   <= 문이 없다" if a0 == 0 else ""))))
    print()
    print("봐야 할 엣지 %d개" % len(need))


if __name__ == "__main__":
    main()
