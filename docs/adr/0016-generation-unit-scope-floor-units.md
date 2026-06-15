# ADR-0016: 생성 단위 2레벨 — 단위세대/층평면도 구분 + scope·세대수 조건

Status: Accepted
Date: 2026-06-15
Deciders: wissens2024, Claude

## Relates
- ADR-0011(현관==1 단일세대 필터)의 *왜*를 밝히고 확장: "현관2"는 자연어로 말할 수 없으니 단위세대가 아니다.
- ADR-0013(다국가 조건 메타)에 **plan_scope·units** 조건 추가.
- ADR-0010(wall.type)에 **세대간벽(party)** 필요성 연결(floor 트랙).
- ADR-0015(토큰 코덱) META 토큰 확장.

## Context
- AI-Hub 원본 도면 1장 = **층평면도**(한 층 2~4세대 + 공용 코어: 계단실·엘리베이터·홀). 우리가 현관 기준 `iter_units`로 세대별(`u0/u1`)로 쪼갠다. RPLAN/CubiCasa는 처음부터 **단위세대**.
- **자연어 입력이 생성 단위를 결정한다**: "방3 화장실2"는 단위세대 기준. 하지만 사용자 반례 — **"방3 화장실2, 3세대 층평면도 그려줘"** 도 가능하다. 즉 층평면도도 유효한 생성 타깃이고, scope·세대수를 자연어로 지정한다.
- 층평면도 = **단위세대들의 조합 + 공용 코어 + 배치**(좌우 미러 등). 단위세대가 기본 빌딩블록이고 층은 그 상위 조합이다.
- 우리 데이터가 둘 다 지원: 쪼갠 `unit`(단위세대 학습) + 원본 `sheet`(층 학습).

## Decision

### 1. 생성 단위 = 2레벨
- **① 단위세대 생성**(기본·우선): program(방 구성, 현관 항상 1) → unit plan. = Phase1 핵심.
- **② 층 조립**(상위): 단위세대 ×N + 공용 코어 + 배치 → floor plan. 단위세대를 마스터한 뒤 그 위 레이어.

### 2. plan_scope·units 를 생성 조건으로
자연어로 scope와 세대수를 지정하므로 **조건(모델 입력)** 이다(데이터 메타이자 생성 조건 동시).
- `plan_scope`: `"unit"` | `"floor"`
- `units`: 세대수. **scope=unit이면 항상 1**, scope=floor이면 **1~n**(자연어 입력).
- 함께: `country`(ADR-0013) · `housing_type` · 단위세대 `program`.

### 3. 데이터에 둘 다 보존·학습 (우선순위 단위세대)
- **단위세대 데이터**: 쪼갠 `unit` 그래프 — `plan_scope=unit, units=1`. 지금 보유(40k). **먼저 학습.**
- **층평면도 데이터**: 원본 `sheet` 그래프(쪼개지 않은 통째) — `plan_scope=floor, units=N`. **빌드 follow-up**(현재 unit만 그래프화).

### 4. meta 필드(g-0.4 확장)
| 필드 | 값 | 비고 |
|---|---|---|
| `plan_scope` | unit \| floor | 생성 조건 + 데이터 라벨 |
| `units` | int (unit=1, floor=N) | floor일 때 세대수 조건 |
| `units_in_source` | int | 원본 sheet 세대수(`iter_units` 카운트) |
| `unit_index` | int | u0=0… (분할 출처 추적) |
| `source_sheet_id` | str | 분할 출처(겹침 큐·오류 추적) |

`unit_native`(RPLAN/CubiCasa)는 `plan_scope=unit, units=1, units_in_source=1`.

### 5. 데이터 품질 신호 (ADR-0011 확장)
- **scope=unit인데 program에 현관≥2 또는 부엌≥2 = 분리 실패 / 층평면도 잔재** → 제외 또는 재분리. ("현관2"는 자연어 불가 = 단위세대 아님.)
- 직전 발견한 **겹침 꼬리**(ADR-0015)는 floor→unit 분할 시 옆 세대 잔재 의심 → `source_sheet_id`로 같은 sheet 세대 묶어 점검.

### 6. 세대간벽(party)·공용 코어는 floor 트랙의 1급 (ADR-0010 확장)
- 단위세대만 보면 후순위였으나, **층 조립을 타깃에 넣으면 필수**: 세대간벽(창 불가, 법규)·공용 코어 배치.
- `wall.type`에 `party`(세대간) 추가 — unit 그래프의 exterior 중 원본 sheet에서 다른 세대와 접한 벽. floor 빌드 시 함께.

### 7. 코덱 META 토큰 확장 (ADR-0015)
- `wallcycle_codec` META 토큰에 `plan_scope`·`units` 추가. unit/floor·세대수가 토큰 시퀀스 조건으로 들어감.

## Considered Alternatives
1. **단위세대만 생성 타깃** — 기각: "3세대 층평면도 그려줘"가 자연어로 가능(사용자 반례). 층은 단위세대 조합으로 유효.
2. **세대수를 항상 조건** — 기각: unit은 정의상 항상 1. floor일 때만 1~n.
3. **세대수를 데이터 메타로만(조건 아님)** — 기각: 자연어로 scope·세대수를 지정하므로 생성 조건.
4. **층평면도를 안 만들고 unit만 보존** — 기각: 원본 sheet가 층 학습 자산. 보존해 follow-up.

## Consequences
- Positive: 단위세대(기본)·층(조합) 2레벨이 자연어 입력과 정합. scope·units·country가 일관 조건. 데이터 품질 신호(현관2=분리실패) 명확. RPLAN/CubiCasa(unit_native)와 AI-Hub(floor→unit) 통일 표현.
- Negative: 층 그래프 빌드 추가 작업(현재 unit만). party wall은 층 컨텍스트(분할 전) 필요해 파이프라인 수정. 코덱 META 확장(vocab 재계산).
- Follow-up: ① geomgraph meta에 plan_scope/units 채움(unit=unit/1) ② 코덱 META 토큰 확장 ③ floor 그래프 빌더(원본 sheet 통째) ④ party wall 태깅(floor 빌드 시) ⑤ unit인데 현관≥2 = 재분리 큐 ⑥ **보정 에디터(edit_server) 통일**: 사람이 고친 평면도구분·세대수가 모델 조건에 반영되도록 `plan_scope`(unit/floor)·`units`·`n_entrance` **동일 필드**로 쓴다(에디터가 만든 plan_kind/n_households 한글 3값은 폐기, 1회 마이그레이션). 도면찾기 필터·세대수 자동(현관수)·unit→units=1 강제 동일 규칙. "기타"는 plan_scope 값 아님 → 제외(disposition).

## Assumptions
- `[검증]` 단위세대가 자연어 입력 주류이자 더 단순 → Phase1 우선.
- `[추론]` 층 조립이 단위세대 위 레이어로 학습 가능(공용 코어·배치·미러). 데이터(원본 sheet) 있음. 미측정.
- 깨지면(층 조립이 단위세대와 무관하게 어려움) 재검토.
