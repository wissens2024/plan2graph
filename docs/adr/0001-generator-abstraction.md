# ADR-0001: 생성 모델 추상화 (Generator 인터페이스 + arch 레지스트리 + 체크포인트 디스패치)

Status: Accepted
Date: 2026-06-05
Deciders: wissens2024

## Context
생성 모델은 지속 교체될 예정(set-transformer-v2 → type조건 → graph-diffusion → 차세대). 현재 `train_gen.py`가 단일 arch를 하드코딩하고, `NeuralGenerator`가 특정 체크포인트 포맷에 묶여 있으며, 소비자(`eval_gen`·`gen_loop`·`admin`)가 이에 직접 결합돼 있다. 모델을 추가하려면 `train_gen`을 고쳐야 하고, **실행 중 작업(v3/v4 매트릭스가 시드마다 `train_gen` 재import)** 이 구조 변경에 깨진다. 다만 공통 인터페이스 `generate(program, rng) → nx.Graph`는 이미 duck-typed로 존재(baseline·neural 공유).

## Decision
생성기를 **`Generator` ABC**(`generate(program, rng, **kw)` + `adj_score` + `from_checkpoint`) + **arch 레지스트리**(`@register("arch")`, `make`, `load`, `archs`) + **체크포인트 `condition.arch` 태그로 자동 디스패치하는 `load()`** 로 표준화한다. 소비자는 `generate()` 인터페이스에만 의존한다. 새 모델 = `generators/` 파일 1개 + `@register`. 구현은 가산적(기존 baseline·set-transformer를 **래핑**, train_gen/model_baseline 내부 불변 → 소비자 점진 이관 → 이후 plugin).

## Considered Alternatives
1. **현행 유지(train_gen 직접 수정)** — 모델마다 train_gen 편집 — **기각**: 소비자 결합·재import 일관성 붕괴·실행중 작업 위험.
2. **모델별 스크립트 복제**(train_gen_typed.py 등) — **기각**: 코드 중복·드리프트, 소비자 분기 폭증, 체크포인트 호환 수동.
3. **외부 프레임워크**(MLflow Models / Hydra) — **기각**: 의존성·러닝커브 과함, 현 규모엔 과설계. 경량 자체 레지스트리로 충분.

## Consequences
- Positive: 새 모델 plugin화(소비자 0수정), 실행중 작업 무영향, **체크포인트만으로 구·신 모델 로드**(GUI/eval 호환), type조건의 v3/v4 일관성 리스크 해소.
- Negative: 1회 리팩터(추상화+이관) 비용, 얇은 간접층.
- Follow-up: ① baseline·set-transformer 래퍼(본 ADR로 신설) → ② eval_gen/gen_loop/admin을 `generators.load/make`로 이관 → ③ **type조건을 첫 plugin으로 검증**(GPU0) → ④ v3/v4 종료 후 train_gen 내부 정리.

## Assumptions
- 모든 생성기가 `generate(program, rng, **kw) → nx.Graph` 단일 인터페이스로 충분(현 baseline·neural 충족).
- 체크포인트에 arch 태그 보존(현 `condition.arch` 존재).
- 모델이 계속 늘어난다 — 이 전제가 거짓이면 추상화는 과설계.
