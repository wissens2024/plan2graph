#!/usr/bin/env python3
"""한 도면과 **세대 영역 픽셀이 같은** 도면을 전수에서 찾는다.

dedup 그룹은 파싱된 방 분할의 서명으로 묶이므로, 같은 그림이라도 분할이 다르면
다른 그룹에 떨어진다(실측: 583b053a 와 44549e8c). 그래서 그룹을 보지 말고
**그림 자체**로 찾는다.

값싼 순서로 거른다:
  1. 방 합집합 bbox 가 --bbox-tol px 이내      (JSON 만 읽는다)
  2. 세대 영역 픽셀 비교                        (PNG 를 읽는다 — 여기부터 비싸다)
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

import numpy as np
from PIL import Image

ROOT = "/home/ju/plan2graph"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
import scripts.edit_server as es   # noqa: E402

EDITS = os.path.join(ROOT, "data", "staging", "corrected", "edits")


def bounds(g):
    xs, ys = [], []
    for r in (g.get("rooms") or {}).values():
        for x, y in (r.get("polygon") or []):
            xs.append(x); ys.append(y)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--bbox-tol", type=float, default=12.0)
    ap.add_argument("--max-diff", type=int, default=0)
    ap.add_argument("--pad", type=int, default=8)
    a = ap.parse_args()

    src = json.load(open(os.path.join(EDITS, a.source + ".json"), encoding="utf-8"))
    sb = bounds(src)
    box = (int(sb[0]) - a.pad, int(sb[1]) - a.pad, int(sb[2]) + a.pad, int(sb[3]) + a.pad)
    print("원본 %s · 세대 영역 %s" % (a.source, list(box)))

    cand = []
    for f in os.listdir(EDITS):
        if not f.endswith(".json"):
            continue
        pid = f[:-5]
        if pid == a.source:
            continue
        try:
            g = json.load(open(os.path.join(EDITS, f), encoding="utf-8"))
        except Exception:
            continue
        b = bounds(g)
        if b is None:
            continue
        if max(abs(b[i] - sb[i]) for i in range(4)) <= a.bbox_tol:
            cand.append(pid)
    print("bbox 가 %.0fpx 이내인 후보 %d장" % (a.bbox_tol, len(cand)))

    es._build_png_index()
    ga = np.asarray(Image.open(io.BytesIO(es._png_bytes(a.source))).convert("L")
                    .crop(box)).astype(np.int16)
    same = []
    for pid in sorted(cand):
        b = es._png_bytes(pid)
        if b is None:
            print("   %-34s PNG 없음" % pid); continue
        gb = np.asarray(Image.open(io.BytesIO(b)).convert("L").crop(box)).astype(np.int16)
        if gb.shape != ga.shape:
            print("   %-34s 크기 다름 %s" % (pid, gb.shape)); continue
        nz = int((np.abs(ga - gb) > 8).sum())
        mark = "같은 그림" if nz <= a.max_diff else "다른 픽셀 %d (%.3f%%)" % (
            nz, 100.0 * nz / ga.size)
        print("   %-34s %s" % (pid, mark))
        if nz <= a.max_diff:
            same.append(pid)
    print("\n세대 영역이 같은 도면 %d장" % len(same))
    for s in same:
        print("   " + s)


if __name__ == "__main__":
    main()
