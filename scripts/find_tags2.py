#!/usr/bin/env python3
"""원형 부호(창호·문 태그)를 **오탐을 걸러서** 찾는다.

1차 결과: HoughCircles 가 진짜 태그(9x22 PD · 7.5x21 ABS · 10x22 WD · 18x25 PD ·
11x22 FSD · 15x22 PW …)를 전부 잡았지만, **해치(빗금) 영역에 오탐이 뭉쳤다**.

진짜 태그의 성질:
  · **고립돼 있다** — 반지름 2.5배 안에 다른 원이 없다(해치 오탐은 줄줄이 붙어 있다)
  · 반지름이 도면 안에서 **일정하다** — 같은 템플릿으로 찍으니까
  · 안쪽은 **흰 바탕에 글자 두 줄**(잉크 비율이 중간) — 해치는 잉크가 빽빽하다
  · 테두리 원이 **뚜렷하다** — 원주 위 픽셀이 대부분 검다

읽기 전용.
"""
import json
import os
import sys
import collections

import cv2
import numpy as np

PID = sys.argv[1] if len(sys.argv) > 1 else "APT_FP_01613b7f_17446927_u0"
SIG = PID.split("_FP_")[1].rsplit("_u", 1)[0]
img = cv2.imread("/home/ju/plan2graph/data/staging/parsed/png/%s.png" % SIG, cv2.IMREAD_GRAYSCALE)
g = json.load(open("/home/ju/plan2graph/data/staging/corrected/edits/%s.json" % PID,
                   encoding="utf-8"))
bb = g["bbox_px"]

blur = cv2.medianBlur(img, 3)
cc = cv2.HoughCircles(blur, cv2.HOUGH_GRADIENT, dp=1, minDist=16,
                      param1=100, param2=22, minRadius=8, maxRadius=30)
cand = [] if cc is None else np.round(cc[0]).astype(int).tolist()
pad = 160
cand = [c for c in cand
        if bb[0] - pad < c[0] < bb[0] + bb[2] + pad and bb[1] - pad < c[1] < bb[1] + bb[3] + pad]
print("1차 후보 %d개" % len(cand))

bw = (img < 128).astype(np.uint8)


def ring_dark(x, y, r):
    """원주 위 픽셀 중 검은 비율 — 테두리가 뚜렷한가."""
    th = np.linspace(0, 2 * np.pi, 72, endpoint=False)
    xs = np.clip((x + r * np.cos(th)).astype(int), 0, bw.shape[1] - 1)
    ys = np.clip((y + r * np.sin(th)).astype(int), 0, bw.shape[0] - 1)
    return float(bw[ys, xs].mean())


def inner_ink(x, y, r):
    """안쪽 잉크 비율 — 글자 두 줄이면 0.05~0.35 쯤."""
    k = int(r * 0.72)
    sub = bw[max(0, y - k):y + k, max(0, x - k):x + k]
    return float(sub.mean()) if sub.size else 1.0


rows = []
for x, y, r in cand:
    rows.append((x, y, r, ring_dark(x, y, r), inner_ink(x, y, r)))

# ── 고립성: 반지름 2.5배 안에 다른 후보가 있으면 해치 덩어리로 본다 ──
pts = np.array([[a, b] for a, b, _, _, _ in rows], dtype=float) if rows else np.zeros((0, 2))
keep = []
for i, (x, y, r, rd, ink) in enumerate(rows):
    d = np.hypot(pts[:, 0] - x, pts[:, 1] - y)
    near = int(((d < r * 2.5) & (d > 0.5)).sum())
    keep.append((near, x, y, r, rd, ink))

rad = collections.Counter(r for _, _, _, r, _, _ in keep)
mode_r = rad.most_common(1)[0][0] if rad else 0
print("반지름 분포(상위5):", rad.most_common(5), "→ 최빈 %d" % mode_r)

tags = [(x, y, r, rd, ink) for near, x, y, r, rd, ink in keep
        if near == 0 and rd >= 0.55 and 0.03 <= ink <= 0.40 and abs(r - mode_r) <= 5]
print("걸러낸 태그 %d개" % len(tags))
for x, y, r, rd, ink in sorted(tags, key=lambda t: (t[1], t[0])):
    print("   (%5d,%5d) r=%2d 테두리 %.2f 내부잉크 %.2f" % (x, y, r, rd, ink))

vis = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
for near, x, y, r, rd, ink in keep:
    cv2.circle(vis, (x, y), r, (190, 190, 190), 1)
for x, y, r, rd, ink in tags:
    cv2.circle(vis, (x, y), r + 3, (0, 0, 255), 2)
x0, y0 = max(0, int(bb[0]) - 170), max(0, int(bb[1]) - 170)
x1, y1 = min(vis.shape[1], int(bb[0] + bb[2]) + 170), min(vis.shape[0], int(bb[1] + bb[3]) + 170)
cv2.imwrite("/tmp/TAGS2.png", vis[y0:y1, x0:x1])

os.makedirs("/tmp/tagcrops", exist_ok=True)
for f in os.listdir("/tmp/tagcrops"):
    os.remove(os.path.join("/tmp/tagcrops", f))
for i, (x, y, r, rd, ink) in enumerate(sorted(tags, key=lambda t: (t[1], t[0]))):
    k = int(r * 0.95)
    crop = img[max(0, y - k):y + k, max(0, x - k):x + k]
    if crop.size:
        cv2.imwrite("/tmp/tagcrops/%02d.png" % i,
                    cv2.resize(crop, None, fx=6, fy=6, interpolation=cv2.INTER_CUBIC))
print("조각 %d개 → /tmp/tagcrops/ · 그림 /tmp/TAGS2.png" % len(tags))
