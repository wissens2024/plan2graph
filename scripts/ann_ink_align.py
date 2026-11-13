#!/usr/bin/env python3
"""ann_ink_align — 주석이 자기 도면 위에 얹혀 있는가: 방 경계가 벽 잉크 위에 있나.

왜 필요한가: 주석↔도면 오매칭(DATA.md §8.14) 검출의 **마지막 구멍**을 닫는다.
  · 크기 검사  — 두 도면 크기가 같으면 못 잡는다
  · 픽셀 대조  — 사본이 없으면 비교 대상이 없다(평면의 30.5%가 단독)
이 검사는 **도면 한 장만으로** 판정한다. 사본도 크기도 필요 없다.

방법: 방 폴리곤 **합집합의 바깥 윤곽**을 일정 간격으로 샘플링해, 각 점 주변 r px 안에
어두운 픽셀(벽 잉크)이 있는지 센다. 세대 외곽선은 거의 항상 실제 벽이므로, 주석이
자기 도면 것이면 이 비율이 높고 남의 도면 것이면 낮다.

내부 경계는 쓰지 않는다 — 열린 LDK처럼 **벽이 없는 경계가 정상**인 곳이 많아서
(CORRECTION_GUIDE §3-A) 거짓 경보가 난다.

⚠⚠ **현재 상태: 보정(calibration)은 됐지만 선별력이 모자란다 [2026-11-13 실측]**
  · 알려진 오염 4장 = 0.308 / 정상 4장 = 0.752~0.936 → **아는 사례는 깨끗이 갈린다.**
  · 그러나 단독 평면 300장 표본에서 <0.5 가 6장(2.0%) 나왔는데, 그중 최하위 2장을
    눈으로 확인하니 **둘 다 오염이 아니었다**:
      - `APT_FP_72f65280_17446941_u3` (0.470) — 폴리곤이 벽에 정확히 맞는다. 점수가 낮은 건
        세대 외곽선이 발코니 유리선·개방 LDK 모서리를 길게 지나기 때문.
      - `APT_FP_62e2af00_17434949_u0` (0.221) — **렌더 홍보 평면도**(선도면이 아님)라
        잉크 임계값이 통하지 않고, 주석도 방 3개뿐이다.
  → **이 점수로 오염률을 추정하면 안 된다.** 필요한 보강:
      ① 도면별 잉크 밀도로 정규화(렌더 도면 보정)  ② 외곽선 대신 **사적 방 사이 벽**만 샘플링
      ③ 또는 우리 V2V 검출과 주석을 대조(독립 관측 비교) ← 가장 강함
  구멍(사본 없음 + 크기 같음)은 **아직 열려 있고 크기도 모른다.**

usage:
  python scripts/ann_ink_align.py --plans A B C      # 지정 평면
  python scripts/ann_ink_align.py --sample 300       # 무작위 표본
  python scripts/ann_ink_align.py --singletons 400   # 사본 없는 평면만(구멍 측정)
읽기 전용.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

import numpy as np
from PIL import Image
from shapely.geometry import Polygon
from shapely.ops import unary_union

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ST = os.path.join(ROOT, "data", "staging")
PNG = os.path.join(ST, "parsed", "png")
GRAPHS = os.path.join(ST, "parsed", "graphs")

DARK = 200        # 이보다 어두우면 잉크
RADIUS = 4        # 샘플점 주변 탐색 반경(px)
STEP = 12         # 윤곽 샘플 간격(px)

_img = {}


def sheet_of(pid):
    return pid.split("_", 2)[2].rsplit("_u", 1)[0]


def ink_mask(sheet):
    if sheet not in _img:
        p = os.path.join(PNG, sheet + ".png")
        if not os.path.exists(p):
            _img[sheet] = None
        else:
            a = np.asarray(Image.open(p).convert("L"))
            _img[sheet] = a < DARK
        if len(_img) > 6:                     # 시트 이미지는 크다 — 캐시를 작게 유지
            for k in list(_img)[:-3]:
                _img.pop(k, None)
    return _img[sheet]


def align_score(pid):
    """(정합률, 샘플수) — 샘플이 모자라면 (None, n)."""
    gp = os.path.join(GRAPHS, pid + ".json")
    if not os.path.exists(gp):
        return None, 0
    g = json.load(open(gp, encoding="utf-8"))
    polys = []
    for r in (g.get("rooms") or {}).values():
        pts = r.get("polygon") or []
        if len(pts) >= 3:
            q = Polygon([(p[0], p[1]) for p in pts])
            if q.is_valid and q.area > 1:
                polys.append(q)
    if not polys:
        return None, 0
    u = unary_union(polys)
    if u.geom_type == "MultiPolygon":
        u = max(u.geoms, key=lambda p: p.area)
    ring = u.exterior
    m = ink_mask(sheet_of(pid))
    if m is None:
        return None, 0
    H, W = m.shape
    n = max(int(ring.length // STEP), 8)
    hit = tot = 0
    for i in range(n):
        pt = ring.interpolate(i / n, normalized=True)
        x, y = int(round(pt.x)), int(round(pt.y))
        if not (0 <= x < W and 0 <= y < H):
            continue                           # 이미지 밖 = 주석이 딴 도면일 강한 신호지만
        tot += 1                               #        여기선 분모에만 넣는다
        y0, y1 = max(0, y - RADIUS), min(H, y + RADIUS + 1)
        x0, x1 = max(0, x - RADIUS), min(W, x + RADIUS + 1)
        if m[y0:y1, x0:x1].any():
            hit += 1
    if tot < 8:
        return None, tot
    return hit / tot, tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", nargs="*", default=[])
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--singletons", type=int, default=0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    pool = list(args.plans)
    if args.sample or args.singletons:
        idx = json.load(open(os.path.join(ST, "dedup_index.json"), encoding="utf-8"))
        sig, G = idx["sig_of"], idx["groups"]
        have = {f[:-4] for f in os.listdir(PNG) if f.endswith(".png")}
        cand = [p for p in sig if sheet_of(p) in have]
        if args.singletons:
            cand = [p for p in cand if G[sig[p]]["n"] == 1]
        random.Random(args.seed).shuffle(cand)
        pool += cand[: (args.singletons or args.sample)]

    rows = []
    for i, pid in enumerate(pool, 1):
        s, n = align_score(pid)
        rows.append((pid, s, n))
        if args.plans and not (args.sample or args.singletons):
            print("%-34s %s (샘플 %d)" % (pid, "정합률 %.3f" % s if s is not None else "측정불가", n))
        elif i % 50 == 0:
            print("   %d/%d" % (i, len(pool)), file=sys.stderr)

    ok = [r for r in rows if r[1] is not None]
    if len(ok) > 3:
        v = sorted(r[1] for r in ok)
        print("\n측정 %d개 · 중앙값 %.3f · 하위 5%% %.3f · 최소 %.3f"
              % (len(ok), v[len(v) // 2], v[max(0, int(len(v) * 0.05))], v[0]))
        for t in (0.5, 0.6, 0.7):
            print("   정합률 < %.1f : %d개 (%.1f%%)"
                  % (t, sum(1 for x in v if x < t), 100.0 * sum(1 for x in v if x < t) / len(v)))
    if args.out:
        json.dump([[p, s, n] for p, s, n in rows], open(args.out, "w", encoding="utf-8"))
        print("→", args.out)


if __name__ == "__main__":
    main()
