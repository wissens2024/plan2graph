#!/usr/bin/env python3
"""문을 **벽의 틈 + 스윙 호**로 찾는다 — 태그를 단서로.

사용자(2026-11-13): *"문의 위치는 다 틀렸다."* 맞다. 실측으로 원인을 봤더니
우리 문 검출이 **문이 아닌 것**을 잡고 있었다:
  d5 → 11x22 FSD 태그 원에서 10px   (태그 원을 문으로)
  d4 →  9x22 WD  태그 원에서 15px   (태그 원을 문으로)
  d7, d1 → 욕실 위생기구 자리        (기구를 문으로)
검출된 문 4개 중 진짜 문이 하나도 없었다.

진짜 문 기호 = **벽이 끊긴 자리 + 사분원 호**(여닫이) 또는 **평행선**(미닫이).
태그가 강한 단서다 — 문 태그(WD·PD·SD·FSD·ABS) 하나당 문 하나이고 그 근처에 찍힌다.

이 스크립트는 **스윙 호를 찾을 수 있는가**만 본다.
방법: 태그 주변을 잘라 ①원 성분 제거 ②남은 곡선 중 사분원에 가까운 것을 찾는다.
HoughCircles 로 **큰 반지름**(문 폭 = 호의 반지름)을 찾되, 호는 원의 1/4뿐이므로
누적 임계를 낮추고 **중심이 벽 위에 있는지**로 거른다.

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
scale = float(g["scale_mm_per_px"])
bw = (img < 128).astype(np.uint8)

# 문 폭 600~1300mm → 호 반지름(px)
r_lo, r_hi = int(600.0 / scale), int(1400.0 / scale)
print("축척 %.2f mm/px → 스윙 호 반지름 %d~%d px" % (scale, r_lo, r_hi))

pad = 60
x0, y0 = max(0, int(bb[0]) - pad), max(0, int(bb[1]) - pad)
x1, y1 = min(img.shape[1], int(bb[0] + bb[2]) + pad), min(img.shape[0], int(bb[1] + bb[3]) + pad)
sub = img[y0:y1, x0:x1]

blur = cv2.medianBlur(sub, 3)
cc = cv2.HoughCircles(blur, cv2.HOUGH_GRADIENT, dp=1, minDist=max(8, r_lo // 2),
                      param1=100, param2=30, minRadius=r_lo, maxRadius=r_hi)
arcs = [] if cc is None else np.round(cc[0]).astype(int).tolist()
print("호 후보 %d개 (반지름 %d~%d)" % (len(arcs), r_lo, r_hi))

sbw = (sub < 128).astype(np.uint8)


def arc_cover(x, y, r):
    """원주를 4분면으로 나눠 **한 분면만 검다**면 스윙 호다."""
    qs = []
    for q in range(4):
        th = np.linspace(q * np.pi / 2, (q + 1) * np.pi / 2, 40)
        xs = np.clip((x + r * np.cos(th)).astype(int), 0, sbw.shape[1] - 1)
        ys = np.clip((y + r * np.sin(th)).astype(int), 0, sbw.shape[0] - 1)
        qs.append(float(sbw[ys, xs].mean()))
    qs_sorted = sorted(qs, reverse=True)
    return qs_sorted[0], qs_sorted[1], qs.index(qs_sorted[0])


good = []
for x, y, r in arcs:
    top, second, qi = arc_cover(x, y, r)
    # 한 분면만 진하고 나머지는 옅다 = 사분원 호
    if top >= 0.55 and second <= 0.45:
        good.append((x + x0, y + y0, r, top, second, qi))
print("사분원 호로 보이는 것 %d개" % len(good))
for x, y, r, t, s2, qi in sorted(good, key=lambda v: (v[1], v[0])):
    print("   중심(%5d,%5d) r=%3d (%4.0fmm) 최대분면 %.2f 두번째 %.2f q%d"
          % (x, y, r, r * scale, t, s2, qi))

vis = cv2.cvtColor(sub, cv2.COLOR_GRAY2BGR)
for x, y, r, t, s2, qi in good:
    cv2.circle(vis, (x - x0, y - y0), r, (0, 0, 255), 2)
    cv2.circle(vis, (x - x0, y - y0), 4, (0, 140, 255), -1)
for d in (g.get("doors") or []):
    p = d.get("position")
    if p:
        cv2.rectangle(vis, (int(p[0]) - x0 - 9, int(p[1]) - y0 - 9),
                      (int(p[0]) - x0 + 9, int(p[1]) - y0 + 9), (0, 170, 0), 2)
cv2.imwrite("/tmp/ARCS.png", vis)
print("그림 /tmp/ARCS.png (빨강=찾은 호 · 초록=지금 문 위치)")
