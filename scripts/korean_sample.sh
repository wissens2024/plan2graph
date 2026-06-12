#!/bin/bash
# korean_sample.sh — 학습된 엔진으로 샘플링(node->adjacency->partitioning) → 엔진출력 JSON.
# 그 뒤 diffplanner_to_cadrender.py(또는 GUI 📗 도면생성)로 도면+DXF.
#
# 사용: bash korean_sample.sh <VARIANT> <ARM> [N] [BS]
#   VARIANT = 데이터셋 구성(ckpt_kr/<VARIANT>/...).  ARM = finetune | korean_only
#   예:  bash korean_sample.sh korean finetune 200
#
# 출력: output/out_korean/<VARIANT>_<ARM>.json (체인 누적). 데이터=해당 VARIANT의 test.
# 체크포인트는 ckpt_kr/<VARIANT>/<stage>/<ARM>/, 없으면 레거시 flat ckpt_kr/<stage>/<ARM>
# (현재 진행 중인 첫 korean 런이 flat). GPU1.
set -e
VARIANT=${1:-korean}
ARM=${2:-finetune}
N=${3:-1000}
BS=${4:-256}
PY=/home/ju/.local/share/mamba/envs/p2g/bin/python
ROOT=~/diffplanner_work
export CUDA_VISIBLE_DEVICES=1
export DIFFPLANNER_DATA_DIR=../../dataset/dataset_json_$VARIANT   # 로더·name2index 통일
OUTDIR=$ROOT/output/out_korean; mkdir -p $OUTDIR
SYN=$OUTDIR/${VARIANT}_${ARM}.json

# 체크포인트: variant 우선, 없으면 flat(legacy 첫 korean 런)
ck () {
  local sd=$1
  local c=$(ls -1v $ROOT/ckpt_kr/$VARIANT/$sd/$ARM/*model*.pt 2>/dev/null | grep -vE "ema_|opt[0-9]" | tail -1)
  [ -z "$c" ] && c=$(ls -1v $ROOT/ckpt_kr/$sd/$ARM/*model*.pt 2>/dev/null | grep -vE "ema_|opt[0-9]" | tail -1)
  echo "$c"
}
NODE=$(ck node_diff); ADJ=$(ck adjacency_diff); PART=$(ck partitioning_diff)
echo "VARIANT=$VARIANT ARM=$ARM N=$N"
echo "  node=$NODE"; echo "  adj=$ADJ"; echo "  part=$PART"
[ -z "$NODE" ] && { echo "체크포인트 없음 — 학습 먼저(gate2_train_runbook.sh $VARIANT)"; exit 1; }

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
echo "다음: GUI 📗 도면생성에서 '$VARIANT $ARM' 선택, 또는"
echo "  PYTHONPATH=~/plan2graph/src $PY ~/plan2graph/scripts/diffplanner_to_cadrender.py --engine-json $SYN --n 8 --out /tmp/p2g_${VARIANT}_${ARM}"
