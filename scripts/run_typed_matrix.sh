#!/bin/bash
# type조건 생성기(set-transformer-typed) 풀 학습 — v0 × 5시드. GPU0(우리 작업, v3/v4는 GPU1).
#   eval은 house_type 인지 이관(소비자) 후 별도 — 여기선 모델만 학습(runs/<run_id>/checkpoint.pt).
#   사용: nohup bash scripts/run_typed_matrix.sh > logs/typed_matrix.log 2>&1 &
cd ~/plan2graph || exit 1
MM="$HOME/bin/micromamba run -n p2g"
export CUDA_VISIBLE_DEVICES=0 PYTHONPATH=src
echo "[$(date +%H:%M:%S)] TYPED matrix v0 × 5시드 (GPU0)"
for S in 42 1 2 3 4; do
  echo "[$(date +%H:%M:%S)] typed v0 seed=$S"
  $MM python -m plan2graph.generators.typed train --finetune v0 --epochs 100 --seed "$S" >/dev/null 2>&1
done
echo "[$(date +%H:%M:%S)] ALL DONE (typed v0)"
