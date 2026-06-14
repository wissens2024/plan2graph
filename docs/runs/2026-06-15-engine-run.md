# 핸드오프 — 기하(도면) 생성 40k 학습 + 결과 회수 (2026-06-15)

> **다음 세션용 인계.** 설계는 [adr/README.md](../adr/README.md) 정본 따름(재설계·새 엔진 제안 금지 — 진행 중인 이 한 가지를 끝까지). 이 문서 = *돌고 있는 작업의 상태 + 결과 회수법*.

## 1. 지금 돌고 있는 것 (검증, 2026-06-15 08:18 기준)
- **DiffPlanner 기하 엔진을 한국 데이터(`dataset_json_korean`, 23,706)로 *맨바닥* 40k 학습 중.** 모델명 `korean`. ckpt = `~/diffplanner_work/ckpt_kr/korean/<stage>/`.
- 3스테이지(node→adjacency→partitioning) 각 40k. 진행: node **~14k/40k**. 속도 ~13k step/h → **총 ~8~10h, 오늘 저녁 완료 예상**.
- 명령: `FT_STEPS=40000 bash gate2_train_runbook.sh korean none` (서버 115, GPU1).
- **자동 후속 감시자** `/tmp/_posttrain_korean.sh`(setsid 가동 중): 40k 완료 감지 → 샘플 40 + 렌더 8 → `/tmp/p2g_korean40k_*.png` 생성 + 마커 `/tmp/p2g_korean40k.DONE`.

## 2. 왜 맨바닥인가 (= "RPLAN 전이 불가, 8k→40k")
- 전이학습(지름길): 글로벌 RPLAN 사전학습 → 한국 파인튜닝. **그러나 RPLAN=방8/종류6, 한국=방18/종류13 → 임베딩 크기 불일치로 RPLAN 가중치를 한국 모델에 못 얹음**(size mismatch 확인, [[diffplanner-rplan-transfer-blocked]]). → 전이 불가.
- 그래서 사전학습 없이 **한국만 처음부터**, 기존 얕은 8k보다 **5배 깊게(40k)** = "더 오래 학습하면 응집되나" 테스트.

## 3. ★ 결과 회수법 (완료되면 사용자에게 *이미지로* 보여줄 것)
```bash
# (1) 끝났나
ssh ju@sse.aines.kr "cat /tmp/p2g_korean40k.DONE 2>/dev/null || echo '아직 진행중'"
# (2) 됐으면 이미지 가져오기
scp ju@sse.aines.kr:/tmp/p2g_korean40k_*.png /c/dev/workspace/plan2graph/artifacts/
# (3) 감시자가 죽었으면 수동 샘플·렌더
ssh ju@sse.aines.kr "cd ~/diffplanner_work && bash korean_sample.sh korean korean 40 40 && \
  PYTHONPATH=~/plan2graph/src /home/ju/.local/share/mamba/envs/p2g/bin/python \
  ~/plan2graph/scripts/diffplanner_to_cadrender.py --engine-json output/out_korean/korean.json --n 8 --out /tmp/p2g_korean40k"
```
→ 대표 2~3장을 `docs/for_review/`에 커밋(영구 보존) + **사용자에게 렌더 이미지로 보고**(문서 말고 그림으로).

## 4. 결과 해석 (판독 기준)
- 이전 8k = 비응집(방 산포·경계 이탈·중복). 비교용 이미지: `docs/for_review/run_2026-06-15_sample{2,4}.png`.
- 40k가 **크게 응집되면** → 깊이 부족이 주원인 → 더 학습/스케일 업.
- **여전히 비응집이면** → 박스/산포 표현 한계 확정 → 다음은 **wall-cycle 표현 트랙**(ADR-0012, 설계 그대로 — 새 엔진 아님).

## 5. 검증된 사실 (재실험 금지 — 공부용)
- RPLAN→한국 전이 불가(차원). 사전학습은 *위상모델*에선 무익 측정됨(EXPERIMENTS §4.3/§8). 박스 회귀 사망(ADR-0006). 상세 = [ARCHITECTURE.md](../ARCHITECTURE.md) §2.
- git: `local=origin=server=770ad06`(2026-06-15). 릴리스 parsed/corrected 이전 완료.
