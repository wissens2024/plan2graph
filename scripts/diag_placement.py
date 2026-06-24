"""배치(placement) 기하 지표 — selfint / overlap / span (EXPERIMENTS clean 정의).

⚠️ 복구 메모(2026-06-24): 원본 diag_placement.py는 git 미커밋으로 소실(학습코드와 동일 케이스,
   handoff-ar-retrain-2026-06-23). render_geomclean.py가 의존하던 metrics(g) 표준 기하정의로 재구성.
clean = selfint_rooms==0 ∧ overlap_frac<0.25(실폴리곤 겹침) ∧ span_ratio<8 (render_geomclean 주석과 일치).
"""
from __future__ import annotations

from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid


def _bbox_span(q):
    minx, miny, maxx, maxy = q.bounds
    w, h = maxx - minx, maxy - miny
    if min(w, h) <= 0:
        return 99.0
    return max(w, h) / min(w, h)


def metrics(g):
    """g-0.4 그래프 → {n_rooms, selfint_rooms, overlap_frac, overlap_any, span_ratio}.

    selfint_rooms : 자기교차(=shapely invalid) 방 수. (OGC Simple Features 유효성 = 표준)
    overlap_frac  : **% Overlap 표준 정의** = (Σ방면적 − 합집합면적) / Σ방면적, 실폴리곤 기준.
                    겹친 총면적 / 생성 총면적 (Lara et al. RLVR floorplan, arXiv:2605.14117).
                    ⚠️ 이전 정의(방-쌍 겹침 *합*)는 3방 겹침 중복계산으로 과대-엄격이라 폐기.
    overlap_any   : 임의 겹침 존재 여부(boolean) — RLVR의 binary overlap.
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
    try:
        union_area = unary_union(valid).area if valid else 0.0
    except Exception:  # noqa: BLE001
        union_area = total                         # 합집합 실패 → 겹침 0 가정(보수적)
    overlapped = max(0.0, total - union_area)      # 겹친 총면적(다중피복 초과분)
    overlap_frac = round(overlapped / total, 4) if total > 0 else 1.0
    overlap_any = overlapped > 1.0                 # 1px² 초과 겹침 = 실제 겹침
    span_ratio = round(max(spans), 2) if spans else 99.0
    return dict(n_rooms=n_rooms, selfint_rooms=selfint,
                overlap_frac=overlap_frac, overlap_any=overlap_any, span_ratio=span_ratio)
