"""가구(fixtures) 카탈로그 — ADR-0010 결정의 단일 소스.

2계층:
- Tier A `obj_detected`: AI-Hub OBJ 검출 5종(변기·세면대·싱크대·가스레인지·욕조).
  실제 위치·bbox·rotation은 OBJ 라벨에서 옴(여기 size_mm는 폴백 기본값).
- Tier B `role_inferred`: 데이터에 없는 가독성용 가구. 역할→가구 카탈로그 + 배치규칙.

size_mm = [폭(벽 따라), 깊이(벽에서 안쪽)]. placement = 배치 휴리스틱(ADR-0006).
방 크기에 맞춰 가구 크기 클램프는 엔진/완성층 책임(여기는 표준치 + 규칙만).
"""
from __future__ import annotations

# ── Tier A: 검출 5종 (config.OBJECT_CLASSES와 1:1) ──────────────────────────
#   key = 정규화 가구명. cls = AI-Hub 카테고리. size_mm = 폴백(보통 bbox로 대체).
DETECTED = {
    "변기":     {"cls": "객체_변기",     "category": "bath",    "size_mm": [400, 600]},
    "세면대":   {"cls": "객체_세면대",   "category": "bath",    "size_mm": [550, 450]},
    "욕조":     {"cls": "객체_욕조",     "category": "bath",    "size_mm": [1600, 750]},
    "싱크대":   {"cls": "객체_싱크대",   "category": "kitchen", "size_mm": [1200, 600]},
    "가스레인지": {"cls": "객체_가스레인지", "category": "kitchen", "size_mm": [600, 600]},
}

# ── Tier B: 역할→추론 가구 (ADR-0010 확정 카탈로그) ─────────────────────────
#   placement: against_wall(긴벽)·facing(짝과 마주봄)·corner·center·near(짝 옆)
ROLE_INFERRED = {
    "거실":   [
        {"name": "소파", "size_mm": [2000, 900], "placement": "against_wall"},
        {"name": "TV",   "size_mm": [1200, 300], "placement": "facing", "faces": "소파"},
    ],
    "침실":   [
        {"name": "침대", "size_mm": [1500, 2000], "placement": "against_wall", "rule": "문 반대 벽"},
        {"name": "옷장", "size_mm": [1200, 600],  "placement": "against_wall"},
    ],
    "안방":   [
        {"name": "침대", "size_mm": [1600, 2000], "placement": "against_wall", "rule": "문 반대 벽"},
        {"name": "옷장", "size_mm": [1400, 600],  "placement": "against_wall"},
    ],
    "주방":   [  # 싱크대·가스레인지는 검출(Tier A)이라 제외, 추론분만
        {"name": "냉장고", "size_mm": [800, 700], "placement": "corner"},
        {"name": "식탁",   "size_mm": [1200, 800], "placement": "center"},
    ],
    "현관":   [
        {"name": "신발장", "size_mm": [1000, 350], "placement": "against_wall"},
    ],
    "드레스룸": [
        {"name": "붙박이장", "size_mm": [1800, 600], "placement": "against_wall"},
    ],
}


def detected_name(cls: str) -> str | None:
    """AI-Hub OBJ 카테고리(객체_*) → 정규화 가구명."""
    for name, spec in DETECTED.items():
        if spec["cls"] == cls:
            return name
    return None


def inferred_for(role: str) -> list[dict]:
    """역할 → Tier B 추론 가구 목록(없으면 빈 리스트)."""
    return ROLE_INFERRED.get(role, [])
