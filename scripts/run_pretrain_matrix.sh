#!/bin/bash
# v3/v4 사전학습 매트릭스 — global_rplan(v3)·global_all(v4) × 5시드 × finetune v0.
# noPretrain 재실행 회피(이미 완료). 각 학습 직후 eval+일반화+dwelling → runs/index.jsonl.
#   사전: recipes/global_rplan.json·global_all.json 존재. GPU1만(운영 GPU0 보호).
#   사용: nohup bash scripts/run_pretrain_matrix.sh > logs/v34_matrix.log 2>&1 &
cd ~/plan2graph || exit 1
MM="$HOME/bin/micromamba run -r /home/ju/.local/share/mamba -n p2g"
export CUDA_VISIBLE_DEVICES=1 PYTHONPATH=src
echo "[$(date +%H:%M:%S)] freeze global_rplan"
$MM python -m plan2graph.release global_rplan 2>&1 | python3 -c "import sys,json;d=json.load(sys.stdin);print('  global_rplan',d['per_source'],d['splits'])" || { echo "freeze rplan FAIL"; exit 1; }
echo "[$(date +%H:%M:%S)] freeze global_all"
$MM python -m plan2graph.release global_all 2>&1 | python3 -c "import sys,json;d=json.load(sys.stdin);print('  global_all',d['per_source'],d['splits'])" || { echo "freeze all FAIL"; exit 1; }
ev(){ local S=$1
  $MM python -m plan2graph.eval_gen --versions v0 --seed "$S" >/dev/null 2>&1
  $MM python -m plan2graph.eval_gen --version v0 --seed "$S" --generalization >/dev/null 2>&1
  $MM python -m plan2graph.eval_gen --version v0 --seed "$S" --dwelling >/dev/null 2>&1
}
for PRE in global_rplan global_all; do
  for S in 42 1 2 3 4; do
    echo "[$(date +%H:%M:%S)] pretrain=$PRE seed=$S"
    $MM python -m plan2graph.train_gen train --pretrain "$PRE" --finetune v0 --pretrain-epochs 50 --epochs 100 --seed "$S" >/dev/null 2>&1
    ev "$S"
  done
done
echo "[$(date +%H:%M:%S)] ALL DONE (v3/v4)"
