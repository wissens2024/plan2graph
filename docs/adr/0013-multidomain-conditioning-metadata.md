# ADR-0013: 다중 도메인 조건부 학습 — 그래프 메타 조건 필드 + 통합 카테고리 어휘

Status: Accepted
Date: 2026-06-15
Deciders: wissens2024, Claude

## Relates
- ADR-0006(ONE 엔진)·ADR-0012(생성 타깃) 위에서, *데이터를 어떻게 조건화해 합동학습하나*를 정함.
- ADR-0011(데이터 구분자: 주거형태 그래프 필수)의 **확장** — 주거형태에 country·label_schema를 더함.
- [[diffplanner-rplan-transfer-blocked]](naive 전이 막힘) 문제의 *대안 해법*.

## Context (검증된 사실 + 사용자 제안)
1. **`[검증]` naive 전이 막힘**: RPLAN(방8/6범주) → 한국(방18/13역할) 가중치 직접 로드는 임베딩 텐서 크기 불일치로 실패. + *의미적으로도* RPLAN 6범주는 거칢 — `cIdx` 매핑상 **거실=LivingRoom+식당+현관+드레스룸 흡수, 침실=Master·Child·Study·Second·Guest 통합**, 주방·화장실·발코니·창고. → **드레스룸·전실·복도·실외기실·안방 같은 한국 고유공간이 RPLAN엔 없음.**
2. **`[검증]` 라벨 없는 글로벌+한국 혼합 = 무익~악화**(combine v3/v4, 5시드, EXPERIMENTS §8). 글로벌-only는 처참(도메인 격차).
3. **`[검증]` 그러나 조건화(housing_type APT/DEH/ROW)는 단일 최강 레버**(매크로 0.123, EXPERIMENTS §7) — 소수형태 격차를 메움.
4. → **결론**: "그냥 섞기"가 아니라 **도메인을 *라벨로 분리*하는 조건부 합동학습**이 길. 그래프에 country/dataset/housing_type/label_schema를 박고, 통합 어휘로 한 모델이 공유 학습. (= "고양이/호랑이: 같은 골격 + 종 라벨" 직관 = 사용자 제안.)

## Decision
### 1. 그래프 `meta`에 조건 필드 추가 (정식 스키마)
```
meta.country:      "CN" | "KR" | "EU"          # 도메인 조건 토큰 (CubiCasa=핀란드/EU, US 아님)
meta.dataset:      "RPLAN" | "AIHUB_KR" | "CubiCasa"   # provenance(회계·필터, ADR-0005). 지금 country와 1:1이나 분리 보관
meta.housing_type: "apartment" | "detached" | "rowhouse"   # 검증된 최강 레버 (house_type APT/DEH/ROW의 정규화 이름)
meta.label_schema: "rplan_6cat" | "korean_13cat" | "cubicasa_Ncat"   # 라벨 거칠기 조건(예: rplan_6cat 거실엔 현관·드레스룸 섞임 → 모델이 감안)
```
- AI-Hub geomgraph 빌더는 `country=KR, dataset=AIHUB_KR, label_schema=korean_13cat`를 발행(구현 §아래). RPLAN/CubiCasa 어댑터도 동일 규약으로 채움(follow-up).

### 2. 통합 카테고리 어휘(union) + 소스별 매핑 — ★키스톤
한 모델이 공유 임베딩으로 학습하려면 **union 어휘 1개**가 필요(크기 불일치 우회). 공통은 합치고 한국 전용은 추가:
```
union 후보: living · bedroom · master_bedroom · kitchen · bathroom · balcony · storage
            · dressing(KR) · vestibule/전실(KR) · corridor/복도(KR) · utility/실외기실(KR) · etc
  (현관=방 아님, entrance_expand로 별도 — ADR-0012/DiffPlanner 규약)
매핑 예: RPLAN living ← {LivingRoom,Dining,Entrance,Walk-in},  bedroom ← {Master,Child,Study,Second,Guest}
        KR korean_13 ← 거의 1:1,  CubiCasa ← 유럽 타입 매핑
```
해외 샘플은 한국 전용 칸을 *안 쓸 뿐*. label_schema가 "이 샘플 어휘가 얼마나 거친가"를 모델에 알림.

### 3. max_rooms는 그래프 메타 아님 — 엔진 용량 파라미터
- **13 = 카테고리 종류 수**, **18 = 세대당 최대 방 수** — 다른 축. 섞지 말 것. `max_rooms(=18)`은 엔진/학습 config (korean_to_engine.MAX_ROOMS), 그래프 필드 아님.

### 4. 학습 방식
- naive pretrain→finetune(차원 막힘) 대신 **union 어휘 + 도메인/주거/스키마 조건으로 합동학습**.
- 측정: **한국 단독(baseline) vs 합동(조건부)** 을 동결 test로 비교 → 가설(해외 prior가 한국에 도움)을 *숫자로* 판정.

## Considered Alternatives
1. **naive 가중치 전이(resume)** — 차원 불일치로 실패 + RPLAN 어휘 거칢. 기각(부분로딩은 가능하나 union 합동학습이 더 깔끔).
2. **라벨 없는 글로벌+한국 혼합** — 무익~악화 측정됨(§Context2). 기각.
3. **소스별 별도 모델** — 공정 비교 어렵고 소수형태 데이터 소량. 기각.
4. **max_rooms를 label_schema에 포함(rplan_6/korean_13/korean_18)** — 카테고리수(13)와 방수(18)는 다른 축이라 혼동. 기각(분리).

## Consequences
- Positive: 임베딩 크기 불일치 *우회*(union 하나), 도메인 분리 학습, 검증된 housing 조건 성공의 확장, 가설을 측정으로 결판, 그래프가 다국가 데이터를 일관 표현.
- Negative: union 어휘+매핑 테이블 구축 필요, 빌더·어댑터·엔진 입력 수정, 교차도메인 효과 미검증(측정해야).
- Follow-up: ① union 어휘 정의 + 소스별 매핑 테이블 ② RPLAN/CubiCasa 어댑터에 country/dataset/label_schema 채움 ③ 엔진 입력(korean_to_engine 등)에 조건 토큰 주입 ④ **한국단독 vs 합동 비교 측정**.

## Assumptions
- `[검증]` housing_type 조건 효과는 측정됨. `[추론]` country·label_schema 교차도메인 효과는 미검증 — 측정 필요.
- union 매핑이 의미를 보존한다(공통 타입이 실제로 호환). 
- 도메인 격차(중국형 RPLAN ≠ 한국)가 너무 크면 조건화해도 한국 단독을 못 넘을 수 있음 → 측정으로 확인.
- 이 전제가 깨지면 재검토.
```

## 구현 상태 (2026-06-15)
- ✅ geomgraph 빌더 `meta`에 country/dataset/housing_type/label_schema 추가(AI-Hub=KR/AIHUB_KR/korean_13cat). 코드 `geomgraph.py build()`.
- ⬜ union 어휘·매핑, 어댑터 채움, 엔진 조건 주입, 비교 측정 = follow-up.
