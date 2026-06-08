# KR_CONVENTIONS — 한국형 아파트 관행 레지스트리 (법규와 별도)

> **법규(法規) ≠ 관행(慣行).** 법규(채광·최소면적·피난거리·복도폭)는 통계법/건축법이라
> `법령 DB + Regulation Validator`가 담당. 이 문서는 법은 아니지만 "이게 정상 한국 아파트"라는
> **유형·동선 규범**을 데이터로 정의한다. 생성 AI는 관행을 통계적으로만 따르므로 **명시적으로
> 보장·검사**해야 환각을 막는다. (룰 = 데이터/레지스트리 — `constraint-rule-framework`)

## 0. 적용 위치 (파이프라인 3곳)
1. **Text-to-Graph (생성)**: 관행 패턴을 **기본 서브그래프 템플릿**으로 주입.
2. **위상 검증기 (무결성)**: 불변 하드 규칙 검사.
3. **관행 검증/스코어러 (법규 검증기와 병렬, self-correction)**: 유형 부합도 = **소프트 점수**.
4. (암묵) 골드 데이터 분포에 내재 → Graph-to-Geometry가 학습.

## 1. 두 층: 불변(hard) vs 유형변형(soft)
- **불변(invariant·hard)** = 구축/신축/평형 공통. 위반 = 오류.
- **유형변형(variant·soft)** = 신축/구축/평형별로 **여럿 다 정상**. 검증은
  **"정상 패턴 중 하나와 부합하는가"** — 특정 1개 템플릿 일치를 강요하면 안 됨
  (예: 구축에 파우더룸 없다고 **감점 금지**).

## 2. 룰 포맷 (데이터)
```jsonc
{
  "id": "KR-H-01",
  "kind": "hard | soft",
  "applies_to": { "house": ["APT"], "vintage": ["any|신축|구축"], "cond": "안방 존재 시" },
  "statement": "사람이 읽는 규칙",
  "check": "Layer2 graph에서 평가하는 식(필드 참조)",
  "severity": "error | warn | score",
  "fix_hint": "위반 시 교정 방향(over-repair 금지)"
}
```

## 3. 불변 규칙 (hard) — seed
- **KR-H-01 방통과 금지**: 사적 방은 connector(복도·거실·전실) 경유로 진입.
  - check: 모든 private room의 진입 경로에 다른 private room이 없음
    (`edge.privacy_transition`이 private→private 관통이 동선상 유일하면 위반).
  - fix: 문 추가 ❌ → **connector 신설해 경유**(over-repair 금지).
- **KR-H-02 문 없는 방 금지**: 모든 방은 최소 1개 개구부(door 또는 open).
  - check: `degree(room) ≥ 1` in topology edges.
- **KR-H-03 거주실 채광**: 거실·침실·안방은 창 ≥ 1.
  - check: role∈{거실,침실,안방} → `room.has_window == true`.
- **KR-H-04 안방 전용 위생 접근**: 안방은 전용 위생공간(전용욕실)에 접근 가능.
  - check: 안방에서 전용욕실까지 **사적 경로**(거실·복도 안 거치고) 존재.
  - note: *접근*이 불변이지, 경유 공간(파우더룸 등)은 변형(아래).
- **KR-H-05 현관 도달성**: 현관에서 모든 공간 도달(`distance_from_entrance` 유한).

## 4. 유형변형 (soft) — seed
- **KR-S-01 메인 동선**: `현관 →[전실]→ 거실`, 거실/복도가 허브.
  - 변형: 전실 생략(소형)·복도 길이 다양. score: 허브 경유 비율.
- **KR-S-02 마스터 스위트 — 변형 집합**(택1 부합 = 정상):
  ```
  신축 :  안방 —open— 파우더룸 —door/open— {드레스룸, 전용욕실}
  구축A:  안방 —door— 전용욕실(직접) [+ 드레스룸 옵션]
  구축B:  안방 —door— 욕실 [드레스룸 없음, 붙박이장]
  ```
  - applies_to: house=APT, cond=안방 존재. **파우더룸·드레스룸 유무는 감점 대상 아님**.
  - score: 위 패턴 중 최근접 하나와의 부합도.
- **KR-S-03 습식공간 군집**: 욕실·주방 등 설비 라인 인접(배관 효율).
  - score: 습식 노드 간 거리/벽 공유.
- **KR-S-04 발코니 확장**: 거실/주방이 발코니 끝(외벽)까지 확장(신축 전형).

## 5. 운용 원칙
- **유형은 메타가 아니라 구조에서 추론**(파우더룸 노드 유무 등) + 골드 분포서 학습
  (AI-Hub에 신축/구축 메타 없을 가능성).
- soft 규칙은 **금지가 아니라 점수** → 생성 가이드·self-correction 우선순위로만.
- over-repair 경고: 위상 오류를 억지 문 추가로 덮지 말 것(`topology-is-grammar-not-picture`).

관련: `GEOMETRY_SCHEMA.md`(무엇을 담나) · canonical 한국 아파트 위상(메모리) · `SPEC.md`.
