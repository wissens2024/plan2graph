#!/usr/bin/env python3
"""태그 원이 **우리 문·창과 맞는지** 본다 — 맞으면 빠진 개구부를 태그로 채울 수 있다.

사용자(2026-11-13): *"문의 위치와 창의 위치만 표시하고 연결해도 정말 학습 데이터로
좋을 것 같은데."* — 그래서 글자를 다 읽을 필요가 없다. **원 위치 = 개구부 위치**이고,
아래 한 줄만 문(WD/PD/SD/FSD/ABS) / 창(PW/AG) 으로 가르면 된다.

진짜 태그의 판별자: 원 안에 **가운데 가로선**이 있다(위=치수, 아래=종류).
실측에서 오탐 3개는 전부 그 선이 없었다.

읽기 전용.
"""
import json
import sys

import cv2
import numpy as np

PID = sys.argv[1] if len(sys.argv) > 1 else "APT_FP_01613b7f_17446927_u0"
SIG = PID.split("_FP_")[1].rsplit("_u", 1)[0]
img = cv2.imread("/home/ju/plan2graph/data/staging/parsed/png/%s.png" % SIG, cv2.IMREAD_GRAYSCALE)
g = json.load(open("/home/ju/plan2graph/data/staging/corrected/edits/%s.json" % PID,
                   encoding="utf-8"))
bb = g["bbox_px"]
bw = (img < 128).astype(np.uint8)


def has_midline(x, y, r):
    """원 중심 높이에 **가로선**이 지나가는가 — 진짜 태그의 판별자."""
    k = int(r * 0.78)
    band = bw[max(0, y - 2):y + 3, max(0, x - k):x + k]
    if band.size == 0:
        return 0.0
    # 가로 한 줄이 거의 전부 검은 행이 있는가
    return float(band.mean(axis=1).max())


blur = cv2.medianBlur(img, 3)
cc = cv2.HoughCircles(blur, cv2.HOUGH_GRADIENT, dp=1, minDist=16,
                      param1=100, param2=22, minRadius=14, maxRadius=32)
cand = [] if cc is None else np.round(cc[0]).astype(int).tolist()
pad = 170
cand = [c for c in cand
        if bb[0] - pad < c[0] < bb[0] + bb[2] + pad and bb[1] - pad < c[1] < bb[1] + bb[3] + pad]

tags = []
for x, y, r in cand:
    m = has_midline(x, y, r)
    k = int(r * 0.72)
    sub = bw[max(0, y - k):y + k, max(0, x - k):x + k]
    ink = float(sub.mean()) if sub.size else 1.0
    if m >= 0.80 and 0.04 <= ink <= 0.45:
        tags.append((x, y, r, m, ink))
# 가까운 중복 제거
tags.sort(key=lambda t: -t[3])
uniq = []
for t in tags:
    if all((t[0] - u[0]) ** 2 + (t[1] - u[1]) ** 2 > (t[2] * 1.2) ** 2 for u in uniq):
        uniq.append(t)
print("후보 %d → 가운데 가로선이 있는 태그 %d개" % (len(cand), len(uniq)))

ours = []
for d in (g.get("doors") or []):
    p = d.get("position")
    if p:
        ours.append(("문", d.get("id"), p, d.get("source")))
for w in (g.get("windows") or []):
    p = w.get("position")
    if p:
        ours.append(("창", w.get("id"), p, "detected"))
print("우리 개구부 %d개 (문 %d · 창 %d)"
      % (len(ours), sum(1 for o in ours if o[0] == "문"), sum(1 for o in ours if o[0] == "창")))
print()
print("%-22s %-8s %s" % ("태그 위치", "r", "가장 가까운 우리 개구부"))
used = set()
far = 0
for x, y, r, m, ink in sorted(uniq, key=lambda t: (t[1], t[0])):
    best, bd = None, 1e9
    for kind, oid, p, src in ours:
        d = ((p[0] - x) ** 2 + (p[1] - y) ** 2) ** 0.5
        if d < bd:
            best, bd = (kind, oid, src), d
    tag = ""
    if bd > 120:
        far += 1
        tag = "  ← 가까운 개구부 없음"
    else:
        used.add(best[1])
    print("(%5d,%5d)  r=%-3d  %-6s %-5s %-12s %6.0fpx%s"
          % (x, y, r, best[0] if best else "-", best[1] if best else "-",
             best[2] if best else "-", bd, tag))
print()
print("태그와 짝이 없는 우리 개구부:")
for kind, oid, p, src in ours:
    if oid not in used:
        print("   %-4s %-6s %-14s (%5.0f,%5.0f)" % (kind, oid, src, p[0], p[1]))
print()
print("요약: 태그 %d개 · 우리 개구부 %d개 · 짝 지어진 것 %d · 태그만 있는 곳 %d · 우리만 있는 곳 %d"
      % (len(uniq), len(ours), len(used), far, len(ours) - len(used)))
