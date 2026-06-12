#!/bin/bash
# gate2_train_runbook.sh — 한국형 소버린 엔진 학습 런북 (ADR-0006/0007, Gate 2)
#
#   사용: bash gate2_train_runbook.sh <VARIANT>     (기본 korean)
#   VARIANT = 데이터셋 구성 이름. dataset/dataset_json_<VARIANT> 를 학습,
#             체크포인트는 ckpt_kr/<VARIANT>/<stage>/<arm>/ 에 저장.
#   구성 만들기: scripts/korean_to_engine.py --variant <VARIANT> [--provenance ...] [--all]
#
# **데이터셋 구성이 곧 실험 변수**: 같은 13/18 아키텍처를, 다른 구성(온전만/＋보정/
# dual만/＋V2V/소스믹스)으로 학습하면 전혀 다른 모델이 나온다. 그 도면 품질 차이가
# 비교의 본체(단순 T∥G 2개가 아니라 구성×기하모델×사전학습 매트릭스).
#
# ARM:
#   ARM-A  RPLAN 사전학습(13/18) → 해당 VARIANT 파인튜닝   (전이학습)
#   ARM-B  해당 VARIANT 만(사전학습 없음)                  (none 베이스라인)
# ⚠️ ARM-A 사전학습(RPLAN)은 VARIANT 무관 → 한 번 한 뒤 _pretrain/ 재사용 권장.
#    (아래는 자기완결: _pretrain 있으면 재사용, 없으면 새로 사전학습.)
#
# GPU1만(GPU0=WiSentinel). 한 stage씩 순차. 기존 b/bncsl/bncsla 레시피 유지.
set -e
VARIANT=${1:-korean}
PY=/home/ju/.local/share/mamba/envs/p2g/bin/python
ROOT=~/diffplanner_work
export CUDA_VISIBLE_DEVICES=1
RPLAN_DIR=../../dataset/dataset_json
VAR_DIR=../../dataset/dataset_json_$VARIANT
PRE=$ROOT/ckpt_kr/_pretrain                 # RPLAN 사전학습(공유)
CK=$ROOT/ckpt_kr/$VARIANT                    # 이 구성의 파인튜닝/단독

[ -d "$ROOT/dataset/dataset_json_$VARIANT" ] || {
  echo "데이터셋 없음: dataset_json_$VARIANT — 먼저 korean_to_engine.py --variant $VARIANT"; exit 1; }

PRE_STEPS=${PRE_STEPS:-150000}; FT_STEPS=${FT_STEPS:-40000}; BS=${BS:-512}
echo "VARIANT=$VARIANT  PRE_STEPS=$PRE_STEPS FT_STEPS=$FT_STEPS BS=$BS"

run_stage () {  # $1=stage_dir $2=cond
  local sd=$1 cond=$2
  echo "######## STAGE $sd  cond='$cond'  VARIANT=$VARIANT ########"
  cd $ROOT/$sd/scripts
  local pre_ck=$(ls -1v $PRE/$sd/*model*.pt 2>/dev/null | grep -vE "ema_|opt[0-9]" | tail -1)

  if [ -z "$pre_ck" ]; then                  # 공유 사전학습 없으면 RPLAN으로 1회
    OPENAI_LOGDIR=$PRE/$sd DIFFPLANNER_DATA_DIR=$RPLAN_DIR \
      $PY train.py --dataset rplan --batch_size $BS --set_name train \
      --support_boundary True --support_conditions "$cond" --support_partial False \
      --lr 1e-4 --save_interval 10000 --lr_anneal_steps $PRE_STEPS
    pre_ck=$(ls -1v $PRE/$sd/*model*.pt 2>/dev/null | grep -vE "ema_|opt[0-9]" | tail -1)
  fi

  # ARM-A: 사전학습 resume → VARIANT 파인튜닝
  OPENAI_LOGDIR=$CK/$sd/finetune DIFFPLANNER_DATA_DIR=$VAR_DIR \
    $PY train.py --dataset rplan --batch_size $BS --set_name train \
    --support_boundary True --support_conditions "$cond" --support_partial False \
    --lr 5e-5 --save_interval 10000 --lr_anneal_steps $FT_STEPS --resume_checkpoint "$pre_ck"

  # ARM-B: VARIANT 단독(사전학습 없음)
  OPENAI_LOGDIR=$CK/$sd/korean_only DIFFPLANNER_DATA_DIR=$VAR_DIR \
    $PY train.py --dataset rplan --batch_size $BS --set_name train \
    --support_boundary True --support_conditions "$cond" --support_partial False \
    --lr 1e-4 --save_interval 10000 --lr_anneal_steps $FT_STEPS
}

run_stage node_diff         ""
run_stage adjacency_diff    "ncsl"
run_stage partitioning_diff "ncsla"

echo "DONE [$VARIANT] — 체크포인트: $CK/<stage>/{finetune,korean_only}/ (사전학습 공유: $PRE)"
echo "다음: bash korean_sample.sh $VARIANT finetune 200  → 도면 렌더·비교"
