#!/bin/bash
# 신뢰성 매트릭스 — noPretrain × 5시드 + preCubicasa(v2 재검증) × 5시드.
#   각 학습 직후 eval + 일반화 진단을 돌려 원장(runs/index.jsonl)에 누적.
#   끝나면: python -m plan2graph.experiments agg  → 평균±표준편차로 '시드 노이즈' 판정.
#   GPU1만 사용(운영 GPU0 보호). 미배치 대비 배치학습이라 전체 ~40분.
cd ~/plan2graph || exit 1
MM="$HOME/bin/micromamba run -n p2g"
export CUDA_VISIBLE_DEVICES=1
export PYTHONPATH=src

ev() {
  $MM python -m plan2graph.eval_gen --versions v0 >/dev/null 2>&1
  $MM python -m plan2graph.eval_gen --version v0 --generalization >/dev/null 2>&1
}

for S in 42 1 2 3 4; do
  echo "[$(date +%H:%M:%S)] noPretrain seed=$S"
  $MM python -m plan2graph.train_gen train --finetune v0 --epochs 100 --seed "$S" >/dev/null 2>&1
  ev
  echo "[$(date +%H:%M:%S)] preCubicasa seed=$S"
  $MM python -m plan2graph.train_gen train --pretrain global_cubicasa --finetune v0 \
       --pretrain-epochs 50 --epochs 100 --seed "$S" >/dev/null 2>&1
  ev
done
echo "[$(date +%H:%M:%S)] ALL DONE"
