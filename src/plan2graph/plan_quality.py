"""plan_quality — 평면도 그래프 1세대의 '온전(사용) vs 보정필요' 판정 (단일 소스).

ADR-0007의 데이터 통일 전제: **온전한 데이터만 학습/사용**, 쓰레기(병합·과다·오라벨)는
보정필요 큐(알바 SVG 보정)로 넘긴다. 이 모듈이 그 판정의 **유일한 진실**이다 —
korean_to_engine.py(엔진 입력 게이트)와 검수 UI가 같은 함수를 호출한다(숫자 일치).

판정은 **구성(composition) 기반**: 방 역할 카운트 + 면적 상대비교만 본다(좌표·스케일 불필요).
세션 실측(APT 5,000세대): 온전 3,028(61%) / 보정필요 1,972(39%). 사유 분포:
  기타과다 678 · 현관≠1(병합) 658 · 발코니과다 614 · 거실≠1 471 · 침실<화장실 291 · 거실오라벨 67.
한 세대가 여러 사유에 걸릴 수 있다(사유는 합집합).

사용:
    from plan2graph.plan_quality import classify
    clean, reasons = classify(graph_dict)   # graph = corrected graphs/APT_*.json 로드 결과
"""
from __future__ import annotations

from collections import Counter

# 역할 군 — 면적 상대비교용
HABITABLE = ("거실", "안방", "침실", "주방", "드레스룸", "다목적공간", "알파룸")  # 거주/생활 공간
WET = ("화장실", "욕실", "전용화장실", "전용욕실", "파우더룸")            # 물공간

# 임계값(세션 실측으로 보정). 넘으면 '보정필요'.
MAX_BALCONY = 4   # 발코니 4개까지 정상(확장발코니 다수), 5+ = 라벨 폭주 의심
MAX_ETC = 3       # 기타(미상) 3개까지, 4+ = 추출 노이즈 의심


def _area(r):
    return r.get("area_px") or 0


def classify(graph: dict):
    """graph(corrected 그래프 dict) → (clean: bool, reasons: list[str]).

    clean=True  → 온전(사용): 학습/엔진 입력 가능.
    clean=False → 보정필요: 사유(reasons)와 함께 알바 보정 큐로.
    reasons는 사람이 읽는 한국어 사유 태그(중복 없음, 발생 순).
    """
    rooms = graph.get("rooms", {})
    if not rooms:
        return False, ["방없음"]
    roles = [r.get("role") for r in rooms.values()]
    cnt = Counter(roles)
    reasons: list[str] = []

    # 1) 현관 정확히 1개 — 0=추출누락, 2+=다세대 병합(상류 분할 필요)
    n_ent = cnt.get("현관", 0)
    if n_ent != 1:
        reasons.append("현관≠1(병합)" if n_ent > 1 else "현관없음")

    # 2) 발코니 과다 — 라벨 폭주(확장발코니 옛 벽선이 방으로 오검출)
    if cnt.get("발코니", 0) > MAX_BALCONY:
        reasons.append("발코니과다")

    # 3) 기타(미상 역할) 과다 — fragment/노이즈 누적
    if cnt.get("기타", 0) > MAX_ETC:
        reasons.append("기타과다")

    # 4) 거실 정확히 1개 — 0=오라벨/누락, 2+=병합
    if cnt.get("거실", 0) != 1:
        reasons.append("거실≠1")

    # 5) 거실 오라벨 — 거실이 가장 큰 거주공간이 아니면(다른 방이 더 큼) 라벨 의심
    living = [_area(r) for r in rooms.values() if r.get("role") == "거실"]
    other_hab = [_area(r) for r in rooms.values()
                 if r.get("role") in HABITABLE and r.get("role") != "거실"]
    if living and other_hab and max(living) < max(other_hab):
        reasons.append("거실오라벨")

    # 6) 침실 < 화장실 — 가장 작은 침실/안방이 가장 큰 물공간보다 작으면 면적/라벨 모순
    beds = [_area(r) for r in rooms.values() if r.get("role") in ("침실", "안방")]
    wets = [_area(r) for r in rooms.values() if r.get("role") in ("화장실", "욕실")]
    if beds and wets and min(beds) < max(wets):
        reasons.append("침실<화장실")

    return (not reasons), reasons


def disposition(graph: dict) -> str:
    """편의: 'use'(온전) | 'fix'(보정필요). dataset_status 처분 축과 동일 어휘."""
    clean, _ = classify(graph)
    return "use" if clean else "fix"
