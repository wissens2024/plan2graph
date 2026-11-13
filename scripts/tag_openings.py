#!/usr/bin/env python3
"""tag_openings — **도면의 원형 태그에서 개구부 위치를 복원한다.**

사용자(2026-11-13): *"문의 위치는 다 틀렸다."* 실측으로 원인을 확인했다 —
우리 문 검출이 **태그 원**과 **위생기구**를 문으로 잡고 있었다(검출 4개 중 진짜 0개).
  d5 → 11x22 FSD 태그에서 10px · d4 → 9x22 WD 태그에서 15px
  d7, d1 → 욕실 위생기구 자리

도면에는 개구부마다 **원형 태그**가 찍혀 있다(위=폭x높이 100mm 단위, 아래=종류):
  9x22 WD · 10x22 WD · 7.5x21 ABS · 11x22 FSD · 18x25 PD · 15x22 PW · 9x22 SD
AI-Hub OCR 라벨에는 이 부호가 **0건**이라(고유 텍스트 483개 전부 한글 방 이름)
지금까지 쓰지 못했다.

방법
  1) 태그 검출 — 원 + **가운데 가로선**(위/아래 두 줄을 가르는 선). 오탐이 전부 걸린다.
  2) 가까운 벽에 투영 — 단, **실제로 틈이 난 벽**을 우선한다. 가장 가까운 벽이
     항상 그 개구부의 벽은 아니다(실측: 10x22 WD 가 안방↔파우더룸 벽에 붙었는데
     실제 문은 거실↔안방이었다).
  3) 벽 양쪽을 찔러 잇는 방을 정한다(rebuild_links 의 probe 재사용).
     한쪽만 나오면 **외부로 나가는 개구부**다(현관문·외벽 창).

출처를 반드시 남긴다 — `source="tag_detect"`. 검출/태그/사람확정을 갈라 셀 수 있어야
논문에 "무엇을 어떻게 보정했는가"를 쓸 수 있다.

읽기 전용(기본). --apply 를 줘야 쓴다.
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import shutil
import sys

import cv2
import numpy as np

ROOT = "/home/ju/plan2graph"
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import importlib.util
_spec = importlib.util.spec_from_file_location("rl", os.path.join(ROOT, "scripts",
                                                                  "rebuild_links.py"))
rl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rl)

EDITS = os.path.join(ROOT, "data", "staging", "corrected", "edits")
PNGD = os.path.join(ROOT, "data", "staging", "parsed", "png")
BK = os.path.join(ROOT, "data", "staging", "corrected", "_manual_backup")


def detect_tags(img, bb):
    """원 + 가운데 가로선 = 태그. [(x,y,r)]"""
    bw = (img < 128).astype(np.uint8)

    def midline(x, y, r):
        k = int(r * 0.78)
        band = bw[max(0, y - 2):y + 3, max(0, x - k):x + k]
        return float(band.mean(axis=1).max()) if band.size else 0.0

    blur = cv2.medianBlur(img, 3)
    cc = cv2.HoughCircles(blur, cv2.HOUGH_GRADIENT, dp=1, minDist=16,
                          param1=100, param2=22, minRadius=14, maxRadius=32)
    cand = [] if cc is None else np.round(cc[0]).astype(int).tolist()
    pad = 170
    cand = [c for c in cand
            if bb[0] - pad < c[0] < bb[0] + bb[2] + pad
            and bb[1] - pad < c[1] < bb[1] + bb[3] + pad]
    scored = []
    for x, y, r in cand:
        m = midline(x, y, r)
        k = int(r * 0.72)
        sub = bw[max(0, y - k):y + k, max(0, x - k):x + k]
        ink = float(sub.mean()) if sub.size else 1.0
        if m >= 0.80 and 0.04 <= ink <= 0.45:
            scored.append((x, y, r, m))
    scored.sort(key=lambda t: -t[3])
    out = []
    for t in scored:
        if all((t[0] - u[0]) ** 2 + (t[1] - u[1]) ** 2 > (t[2] * 1.2) ** 2 for u in out):
            out.append(t)
    return [(x, y, r) for x, y, r, _ in out]


def project(px, py, s):
    (ax, ay), (bx, by) = s[0], s[1]
    vx, vy = bx - ax, by - ay
    d = vx * vx + vy * vy
    if d < 1e-9:
        return (ax, ay), math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / d))
    qx, qy = ax + vx * t, ay + vy * t
    return (qx, qy), math.hypot(px - qx, py - qy)


def gap_score(bw, s, q, t_px, span):
    """투영점 둘레 span 구간의 **벽 잉크가 옅은가** — 틈(개구부)이면 옅다.
    벽 전체 평균 잉크와 비교해 (평균 − 국소)/평균 을 돌려준다."""
    (ax, ay), (bx, by) = s[0], s[1]
    L = math.hypot(bx - ax, by - ay)
    if L < 1:
        return 0.0
    ux, uy = (bx - ax) / L, (by - ay) / L
    nx, ny = -uy, ux
    half = max(2.0, t_px / 2.0 + 1.0)

    def band(c0, c1, step=2.0):
        n = max(3, int(abs(c1 - c0) / step))
        vals = []
        for i in range(n):
            tt = c0 + (c1 - c0) * i / (n - 1)
            cx, cy = ax + ux * tt, ay + uy * tt
            for o in np.linspace(-half, half, 5):
                X = int(round(cx + nx * o))
                Y = int(round(cy + ny * o))
                if 0 <= Y < bw.shape[0] and 0 <= X < bw.shape[1]:
                    vals.append(bw[Y, X])
        return float(np.mean(vals)) if vals else 0.0

    tq = ((q[0] - ax) * ux + (q[1] - ay) * uy)
    whole = band(0.0, L)
    local = band(max(0.0, tq - span / 2), min(L, tq + span / 2))
    if whole <= 1e-6:
        return 0.0
    return max(0.0, (whole - local) / whole)


def run(pid, apply_=False, verbose=True):
    p = os.path.join(EDITS, pid + ".json")
    sig = pid.split("_FP_")[1].rsplit("_u", 1)[0]
    png = os.path.join(PNGD, sig + ".png")
    if not (os.path.exists(p) and os.path.exists(png)):
        return None
    g = json.load(open(p, encoding="utf-8"))
    img = cv2.imread(png, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    bw = (img < 128).astype(np.uint8)
    bb = g["bbox_px"]
    scale = float(g.get("scale_mm_per_px") or 0) or None
    if not scale:
        return None
    polys = rl.polys_of(g)
    R = {str(k): v for k, v in (g.get("rooms") or {}).items()}

    def nm(r):
        return "%s(%s)" % ((R.get(str(r)) or {}).get("role"), r) if str(r) in R else "외부"

    tags = detect_tags(img, bb)
    walls = []
    for w in (g.get("walls") or []):
        s = w.get("segment") or []
        if len(s) < 2:
            continue
        L = math.hypot(s[1][0] - s[0][0], s[1][1] - s[0][1])
        if L < 6 or L > rl.AXIS_LEN:
            continue
        t_mm = w.get("thickness_mm")
        walls.append((str(w.get("id")), w.get("type"), s, L,
                      (float(t_mm) / scale) if t_mm else 7.0))

    span = 900.0 / scale          # 표준 문폭 900mm 만큼 보고 틈을 잰다
    found = []
    for (x, y, r) in tags:
        # ① 가까운 벽부터 후보로 모은다 — **거리가 1차**다.
        #    (틈 점수를 1차로 쓰면 엉뚱한 벽으로 끌려간다. 실측에서 침실3 문이
        #     복도↔침실2 벽으로 갔다.)
        cands = []
        for wid, wt, s_, L, t_px in walls:
            q, d = project(x, y, s_)
            if d <= 90:
                cands.append((d, q, wid, wt, s_, t_px))
        if not cands:
            continue
        cands.sort(key=lambda c: c[0])
        dmin = cands[0][0]
        # ② 비슷한 거리(1.5배 + 10px 이내)끼리만 **틈**으로 가른다
        # **거리만 쓴다.** 틈 점수를 가중치로 넣으면 계속 엉뚱한 벽으로 끌렸다
        # (침실3 태그 → 침실2↔침실3 벽, 그 벽에도 틈이 있어서).
        # 틈은 5px 이내 동률일 때만 가른다.
        near = [c for c in cands if c[0] <= dmin + 5]
        best = None
        for d, q, wid, wt, s_, t_px in near:
            gs = gap_score(bw, s_, q, t_px, span)
            a2, b2 = rl.probe_at(s_, t_px, polys, pt=(x, y), reach=22.0)
            rooms = [z for z in dict.fromkeys([a2, b2]) if z]
            sc = (0.4 if len(rooms) == 2 else 0.0) + gs * 0.2 - d / 50.0
            if best is None or sc > best[0]:
                best = (sc, q, d, wid, wt, rooms, gs)
        sc, q, d, wid, wt, rooms, gs = best
        found.append({"pos": [round(q[0], 1), round(q[1], 1)], "tag": [x, y, r],
                      "wall": wid, "wall_type": wt, "dist": round(d, 1),
                      "gap": round(gs, 3), "rooms": rooms})

    if verbose:
        print("%s — 태그 %d개 · 벽에 붙은 개구부 %d개" % (pid, len(tags), len(found)))
        print("   %-16s %-7s %7s %6s  %s" % ("위치", "벽", "태그거리", "틈", "잇는 방"))
        for f in sorted(found, key=lambda v: (v["pos"][1], v["pos"][0])):
            print("   (%5.0f,%5.0f)  %-7s %6.0fpx %6.2f  %s"
                  % (f["pos"][0], f["pos"][1], f["wall"], f["dist"], f["gap"],
                     " + ".join(nm(z) for z in f["rooms"]) if f["rooms"] else "못 찾음 (외부?)"))

    if apply_:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        bk = os.path.join(BK, stamp + "_tags")
        os.makedirs(bk, exist_ok=True)
        shutil.copy(p, os.path.join(bk, pid + ".json"))
        g["tag_openings"] = found          # 원자료를 남긴다(되돌릴 수 있게)
        g.setdefault("provenance", {})["tags"] = "tag_detect"
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(g, f, ensure_ascii=False)
        os.replace(tmp, p)
        if verbose:
            print("   → tag_openings %d개 기록 · 백업 %s" % (len(found), bk))
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", nargs="*", default=[])
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    for pid in a.plans:
        run(pid, apply_=a.apply)


if __name__ == "__main__":
    main()
