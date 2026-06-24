"""배치(placement) 기하 지표 — selfint / overlap / span (EXPERIMENTS clean 정의).

⚠️ 복구 메모(2026-06-24): 원본 diag_placement.py는 git 미커밋으로 소실(학습코드와 동일 케이스,
   handoff-ar-retrain-2026-06-23). render_geomclean.py가 의존하던 metrics(g) 표준 기하정의로 재구성.
clean = selfint_rooms==0 ∧ overlap_frac<0.25(실폴리곤 겹침) ∧ span_ratio<8 (render_geomclean 주석과 일치).
"""
from __future__ import annotations

from shapely.geometry import Polygon
from shapely.validation import make_valid


def _bbox_span(q):
    minx, miny, maxx, maxy = q.bounds
    w, h = maxx - minx, maxy - miny
    if min(w, h) <= 0:
        return 99.0
    return max(w, h) / min(w, h)


def metrics(g):
    """g-0.4 그래프 → {n_rooms, selfint_rooms, overlap_frac, span_ratio}.

    selfint_rooms : 자기교차(=shapely invalid) 방 수.
    overlap_frac  : 방-쌍 겹침 면적합 / 전체 방 면적합 (실폴리곤 기준).
    span_ratio    : 가장 세장한 방의 bbox 종횡비(max/min).
    """
    rooms = g.get("rooms") or {}
    n_rooms = len(rooms)
    selfint = 0
    valid = []          # 겹침 계산용(자기교차는 make_valid로 보정 후 포함)
    spans = []
    for r in rooms.values():
        pp = r.get("polygon") or []
        if len(pp) < 3:
            continue
        try:
            q = Polygon(pp)
        except Exception:  # noqa: BLE001
            selfint += 1
            continue
        if not q.is_valid:
            selfint += 1
            try:
                q = make_valid(q)
            except Exception:  # noqa: BLE001
                continue
        if getattr(q, "area", 0) <= 1:
            continue
        valid.append(q)
        spans.append(_bbox_span(q))

    total = sum(p.area for p in valid)
    inter = 0.0
    for i in range(len(valid)):
        for j in range(i + 1, len(valid)):
            try:
                inter += valid[i].intersection(valid[j]).area
            except Exception:  # noqa: BLE001
                pass
    overlap_frac = round(inter / total, 4) if total > 0 else 1.0
    span_ratio = round(max(spans), 2) if spans else 99.0
    return dict(n_rooms=n_rooms, selfint_rooms=selfint,
                overlap_frac=overlap_frac, span_ratio=span_ratio)
