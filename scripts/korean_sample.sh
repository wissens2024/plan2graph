#!/bin/bash
# korean_sample.sh <MODEL_ID> <DATA_DS> [N] [BS] — 학습된 엔진으로 샘플링→엔진출력 JSON.
#   MODEL_ID = ckpt_kr/<MODEL_ID>/<stage>/ 의 모델 (예: korean_pre-rplan, korean, dual_pre-rplan)
#   DATA_DS  = 경계조건을 가져올 데이터셋 이름(보통 모델의 본학습 데이터). rplan→dataset_json
#   출력: output/out_korean/<MODEL_ID>.json. node→adjacency→partitioning 체인. GPU1.
set -e
MODEL=${1:-korean_pre-rplan}
DATA=${2:-korean}
N=${3:-1000}
BS=${4:-256}
PY=/home/ju/.local/share/mamba/envs/p2g/bin/python
ROOT=~/diffplanner_work
export CUDA_VISIBLE_DEVICES=1
if [ "$DATA" = rplan ]; then export DIFFPLANNER_DATA_DIR=../../dataset/dataset_json
else export DIFFPLANNER_DATA_DIR=../../dataset/dataset_json_$DATA; fi
OUTDIR=$ROOT/output/out_korean; mkdir -p $OUTDIR
SYN=$OUTDIR/${MODEL}.json

ck(){  # 모델 stage 체크포인트; korean_pre-rplan/korean은 레거시 flat 폴백
  local sd=$1
  local c=$(ls -1v $ROOT/ckpt_kr/$MODEL/$sd/*model*.pt 2>/dev/null | grep -vE "ema_|opt[0-9]" | tail -1)
  if [ -z "$c" ]; then
    local arm=finetune; [ "$MODEL" = korean ] && arm=korean_only
    c=$(ls -1v $ROOT/ckpt_kr/$sd/$arm/*model*.pt 2>/dev/null | grep -vE "ema_|opt[0-9]" | tail -1)
  fi
  echo "$c"
}
NODE=$(ck node_diff); ADJ=$(ck adjacency_diff); PART=$(ck partitioning_diff)
echo "MODEL=$MODEL DATA=$DATA N=$N"; echo "  node=$NODE"; echo "  adj=$ADJ"; echo "  part=$PART"
[ -z "$NODE" ] && { echo "체크포인트 없음 — 학습 먼저: gate2_train_runbook.sh"; exit 1; }

echo "=== STAGE 1 node ($(date +%T)) ==="
cd $ROOT/node_diff/scripts
$PY sample.py --dataset rplan --batch_size $BS --set_name test --model_path "$NODE" \
  --num_samples $N --support_boundary True --support_conditions "" --support_partial False
cp ../../output/output_json/b.json "$SYN"

echo "=== STAGE 2 adjacency ($(date +%T)) ==="
cd $ROOT/adjacency_diff/scripts
$PY sample.py --dataset rplan --batch_size $BS --set_name test --model_path "$ADJ" \
  --num_samples $N --support_boundary True --support_conditions "ncsl" --support_partial False \
  --syn_dataset_path "$SYN"

echo "=== STAGE 3 partitioning ($(date +%T)) ==="
cd $ROOT/partitioning_diff/scripts
$PY sample.py --dataset rplan --batch_size $BS --set_name test --model_path "$PART" \
  --num_samples $N --support_boundary True --support_conditions "ncsla" --support_partial False \
  --syn_dataset_path "$SYN"

echo "=== 최종 엔진출력: $SYN ==="
echo "다음: GUI 📗 도면생성에서 모델 '$MODEL' 선택 → 렌더, 또는"
echo "  PYTHONPATH=~/plan2graph/src $PY ~/plan2graph/scripts/diffplanner_to_cadrender.py --engine-json $SYN --n 8 --out /tmp/p2g_${MODEL}"
