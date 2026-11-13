#!/usr/bin/env python3
"""detect_openings_ink — 도면 잉크에서 개구부를 직접 뽑는다.

사용자(2026-11-13): "APT_FP_091800b0_17446941_u1 여긴 기하를 다시 생성해줘."

왜 필요한가. 그 도면은 AI-Hub 원천에 **SPA(방 폴리곤) 카테고리밖에 없다** —
STR/OBJ/OCR 이 통째로 없어서 문과 창이 들어올 데가 없었다. 결과: 문 1개, 창 0개,
엣지 18개가 거의 전부 '개방'. 같은 처지(문 1개 이하)가 전수에서 2,166장이다.

근거는 하나다. **벽은 잉크고 개구부는 잉크가 끊긴 자리다.**
두 방 사이의 벽 띠를 긴 방향으로 훑으며 어두움을 재면, 벽은 짙고 문·창은 옅다.

문과 창을 가르는 것
  · 문  — 틈이 거의 비어 있다(문턱선 한 줄). 띠 안 평균 어두움이 아주 낮다.
  · 창  — 유리·창틀을 **평행선 여러 줄**로 그린다. 틈인데도 어두움이 중간이다.
  이 기준은 이 도면에서 눈으로 맞춰 보고 정했다. 맞지 않는 도면이 있을 테니
  전수로 돌리기 전에 반드시 표본을 눈으로 봐라.

지어내지 않는다
  · 폭이 표준 범위(600~2600mm) 밖이면 버린다.
  · 이미 그 두 방을 잇는 문/창이 있으면 건드리지 않는다.
  · 모르는 값(폭의 출처 등)은 None 으로 둔다.

읽기 전용(기본). --apply 를 줘야 쓴다.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

import math

import numpy as np
from shapely.geometry import Polygon, box

ROOT = "/home/ju/plan2graph"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
import scripts.edit_server as es            # noqa: E402
try:
    from scripts import role_rules           # noqa: E402
except Exception:
    role_rules = None

EDITS = os.path.join(ROOT, "data", "staging", "corrected", "edits")

ADJ_PX = 45.0          # 이보다 멀면 맞닿은 것으로 보지 않는다
HALF_MIN = 12.0        # 벽 띠 반두께 하한 (한국 내벽 200mm ~= 19px @10.5mm/px)
DARK = 128             # 이보다 어두우면 잉크
W_MIN_MM, W_MAX_MM = 600.0, 2600.0
DOOR_MAX = 0.12        # 띠 안 어두움이 이 아래면 문(거의 비어 있다)
WIN_MAX = 0.55         # 이 아래면 창(평행선이 있다), 넘으면 벽


# ---------------------------------------------------------------- 여닫이 호
ARC_OK = 0.45          # 호 위 점의 이 비율 이상이 잉크면 문으로 인정
ARC_N = 48             # 호를 이 개수로 샘플


def _ink_at(ink, x, y):
    H, W = ink.shape
    xi, yi = int(round(x)), int(round(y))
    if 0 <= xi < W and 0 <= yi < H:
        return ink[yi, xi] > 0
    return False


def arc_score(ink, hinge, along, into, r):
    hit = 0
    for k in range(ARC_N + 1):
        th = (math.pi / 2.0) * k / ARC_N
        ux = along[0] * math.cos(th) + into[0] * math.sin(th)
        uy = along[1] * math.cos(th) + into[1] * math.sin(th)
        x, y = hinge[0] + ux * r, hinge[1] + uy * r
        if (_ink_at(ink, x, y) or _ink_at(ink, x + 1, y) or _ink_at(ink, x, y + 1)
                or _ink_at(ink, x - 1, y) or _ink_at(ink, x, y - 1)):
            hit += 1
    return hit / float(ARC_N + 1)


def confirm_swing(ink, pos, width, horiz, ca, cb):
    """여닫이 호를 찾는다. 경첩 위치와 반지름을 같이 훑는다.

    돌려주는 값: (최고점수, 경첩, 열린 방향)
    """
    axis = (1.0, 0.0) if horiz else (0.0, 1.0)
    half = width / 2.0
    ins = []
    for c in (ca, cb):
        if horiz:
            ins.append((0.0, 1.0 if c[1] > pos[1] else -1.0))
        else:
            ins.append((1.0 if c[0] > pos[0] else -1.0, 0.0))
    seen = set()
    ins = [v for v in ins if not (v in seen or seen.add(v))]

    best = (0.0, None, None)
    for sgn in (-1.0, 1.0):                       # 어느 끝이 경첩인가
        along = (-axis[0] * sgn, -axis[1] * sgn)  # 문짝은 개구부 안쪽으로 눕는다
        for off in range(-12, 13, 3):             # 경첩을 벽을 따라 민다
            hx = pos[0] + axis[0] * (sgn * half + off)
            hy = pos[1] + axis[1] * (sgn * half + off)
            for mul in (0.80, 0.90, 1.00, 1.10, 1.25):
                r = width * mul
                if r < 10:
                    continue
                for into in ins:
                    s = arc_score(ink, (hx, hy), along, into, r)
                    if s > best[0]:
                        best = (s, (round(hx, 1), round(hy, 1)), into)
    return best


def poly_of(r):
    p = r.get("polygon") or []
    if len(p) < 3:
        return None
    q = Polygon([(x, y) for x, y in p])
    if not q.is_valid:
        q = q.buffer(0)
    return q if (not q.is_empty and q.geom_type == "Polygon") else None


def runs_below(prof, thr, min_len):
    """prof 에서 thr 미만이 min_len 이상 이어지는 구간 [(i0,i1)]."""
    out, s = [], None
    for i, v in enumerate(prof):
        if v < thr:
            if s is None:
                s = i
        else:
            if s is not None and i - s >= min_len:
                out.append((s, i))
            s = None
    if s is not None and len(prof) - s >= min_len:
        out.append((s, len(prof)))
    return out


def scan_band(ink, b, horiz):
    """띠 b(box) 를 긴 방향으로 훑어 1px 간격 어두움 프로파일을 낸다."""
    x0, y0, x1, y1 = (int(round(v)) for v in b.bounds)
    H, W = ink.shape
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(W, x1), min(H, y1)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None, None
    sub = ink[y0:y1, x0:x1]
    if horiz:                      # 가로 벽 — x 를 따라 훑고 y 로 평균
        return sub.mean(axis=0), (x0, y0, "x")
    return sub.mean(axis=1), (x0, y0, "y")


def find_openings(g, ink, verbose=False):
    rooms = {str(k): v for k, v in (g.get("rooms") or {}).items()}
    polys = {}
    for rid, r in rooms.items():
        q = poly_of(r)
        if q is not None:
            polys[rid] = q
    scale = float(g.get("scale_mm_per_px") or 0) or None
    if not scale:
        return [], "축척 없음"
    wmin, wmax = W_MIN_MM / scale, W_MAX_MM / scale

    have = set()
    for d in (g.get("doors") or []):
        c = [str(x) for x in (d.get("connects") or [])]
        if len(c) == 2:
            have.add(frozenset(c))
    for w in (g.get("windows") or []):
        pass

    ids = sorted(polys, key=int)
    found = []
    for i, a in enumerate(ids):
        for b2 in ids[i + 1:]:
            pa, pb = polys[a], polys[b2]
            gap = pa.distance(pb)
            if gap > ADJ_PX:
                continue
            if frozenset((a, b2)) in have:
                continue
            half = max(HALF_MIN, gap / 2.0 + 4.0)
            band = pa.buffer(half).intersection(pb.buffer(half))
            if band.is_empty or band.area < 20:
                continue
            bb = band.bounds
            bw, bh = bb[2] - bb[0], bb[3] - bb[1]
            if max(bw, bh) < wmin:
                continue
            horiz = bw >= bh
            prof, origin = scan_band(ink, box(*bb), horiz)
            if prof is None:
                continue
            for s, e in runs_below(prof, WIN_MAX, int(wmin)):
                L = e - s
                if L > wmax:
                    continue
                seg = prof[s:e]
                m = float(seg.mean())
                c = (s + e) / 2.0
                if horiz:
                    pos = [origin[0] + c, (bb[1] + bb[3]) / 2.0]
                else:
                    pos = [(bb[0] + bb[2]) / 2.0, origin[1] + c]
                ca = (pa.centroid.x, pa.centroid.y)
                cb = (pb.centroid.x, pb.centroid.y)
                arc, hinge, into = confirm_swing(ink, pos, L, horiz, ca, cb)
                ra = (rooms.get(a) or {}).get("role")
                rb = (rooms.get(b2) or {}).get("role")
                if arc >= ARC_OK:
                    kind = "door"
                elif m < DOOR_MAX:
                    kind = "door?"        # 틈은 비었는데 호가 없다 — 미닫이이거나 가짜
                else:
                    kind = "window"
                if role_rules is not None and kind.startswith("door") \
                        and role_rules.door_forbidden(ra, rb):
                    kind = "금지쌍"
                found.append({"rooms": [a, b2], "kind": kind,
                              "pos": [round(pos[0], 1), round(pos[1], 1)],
                              "width_px": round(float(L), 1),
                              "width_m": round(L * scale / 1000.0, 2),
                              "ink": round(m, 3), "arc": round(arc, 2),
                              "horiz": horiz})
    return found, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", nargs="+")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    es._build_png_index()
    from PIL import Image
    for pid in a.plans:
        p = os.path.join(EDITS, pid + ".json")
        g = json.load(open(p, encoding="utf-8"))
        raw = es._png_bytes(pid)
        if raw is None:
            print("%s — 원본 PNG 없음" % pid)
            continue
        im = Image.open(io.BytesIO(raw)).convert("L")
        ink = (np.asarray(im) < DARK).astype(np.float32)
        found, err = find_openings(g, ink)
        rooms = {str(k): v for k, v in (g.get("rooms") or {}).items()}

        def nm(r):
            return "%s(%s)" % ((rooms.get(r) or {}).get("role"), r)
        if err:
            print("%s — %s" % (pid, err))
            continue
        nd = sum(1 for f in found if f["kind"] == "door")
        print("%s — 찾은 개구부 %d (문 %d · 창 %d) · 기존 문 %d 창 %d"
              % (pid, len(found), nd, len(found) - nd,
                 len(g.get("doors") or []), len(g.get("windows") or [])))
        print("   %-28s %-6s %-16s %6s %6s %5s"
              % ("잇는 방", "종류", "위치", "폭m", "잉크", "호"))
        for f in sorted(found, key=lambda v: (v["pos"][1], v["pos"][0])):
            print("   %-28s %-6s (%6.0f,%6.0f) %6.2f %6.3f %5.2f"
                  % (" + ".join(nm(r) for r in f["rooms"]), f["kind"],
                     f["pos"][0], f["pos"][1], f["width_m"], f["ink"], f["arc"]))
        if a.apply:
            json.dump(found, open("/tmp/openings_%s.json" % pid, "w"), ensure_ascii=False)
            print("   → /tmp/openings_%s.json" % pid)


if __name__ == "__main__":
    main()
