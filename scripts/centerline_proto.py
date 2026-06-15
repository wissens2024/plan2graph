"""벽 중심선 스냅 1차 프로토타입 — AI-Hub 벽두께 폴리곤 → 공유 중심선.

geomgraph가 계산한 interior 벽(rooms=[a,b], segment=두 방 공유 경계)을 이용해,
인접 방 폴리곤의 평행·근접 변을 그 벽 직선으로 투영 → 두 방이 같은 선 공유(gap 제거).
1차 근사(코너 정합 미완) — gap이 줄어드는지 before/after로 검증.

사용:
  PYTHONPATH=src python scripts/centerline_proto.py --dir data/staging/corrected/graphs --out /tmp/cl_proto
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys

sys.path.insert(0, "src")
from plan2graph import cadrender  # noqa: E402


def _proj_to_line(pt, a, b):
    """점 pt를 직선 a-b 위로 수직 투영(수선의 발)."""
    ax, ay = a; bx, by = b; px, py = pt
    vx, vy = bx - ax, by - ay
    dd = vx * vx + vy * vy
    if dd < 1e-9:
        return [px, py]
    t = ((px - ax) * vx + (py - ay) * vy) / dd
    return [round(ax + t * vx, 1), round(ay + t * vy, 1)]


def snap_to_walls(g):
    """interior 벽 직선으로 양 방 경계 변을 투영 → 공유(gap 제거)."""
    rooms = g.get("rooms") or {}
    new = {nid: [list(p) for p in (r.get("polygon") or [])] for nid, r in rooms.items()}
    n_snapped = 0
    for w in (g.get("walls") or []):
        if w.get("type") != "interior":
            continue
        rms = [r for r in (w.get("rooms") or []) if str(r) in new]
        seg = w.get("segment") or []
        if len(rms) < 2 or len(seg) < 2:
            continue
        a, b = seg[0], seg[-1]
        sang = math.atan2(b[1] - a[1], b[0] - a[0]) % math.pi
        smid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        for rid in rms[:2]:
            poly = new[str(rid)]
            n = len(poly)
            best_i, best_d = None, 1e18
            for i in range(n - 1):
                p, q = poly[i], poly[i + 1]
                pang = math.atan2(q[1] - p[1], q[0] - p[0]) % math.pi
                da = abs(pang - sang); da = min(da, math.pi - da)
                if da > 0.35:                      # 평행 아니면 skip
                    continue
                pmid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
                d = math.hypot(pmid[0] - smid[0], pmid[1] - smid[1])
                if d < best_d:
                    best_d, best_i = d, i
            if best_i is None or best_d > 40:      # 근접 변 없음
                continue
            poly[best_i] = _proj_to_line(poly[best_i], a, b)
            poly[best_i + 1] = _proj_to_line(poly[best_i + 1], a, b)
            n_snapped += 1
    for nid in new:
        rooms[nid]["polygon"] = new[nid]
    return g, n_snapped


def _gap_metric(g):
    """방 폴리곤 면적 합 / 전체 외곽 면적 — 1에 가까울수록 gap 적음(딱 붙음)."""
    try:
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
        polys = [Polygon([tuple(p) for p in r["polygon"]])
                 for r in (g.get("rooms") or {}).values()
                 if len(r.get("polygon") or []) >= 3]
        polys = [p for p in polys if p.is_valid and p.area > 0]
        if not polys:
            return None
        room_sum = sum(p.area for p in polys)
        hull = unary_union(polys).convex_hull.area
        return round(room_sum / hull, 3) if hull > 0 else None
    except Exception:  # noqa: BLE001
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", default="/tmp/cl_proto")
    ap.add_argument("--n", type=int, default=3)
    args = ap.parse_args()
    files = sorted(glob.glob(os.path.join(args.dir, "APT_*.json")))
    os.makedirs(args.out, exist_ok=True)
    done = 0
    for f in files:
        g = json.load(open(f, encoding="utf-8"))
        if sum(1 for r in (g.get("rooms") or {}).values()
               if (r.get("role") or r.get("base")) == "현관") != 1:
            continue
        if len(g.get("rooms") or {}) > 20 or not (g.get("validation") or {}).get("passed"):
            continue
        gap_before = _gap_metric(g)
        cadrender.render_png(cadrender.from_geomgraph(g))  # warm
        open(os.path.join(args.out, f"before_{done}.png"), "wb").write(
            cadrender.render_png(cadrender.from_geomgraph(json.loads(json.dumps(g)))))
        g2, ns = snap_to_walls(json.loads(json.dumps(g)))
        gap_after = _gap_metric(g2)
        open(os.path.join(args.out, f"after_{done}.png"), "wb").write(
            cadrender.render_png(cadrender.from_geomgraph(g2)))
        print(f"sample {done}: snapped {ns}변 | gap(면적/외곽) {gap_before} → {gap_after}",
              flush=True)
        done += 1
        if done >= args.n:
            break
    print(f"→ {args.out} (before/after {done}쌍)", flush=True)


if __name__ == "__main__":
    main()
