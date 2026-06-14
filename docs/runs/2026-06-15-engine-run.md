# 엔진 실행 기록 — 2026-06-15 (도면 생성 끝까지 돌리기)

> 목적: "네이티브 생성기 재작성 → 사전학습 → 파인튜닝 → 도면 생성 확인"을 끝까지 실행하고
> **실제 결과를 정직하게** 기록(좋든 나쁘든). 관련: [ADR-0012](../adr/0012-generation-target-opening-tokens-eval-order.md), [정본 README](../adr/README.md).

## 0. 결론 요약 (TL;DR)
1. **네이티브 wall-cycle 생성기 = 오늘 불가** — 서버에 기하 데이터(`geom.jsonl`)·모델 둘 다 0. wall-cycle 데이터 표현부터 지어야 하는 연구 빌드(여러 세션). → 별도 트랙.
2. **DiffPlanner(베이스라인)로 실제 한국 도면 생성·렌더 = 성공(파이프라인)**. 방·문·창·가구·실명·면적·DXF까지 완전 출력. 단 **배치가 비응집**(방 산포·경계 이탈·중복·겹침) = 쓸 수 있는 도면 아님.
3. **RPLAN→한국 전이학습 = 차원 불일치로 막힘**(아래 §3). DiffPlanner의 구조적 한계 확인 → ADR-0012 네이티브 엔진 근거 강화.
4. **개선 시도**: 한국 데이터(23,706)로 40k 깊이 학습 백그라운드 가동(전이 없이). 완료 후 재샘플·재렌더 예정.

## 1. 실행한 것 (DiffPlanner, 서버 115 GPU1)
- 엔진: `~/diffplanner_work`, 3스테이지 디퓨전(node→adjacency→partitioning), 13역할/18방.
- 체크포인트: `ckpt_kr/aihub_g_dual`(RPLAN 사전학습 시도 없이 한국 맨바닥 **8,000 step**, 얕음).
- 샘플 40개: `korean_sample.sh aihub_g_dual aihub_g_dual 40` → `output/out_korean/aihub_g_dual.json`.
- 렌더 8개: `diffplanner_to_cadrender.py` → PNG+DXF (`/tmp/p2g_aihub_g_dual_*`).

## 2. 현재 도면 품질 (8k 체크포인트, 눈으로 확인)
샘플별 자기교정 잔여 이슈: 3·11·3·8·3·9·11·13.

**작동 ✅**: 렌더 파이프라인 완전 — 방 폴리곤·문·창·가구(검출+추론)·실명·면적(㎡)·DXF 레이어. cadrender·DXF 정상.

**실패 ❌ (대표 샘플 `_2`,`_4` 이미지 확인)**:
- 방들이 **외곽 경계 밖으로 산포**(안방·욕실·침실이 집 밖에 떠 있음).
- **중복 방**(주방×2, 화장실/욕실 중복), 가구 겹침(냉장고×2).
- 거실이 비현실적 대형(34~59㎡), 방 타일링 안 됨 → **한 채 아파트로 안 보임**.
- 원인 = ①8k 얕은 학습 + ②박스/산포 표현(겹침·경계이탈) = ADR-0012가 지목한 그대로.

이미지: `docs/for_review/run_2026-06-15_sample{2,4}.png`.

## 3. 핵심 발견 — RPLAN 사전학습 전이가 막힘 (재현된 에러)
`gate2_train_runbook.sh korean rplan`로 RPLAN 300k → 한국 파인튜닝 시도 시 **즉시 실패**:
```
size mismatch for condition_number_emb.weight:   ckpt [512, 8]  vs model [512, 18]
size mismatch for condition_category_emb.weight: ckpt [512, 6]  vs model [512, 13]
```
- RPLAN = **8방 / 6범주**, 한국 = **18방 / 13역할** → 임베딩 테이블 크기가 달라 **가중치를 그대로 못 올림**.
- = ADR-0007/메모리의 *"DiffPlanner 8방 한도 = 한국 부적합"* 의 구체 증거.
- 의미: **DiffPlanner로는 "RPLAN 사전학습→한국 파인튜닝"이 그냥은 불가.** 부분로딩(임베딩 재초기화) 코드 surgery가 있어야 함. 그래서 기존 8k도 사실상 한국 맨바닥 학습이라 얕고 거칢.
- → **ADR-0012 네이티브 엔진(고정 8/6 차원에 안 묶임)의 근거 강화.**

## 4. 진행 중 — 한국 단독 40k 학습 (전이 없이)
- `FT_STEPS=40000 gate2_train_runbook.sh korean none` (data=`dataset_json_korean` 23,706, BS 512, 3스테이지).
- 시작 확인: node_diff step 590, loss 0.0099 하강, GPU1 51%. 체크포인트 10k 간격.
- 예상 수 시간(40k×3스테이지). **완료 후 재샘플·재렌더해 응집도 개선 여부 보고.**
- 판독 기준: 크게 개선되면 "깊이 부족"이 주원인 / 여전히 비응집이면 "박스 표현"이 주원인(ADR-0012 확증).

## 5. 다음
- (단기) 40k 학습 완료 → 샘플·렌더·품질 재평가.
- (중기) 부분로딩으로 RPLAN 전이 복구 시도 여부 판단(또는 한국 단독으로 충분한지).
- (본질, ADR-0012) 네이티브 wall-cycle+opening 생성기: ① g-0.4→wall-cycle 데이터 표현 구축 ② v1 모델 ③ 미니셋 collapse 진단. 여러 세션 트랙.
