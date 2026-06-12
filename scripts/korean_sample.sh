#!/bin/bash
# korean_sample.sh — 학습된 한국형 엔진으로 샘플링(node->adjacency->partitioning 체인)
# → 엔진 출력 JSON. 그 뒤 diffplanner_to_cadrender.py로 도면+DXF.
#
# 사용: bash korean_sample.sh <ARM> [N] [BS]
#   ARM = finetune | korean_only | pretrain  (ckpt_kr/<stage>/<ARM>/ 에서 체크포인트)
#   N   = 샘플 수(기본 1000)   BS = 배치(기본 256)
# 예:  bash korean_sample.sh finetune 200
#
# 체인: node "" → adjacency "ncsl" → partitioning "ncsla" (검증 레시피).
# 세 stage가 같은 syn JSON(out_korean/<ARM>.json)에 누적, 최종 = 방(역할·박스)+인접+외곽.
# 데이터=한국 test(dataset_json_korean). GPU1.
set -e
ARM=${1:-finetune}
N=${2:-1000}
BS=${3:-256}
PY=/home/ju/.local/share/mamba/envs/p2g/bin/python
ROOT=~/diffplanner_work
export CUDA_VISIBLE_DEVICES=1
export DIFFPLANNER_DATA_DIR=../../dataset/dataset_json_korean   # 로더·name2index 통일
CKPT=$ROOT/ckpt_kr
OUTDIR=$ROOT/output/out_korean
mkdir -p $OUTDIR
SYN=$OUTDIR/${ARM}.json   # 체인 누적 파일(절대경로)

last_ckpt () { ls -1v $CKPT/$1/$ARM/*model*.pt 2>/dev/null | grep -vE "ema_|opt[0-9]" | tail -1; }
NODE_CKPT=$(last_ckpt node_diff); ADJ_CKPT=$(last_ckpt adjacency_diff); PART_CKPT=$(last_ckpt partitioning_diff)
echo "ARM=$ARM N=$N  node=$NODE_CKPT  adj=$ADJ_CKPT  part=$PART_CKPT"
[ -z "$NODE_CKPT" ] && { echo "체크포인트 없음(아직 학습 안 끝남): $CKPT/node_diff/$ARM/"; exit 1; }

echo "=== STAGE 1 node ($(date +%T)) ==="
cd $ROOT/node_diff/scripts
$PY sample.py --dataset rplan --batch_size $BS --set_name test --model_path "$NODE_CKPT" \
  --num_samples $N --support_boundary True --support_conditions "" --support_partial False
cp ../../output/output_json/b.json "$SYN"      # node 출력(b.json) → 체인 시작

echo "=== STAGE 2 adjacency ($(date +%T)) ==="
cd $ROOT/adjacency_diff/scripts
$PY sample.py --dataset rplan --batch_size $BS --set_name test --model_path "$ADJ_CKPT" \
  --num_samples $N --support_boundary True --support_conditions "ncsl" --support_partial False \
  --syn_dataset_path "$SYN"

echo "=== STAGE 3 partitioning ($(date +%T)) ==="
cd $ROOT/partitioning_diff/scripts
$PY sample.py --dataset rplan --batch_size $BS --set_name test --model_path "$PART_CKPT" \
  --num_samples $N --support_boundary True --support_conditions "ncsla" --support_partial False \
  --syn_dataset_path "$SYN"

echo "=== 최종 엔진출력: $SYN ==="
echo "다음: PYTHONPATH=~/plan2graph/src $PY ~/plan2graph/scripts/diffplanner_to_cadrender.py --engine-json $SYN --n 8 --out /tmp/p2g_${ARM}"
