# 생성모델 학습·평가 구조 (Training & Eval)

> 목적: 학습 쪽도 데이터셋 버전관리(DATASET_DESIGN)와 **짝으로 못박아** 세션마다 흔들리지 않게 한다.
> 이름 충돌·개발본 혼입으로 결과가 헷갈렸던 걸 방지하는 **단일 규칙서**. 결과 보고는 [EXPERIMENTS.md](EXPERIMENTS.md).

---

## 1. 파이프라인 (한눈에)

```
[글로벌 사전학습셋]            [한국형 파인튜닝셋]         [평가: AI-Hub 동결 test]
 CubiCasa/RPLAN  ──사전학습──▶  AI-Hub(v0)  ──파인튜닝──▶  test 518장 (전 버전 공유)
   (선택)          50ep          100ep                       │
                                  │                          ├─ 규제루프 off/on
                              val 599로 temperature 선택       └─ seen 487 / unseen 31 로 분할 진단
```

- 코드: 학습 `train_gen.train()` · 평가 `eval_gen.evaluate_version()` · 일반화 `eval_gen.generalization_diag()`
  · 집계 `experiments.agg_summary()` · 오케스트레이션 `scripts/run_matrix.sh`.

## 2. 직교 5축 (이 5개를 섞지 말 것 — 혼동의 뿌리였음)

| 축 | 값 | 의미 | 결정 위치 |
|---|---|---|---|
| **① 데이터버전** | v0·v1·v2·v3 | 학습 데이터 조합(AI-Hub/+보정/+CubiCasa/+RPLAN) | DATASET_DESIGN · recipe |
| **② 생성기** | 규칙기반(알고리즘) · 신경망(set-transformer) | 통계 baseline vs 학습모델. **신경망은 모델 1종** | `eval_gen` · `train_gen` |
| **③ 학습레짐** | 미배치 → 배치 | 학습 **절차 성숙**(모델 동일, "버전" 아님) | `train_gen.train(batch_size)` |
| **④ 추론/규제** | temperature · 규제루프 on/off | 다양성 조절 · 법규 자동보정 | `eval_gen` · `gen_loop` |
| **⑤ 평가렌즈** | 전체 · seen · unseen | 같은 test를 보는 각도 | `eval_gen.generalization_diag` |

> ⚠️ "v1/v2"는 ①(데이터)에서만 쓴다. ③(학습레짐)을 "set-transformer-v1/v2"로 부르던 건 **폐기**(이름 충돌).
> 신경망 라벨은 버전꼬리표 없이 `신경망(set-transformer)` 하나.

## 3. 모델 — set-transformer (`train_gen._build_model`, EdgeModel)

방을 **순서 무관 집합**으로 보고 어떤 방끼리 무엇으로 연결되는지 예측하는 **링크예측기**.

- 방 타입 → `Embedding(48)` → `TransformerEncoder`(d_model 48, 2층, 4헤드, ffn 96, dropout 0) → 방별 맥락 `h` + 전역 program 맥락 `g`(평균).
- 방 쌍 (i,j): `[h_i+h_j, |h_i−h_j|, g]` → 두 헤드: **edge**(연결 여부) + **via**(연결 종류: door/open/balcony/…).
- 입력 = program(방 타입 집합), 출력 = 인접 그래프. ("Set Transformer" 논문의 ISAB/PMA가 아니라 일반 TransformerEncoder의 집합 self-attention.)

## 4. 학습 절차 (`train_gen.train`)

- **전이학습**: `--pretrain global_{cubicasa|rplan}`(사전학습 50ep) → `--finetune v0`(파인튜닝 100ep). noPretrain은 파인튜닝 100ep만(동일 예산, 공정 비교).
- **배치학습 레짐**: 가변 방수를 **패딩 + 어텐션 마스크**로 한 배치(기본 64)에 쌓아 한 번에 통과 + 특징 1회 사전계산 → 미배치 대비 수배 가속(다중시드 매트릭스 현실화).
- 손실 = 링크예측(+via). neg_ratio 3, lr 1e-3, Adam. 시드 고정(`exp.seed_everything`) + `runs/<run_id>/` 보존(재현).
- run_id = `gen-<데이터버전>-<생성기>-<아키텍처>-<pretrain>-seed<N>`. 체크포인트는 git 제외(시드+코드로 재생성), 메타·지표·원장만 추적.

## 5. 평가 (`eval_gen`)

- **벤치마크 앵커 = AI-Hub 동결 test 518장**(`_frozen_test.json`, v0에서 정의·전 버전 공유). 이것만 불변 → v0~vN 비교 타당.
- **seen/unseen**(⑤): test를 평가 시점에 사후 분할. test 도면의 program 지문(방 타입 multiset)이 train에 있었나로 → **seen 487 / unseen 31**. *새 split이 아니라 진단 렌즈.* seen=암기·보간, unseen=진짜 일반화.
- **규제루프**(④ on): 생성 → 법규 위반 시 자동보정 → legal 0.94→1.00.
- **temperature**(④): val 599로 선택(`sweep_temperature`), test엔 고정값 적용.
- **지표**: `integrity`(위상 R1~R5) · `legal`(채광 등) · `adj_L1`(인접 L1, 낮을수록 GT 근접) · `diversity` · `novelty`.

## 6. 결과 채택 규칙 (드리프트 방지의 핵심)

1. **단일 소스**: 모든 결과는 `runs/index.jsonl` → `experiments.agg_summary()` 한 곳에서 집계. 대시보드 §3·§5·EXPERIMENTS.md가 이걸 공유(숫자 어긋남 불가).
2. **수렴·다중시드만 채택**: 시드 1개·미수렴 탐색본은 결과로 보지 않는다.
3. **개발 부산물 폐기**: 미배치(`set-transformer-v1`, 30ep·1시드)는 `experiments.RETIRED_ARCH`로 집계 제외. git/runs 원본엔 이력 보존.
4. **재학습 시 최종값만 남김**: 데이터 늘려 재학습하면 같은 표의 해당 버전 행을 **갱신**(중간값 누적 금지).

## 7. 오케스트레이션 (`scripts/run_matrix.sh`)

- 신뢰성 매트릭스: noPretrain × 5시드 + preCubicasa × 5시드. 각 학습 직후 eval + 일반화 진단을 원장에 누적, 끝나면 `experiments agg`로 평균±표준편차(시드 노이즈 판정).
- **GPU1만 사용**(운영 GPU0 보호), `CUDA_VISIBLE_DEVICES=1`.

## 8. 성능 개량 이력 · 다음 후보

| 개량 | 축 | 효과 |
|---|---|---|
| 미배치 → 배치학습 | ③ | 다중시드·수렴 가능 → neural adj_L1 안정화(0.074±0.008) |
| 규제루프 도입 | ④ | legal 0.94 → 1.00 |
| CubiCasa 사전학습(v2) | ① | unseen 일반화 0.260→0.247 + 분산 감소 |
| **🔜 RPLAN 사전학습(v3)** | ① | 사전학습 규모 ≈27배 → unseen 이득 확대 검증(예정) |
| 후보: 글로벌 합산 사전학습 · 좌표 회귀 · graph-diffusion | ②③ | PROJECT_PLAN §4-2 |
