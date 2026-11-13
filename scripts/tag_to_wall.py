#!/usr/bin/env python3
"""태그를 **가장 가까운 벽에 투영**해 개구부 위치를 얻는다.

사용자(2026-11-13): *"문의 위치는 다 틀렸다."*
원인: 우리 문 검출이 **태그 원**과 **위생기구**를 문으로 잡고 있었다
(검출 4개 중 진짜 문 0개).

스윙 호 검출은 욕조·변기까지 잡아 지저분했다. 그런데 도면을 보면 더 단순하다 —
**태그는 자기 개구부 바로 옆에 찍히고, 개구부는 벽 위에 있다.**
벽 선분은 이미 그래프에 있으므로 **태그 → 가장 가까운 벽 선분에 투영**하면
개구부 위치가 나온다. 호를 찾을 필요가 없다.

개구부 종류는 투영된 벽의 양쪽으로 판단한다(기존 probe 재사용):
  두 방이 나오면 문 후보 · 한 방 + 외부면 창/현관문 후보.

읽기 전용 — 위치만 계산해 비교표로 낸다.
"""
import json
import math
import sys

import cv2
import numpy as np

sys.path.insert(0, "/home/ju/plan2graph/scripts")
import importlib.util
spec = importlib.util.spec_from_file_location("rl", "/home/ju/plan2graph/scripts/rebuild_links.py")
rl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rl)

PID = sys.argv[1] if len(sys.argv) > 1 else "APT_FP_01613b7f_17446927_u0"
SIG = PID.split("_FP_")[1].rsplit("_u", 1)[0]
img = cv2.imread("/home/ju/plan2graph/data/staging/parsed/png/%s.png" % SIG, cv2.IMREAD_GRAYSCALE)
g = json.load(open("/home/ju/plan2graph/data/staging/corrected/edits/%s.json" % PID,
                   encoding="utf-8"))
bb = g["bbox_px"]
scale = float(g["scale_mm_per_px"])
bw = (img < 128).astype(np.uint8)
R = {str(k): v for k, v in g["rooms"].items()}
polys = rl.polys_of(g)


def nm(r):
    return "%s(%s)" % ((R.get(str(r)) or {}).get("role"), r) if str(r) in R else "외부"


# ── 태그 검출 (원 + 가운데 가로선) ────────────────────────────────
def has_midline(x, y, r):
    k = int(r * 0.78)
    band = bw[max(0, y - 2):y + 3, max(0, x - k):x + k]
    return float(band.mean(axis=1).max()) if band.size else 0.0


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
        tags.append((x, y, r, m))
tags.sort(key=lambda t: -t[3])
uniq = []
for t in tags:
    if all((t[0] - u[0]) ** 2 + (t[1] - u[1]) ** 2 > (t[2] * 1.2) ** 2 for u in uniq):
        uniq.append(t)
print("태그 %d개" % len(uniq))

# ── 벽 선분 (축선 제외) ──────────────────────────────────────────
walls = []
for w in (g.get("walls") or []):
    s = w.get("segment") or []
    if len(s) < 2:
        continue
    L = math.hypot(s[1][0] - s[0][0], s[1][1] - s[0][1])
    if L < 6 or L > rl.AXIS_LEN:          # 축선은 벽이 아니다
        continue
    walls.append((w.get("id"), w.get("type"), s, L))
print("벽 선분 %d개 (길이 6~%d px)" % (len(walls), int(rl.AXIS_LEN)))


def project(px, py, s):
    (ax, ay), (bx, by) = s[0], s[1]
    vx, vy = bx - ax, by - ay
    d = vx * vx + vy * vy
    if d < 1e-9:
        return (ax, ay), math.hypot(px - ax, py - ay), 0.0
    t = ((px - ax) * vx + (py - ay) * vy) / d
    t = max(0.0, min(1.0, t))
    qx, qy = ax + vx * t, ay + vy * t
    return (qx, qy), math.hypot(px - qx, py - qy), t


print()
print("%-16s %-7s %-9s %7s  %s" % ("태그", "벽", "종류", "거리", "벽 양쪽 방"))
out = []
for x, y, r, m in sorted(uniq, key=lambda t: (t[1], t[0])):
    best = None
    for wid, wt, s, L in walls:
        q, d, t = project(x, y, s)
        if best is None or d < best[1]:
            best = (q, d, wid, wt, s, t)
    if best is None:
        continue
    q, d, wid, wt, s, t = best
    t_mm = None
    for w in (g.get("walls") or []):
        if str(w.get("id")) == str(wid):
            t_mm = w.get("thickness_mm")
    t_px = (float(t_mm) / scale) if (scale and t_mm) else 7.0
    a, b = rl.probe_at(s, t_px, polys, pt=(x, y), reach=22.0)
    rooms = [z for z in (a, b) if z]
    out.append((x, y, q, d, wid, wt, rooms))
    print("(%5d,%5d)  %-7s %-9s %6.0fpx  %s" %
          (x, y, wid, wt, d, " + ".join(nm(z) for z in rooms) if rooms else "못 찾음"))

print()
print("현재 우리 문 위치와의 거리:")
for d in (g.get("doors") or []):
    p = d.get("position")
    if not p:
        continue
    bd = min((math.hypot(p[0] - o[2][0], p[1] - o[2][1]) for o in out), default=9e9)
    print("   %-5s %-26s %-13s 가장 가까운 태그-벽 투영점까지 %5.0fpx"
          % (d.get("id"), " + ".join(nm(z) for z in (d.get("connects") or [])),
             d.get("source"), bd))
