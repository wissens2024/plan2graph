# 실험 — 학습·평가·결과 (EXPERIMENTS)

> TRAINING + EXPERIMENTS 통합본.

---

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


---

# 생성모델 실험 결과 — 데이터버전 v0 vs v2 (글로벌 사전학습 효과)

> 3단계(공간배치 생성 AI). **현재까지 완료된 학습**의 결과 보고이며, 재학습 결과로 **업데이트**(최종값만 남김).
> 집계: `python -m plan2graph.experiments agg` (원장 `runs/index.jsonl`). 최종 실행 2026-06-05.
> 데이터버전 정의는 [[memory: dataset-version-scheme]] / 대시보드 §1 참조.

---

## 1. 실험 목적
글로벌 사전학습셋(CubiCasa)으로 **사전학습 후 AI-Hub로 파인튜닝**(= 데이터버전 v2)하면, AI-Hub만(v0) 대비
생성 품질·일반화가 나아지는지 검증. 앞선 단일시드 "음의 전이"가 실제인지 노이즈인지 **다중시드(42·1·2·3·4)**로 판정.

## 2. 설정
- **두 축(분리)**: ① **파인튜닝 코퍼스** — v0(clean AI-Hub 4,531) / v2(V2V확장 20,944, test 누수0·frozen 동일). ② **사전학습** — none / CubiCasa(4,758). 2026-06-05 매트릭스 = 2×2×5시드.
- (옛 표기 "v2=CubiCasa사전학습"은 ②축이었음. 본 갱신부터 ①코퍼스축과 분리.)
- **생성기**: 규칙기반(알고리즘) · 신경망(**set-transformer** — 모델 1종).
  - 신경망 학습 = 배치학습 100ep(사전학습 50ep→파인튜닝 100ep), 시드 42·1·2·3·4.
  - ⚠️ 초기 **미배치 개발본**(30ep·1시드)은 비교 불가한 미수렴 부산물 → **결과 미채택**(git 이력만 보존).
- **규제 루프**: off/on(on = 법규 위반 자동보정).
- **평가셋**: AI-Hub **동결 test 518장**(전 버전 공유). 일반화 진단 = test를 seen 487 / unseen 31로 사후 분할.
- **지표**: `adj_L1`(인접 L1, **낮을수록** 실제에 가까움) · integrity · legal · diversity · novelty.

## 3. 결과 — 전체 test (시드 집계, mean±std) [최종 2026-06-05]

> 축 명확화: **코퍼스**(파인튜닝 데이터: v0 clean 4,531 / v2 = V2V확장 20,944)와 **사전학습**(none / CubiCasa)을 분리. (옛 표의 "v2"는 CubiCasa사전학습을 뜻했음 — 본 갱신부터 두 축을 분리해 혼동 제거.)

| 코퍼스(파인튜닝) | 사전학습 | 생성기 | 시드 | adj_L1 ↓ | integrity | legal(off/on) | div | nov |
|---|---|---|---|---|---|---|---|---|
| v0(clean 4,531) | — | 규칙기반 | 23 | **0.069** | 1.000 | 0.94/1.00 | 1.000 | 1.000 |
| v0(clean 4,531) | none | 신경망 | 11 | 0.074±0.007 | 1.000 | 0.94/1.00 | 0.987 | 0.976 |
| v0(clean 4,531) | CubiCasa | 신경망 | 10 | 0.074±0.007 | 1.000 | 0.94/1.00 | 0.989 | 0.979 |
| **v2(V2V확장 20,944)** | none | 신경망 | 5 | **0.104±0.005** | 1.000 | **0.37**/1.00 | 0.992 | 0.982 |
| **v2(V2V확장 20,944)** | CubiCasa | 신경망 | 5 | **0.102±0.005** | 1.000 | **0.37**/1.00 | 0.992 | 0.979 |

- **integrity 전 구성 1.000**(규칙엔진 보장). legal off→on은 규제루프 자동보정.
- **CubiCasa 사전학습 = 중립**(v0 0.074=0.074, v2 0.104→0.102 미미).
- ⚠️ **V2V 확장 코퍼스(20,944) 파인튜닝은 악화**: adj_L1 0.074→0.104, **legal(off) 0.94→0.37**. V2V 예측 노이즈가 충실도·법규준수를 떨어뜨림(다양성·신규성만 소폭↑). → 확장은 `use`건수엔 +([[V2V.md]] +375)이나 생성 충실도엔 −.

## 4. 결과 — 일반화 (seen 487 / unseen 31) [최종 2026-06-05]

| 코퍼스 | 사전학습 | 생성기 | 시드 | seen adj_L1 ↓ | unseen adj_L1 ↓ |
|---|---|---|---|---|---|
| v0 | — | 규칙기반 | 23 | **0.059** | 0.266 |
| v0 | none | 신경망 | 11 | 0.112±0.025 | 0.261±0.038 |
| v0 | CubiCasa | 신경망 | 10 | **0.101±0.012** | **0.247±0.019** |
| v2(확장) | none | 신경망 | 5 | 0.135±0.018 | 0.279±0.011 |
| v2(확장) | CubiCasa | 신경망 | 5 | 0.132±0.018 | 0.279±0.023 |

- seen = train에 있던 방 구성(암기로 풀림), unseen = 처음 보는 구성(진짜 일반화).
- **v0+CubiCasa가 unseen 최선(0.247)**. **v2(확장)은 seen·unseen 모두 악화**(unseen 0.247→0.279) — 확장 코퍼스의 노이즈가 일반화도 해침.

## 5. 해석·결론 [2026-06-05 갱신]
1. **CubiCasa 사전학습은 중립** — 전체 test v0 0.074=0.074, v2 0.104→0.102. unseen에서만 v0 소폭 이득(0.261→0.247, 분산도 작음). "음의 전이"는 저학습·단일시드 노이즈였음 재확인.
2. **V2V 확장 코퍼스(20,944) 파인튜닝은 생성 충실도를 악화** — adj_L1 0.074→0.104, unseen 0.261→0.279, **legal(off) 0.94→0.37**. V2V 예측 노이즈가 라벨 품질을 떨어뜨려, **`use`건수↑(+375, [[V2V.md]])가 생성품질↑로 이어지지 않음** — *수량≠품질*이 생성 단계에서도 확인.
3. → 생성모델 최선 레짐 = **clean v0 + CubiCasa 사전학습**. V2V 확장본을 학습에 쓰려면 **노이즈 필터(게이트 강화·예측 신뢰도 가중) 선행** 필요([[CORRECTION_GUIDE.md]] ④ 정밀화).
4. baseline(규칙기반)이 전체·seen에서 최저(최강) — 신경망 의의는 **다양성·신규성·unseen 강건성**.

## 6. 한계
- 사전학습셋 CubiCasa 3,018장 소규모 → 이득 상한 미확인. unseen 표본 31장으로 작음.
- 평가는 AI-Hub 동결 test 한정.

## 7. 후속 (재학습 시 §3·§4 표에 행 추가, 최종값만)
- 🔜 **v3 = +RPLAN 80,371 사전학습**(2026-06-04 전량 변환완료, CubiCasa의 ≈27배). `train_gen --pretrain`에 RPLAN을 물려 동일 다중시드 매트릭스 재실행 → 사전학습 규모가 unseen 이득을 키우는지 확인.
- 글로벌(RPLAN+CubiCasa) 합산 사전학습도 후보.
