#!/usr/bin/env python3
"""도면의 **원형 부호(창호·문 일람 태그)**를 찾는다 — 타당성 확인.

사용자 지시(2026-11-13): *"정확한 문의 위치와 사이즈, 창의 위치와 사이즈를 기록해서
연결 하자."*

이 도면들의 문·창 치수는 **원 안에 인쇄된 부호**에 들어 있다:
    9x22 WD · 10x22 WD · 7.5x21 ABS · 11x22 FSD · 18x25 PD · 15x22 PW
앞 숫자쌍 = 폭 × 높이 (100mm 단위), 아래 글자 = 종류.
AI-Hub OCR 라벨에는 이 부호가 **0건**(고유 텍스트 483개 전부 한글 방 이름)이라
우리가 직접 읽어야 한다.

이 스크립트는 1단계 — **원을 찾을 수 있는가**만 본다. 글자 인식은 그다음이다.
읽기 전용. 결과를 PNG 로 그려 눈으로 확인한다.
"""
import json
import os
import sys

import cv2
import numpy as np

PID = sys.argv[1] if len(sys.argv) > 1 else "APT_FP_01613b7f_17446927_u0"
SIG = PID.split("_FP_")[1].rsplit("_u", 1)[0]
PNG = "/home/ju/plan2graph/data/staging/parsed/png/%s.png" % SIG
G = "/home/ju/plan2graph/data/staging/corrected/edits/%s.json" % PID

img = cv2.imread(PNG, cv2.IMREAD_GRAYSCALE)
print("이미지 %s" % (img.shape,))
g = json.load(open(G, encoding="utf-8"))
bb = g["bbox_px"]
scale = float(g["scale_mm_per_px"])

# 태그 원의 실제 지름은 보통 7~9mm 정도로 인쇄된다 → 픽셀 반지름 추정
r_mm = (3.0, 7.0)
r_px = (max(4, int(r_mm[0] / scale * 2)), int(r_mm[1] / scale * 2) + 6)
print("축척 %.2f mm/px → 원 반지름 탐색 %d~%d px" % (scale, r_px[0], r_px[1]))

blur = cv2.medianBlur(img, 3)
circles = cv2.HoughCircles(blur, cv2.HOUGH_GRADIENT, dp=1, minDist=r_px[0] * 2,
                           param1=120, param2=18,
                           minRadius=r_px[0], maxRadius=r_px[1])
found = [] if circles is None else np.round(circles[0]).astype(int).tolist()
print("원 후보 %d개" % len(found))

# 도면 영역 안쪽만
pad = 120
inside = [c for c in found
          if bb[0] - pad < c[0] < bb[0] + bb[2] + pad and bb[1] - pad < c[1] < bb[1] + bb[3] + pad]
print("도면 영역 안 %d개" % len(inside))

vis = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
for x, y, r in inside:
    cv2.circle(vis, (x, y), r, (0, 0, 255), 2)
    cv2.circle(vis, (x, y), 2, (0, 165, 255), 3)
# 우리가 가진 문·창 위치도 같이
for d in (g.get("doors") or []):
    p = d.get("position")
    if p:
        cv2.rectangle(vis, (int(p[0]) - 9, int(p[1]) - 9), (int(p[0]) + 9, int(p[1]) + 9),
                      (0, 170, 0), 2)
for w in (g.get("windows") or []):
    p = w.get("position")
    if p:
        cv2.rectangle(vis, (int(p[0]) - 9, int(p[1]) - 9), (int(p[0]) + 9, int(p[1]) + 9),
                      (200, 0, 160), 2)
x0, y0 = max(0, int(bb[0]) - 150), max(0, int(bb[1]) - 150)
x1, y1 = min(vis.shape[1], int(bb[0] + bb[2]) + 150), min(vis.shape[0], int(bb[1] + bb[3]) + 150)
cv2.imwrite("/tmp/TAGS.png", vis[y0:y1, x0:x1])
print("저장 /tmp/TAGS.png  (빨강=찾은 원 · 초록=우리 문 · 자주=우리 창)")

# 원 안의 글자 조각을 잘라 둔다(2단계 준비)
os.makedirs("/tmp/tagcrops", exist_ok=True)
for i, (x, y, r) in enumerate(inside[:40]):
    k = int(r * 1.25)
    crop = img[max(0, y - k):y + k, max(0, x - k):x + k]
    if crop.size:
        cv2.imwrite("/tmp/tagcrops/%02d_%d_%d.png" % (i, x, y),
                    cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC))
print("원 내부 조각 %d개 → /tmp/tagcrops/" % min(len(inside), 40))
