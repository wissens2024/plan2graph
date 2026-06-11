"""기하 공용 헬퍼 — 방 유형 정규화(_type)와 **인접 판정 단일정의(share_wall)**.

⚠️ 좌표 배치는 생성형 기하 AI(`geom_gen`, geom_g0)가 한다. 옛 규칙기반 treemap 배치
(layout_rooms·squarify·render_floorplan_fig 등)는 **폐기**했다 — 사업계획 목표는 생성 AI다.
이 모듈에 남은 것은 모델과 무관한 두 헬퍼뿐:
  - `_type`      : 노드 dict → "공간_*" 정규화 역할명
  - `share_wall` : 두 사각형이 공유하는 벽 구간 — 측정(cadrender.verify)과 realizer가 같이 쓰는 자(nail 4)
"""
from __future__ import annotations


def _type(d):
    t = d.get("type")
    if not t:
        return None
    return t if str(t).startswith("공간_") else "공간_" + str(t)


def share_wall(r1, r2, min_len=0.3, coincide=1e-3):
    """두 사각형 (x,y,w,h)이 공유하는 벽 구간 → ('v'|'h', 좌표, lo, hi) 또는 None.
    **인접 판정 단일정의** — 측정(cadrender.verify)과 최적화(realizer) 둘 다 이 함수를 쓴다(nail 4).
    min_len·coincide 단위는 입력 좌표계(m 또는 px)."""
    x1, y1, w1, h1 = r1
    x2, y2, w2, h2 = r2
    # 수직 벽(좌우로 인접): x 경계 일치 + y 겹침
    for xa, xb in ((x1 + w1, x2), (x2 + w2, x1)):
        if abs(xa - xb) < coincide:
            lo, hi = max(y1, y2), min(y1 + h1, y2 + h2)
            if hi - lo > min_len:
                return ("v", xa, lo, hi)
    # 수평 벽(상하로 인접): y 경계 일치 + x 겹침
    for ya, yb in ((y1 + h1, y2), (y2 + h2, y1)):
        if abs(ya - yb) < coincide:
            lo, hi = max(x1, x2), min(x1 + w1, x2 + w2)
            if hi - lo > min_len:
                return ("h", ya, lo, hi)
    return None
