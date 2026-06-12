#!/bin/bash
# gate2_train_runbook.sh — 한국형 소버린 엔진 학습 런북 (ADR-0006/0007, Gate 2)
#
# 아키텍처는 이미 한국형(13 category / 18 rooms)으로 확장·검증됨(gate2_patch_*).
# 데이터: RPLAN = dataset/dataset_json (사전학습) · 한국 온전 24,706 =
#         dataset/dataset_json_korean (파인튜닝). DIFFPLANNER_DATA_DIR로 전환.
#
# 3 stage 각각 독립 학습(샘플링 때 node->adjacency->partitioning 체인).
# support_conditions/모델명 = 기존 검증 레시피(b / bncsl / bncsla) 그대로.
#
# ⚠️ 기존 사전학습 가중치(b/bncsl/bncsla_model300000.pt)는 6/8 차원이라 13/18과
#    호환 안 됨 → resume 불가. 아래는 13/18로 **새로 사전학습**(RPLAN) 후 파인튜닝.
#
# 비교 매트릭스(소버린 엔진 논문용):
#   ARM-A  RPLAN 사전학습(13/18) → 한국 파인튜닝   (전이학습 가설)
#   ARM-B  한국만(13/18, 사전학습 없음)            (none 베이스라인)
#   → 같은 frozen test로 FID·법규준수·완성도 비교.
#
# GPU1만 사용(GPU0=WiSentinel). 한 스테이지씩 순차 실행 권장.
set -e
PY=/home/ju/.local/share/mamba/envs/p2g/bin/python
ROOT=~/diffplanner_work
export CUDA_VISIBLE_DEVICES=1
RPLAN_DIR=../../dataset/dataset_json
KOREAN_DIR=../../dataset/dataset_json_korean

# 스테이지별 (디렉터리, support_conditions, 모델접두) — 검증 레시피
# node:        ""      (b)
# adjacency:   ncsl    (bncsl)
# partitioning ncsla   (bncsla)

PRE_STEPS=${PRE_STEPS:-150000}     # 사전학습 스텝(원논문 300k, 가용시간 맞춰 조정)
FT_STEPS=${FT_STEPS:-40000}        # 파인튜닝 스텝
BS=${BS:-512}

run_stage () {  # $1=stage_dir $2=inner $3=cond
  local sd=$1 inner=$2 cond=$3
  echo "######## STAGE $sd  cond='$cond' ########"
  cd $ROOT/$sd/scripts

  # ---- ARM-A: 사전학습(RPLAN) ----
  OPENAI_LOGDIR=$ROOT/ckpt_kr/$sd/pretrain DIFFPLANNER_DATA_DIR=$RPLAN_DIR \
    $PY train.py --dataset rplan --batch_size $BS --set_name train \
    --support_boundary True --support_conditions "$cond" --support_partial False \
    --lr 1e-4 --save_interval 10000 --lr_anneal_steps $PRE_STEPS

  PRE_CKPT=$ROOT/ckpt_kr/$sd/pretrain/model$(printf "%06d" $PRE_STEPS).pt

  # ---- ARM-A: 파인튜닝(한국, 사전학습 resume) ----
  OPENAI_LOGDIR=$ROOT/ckpt_kr/$sd/finetune DIFFPLANNER_DATA_DIR=$KOREAN_DIR \
    $PY train.py --dataset rplan --batch_size $BS --set_name train \
    --support_boundary True --support_conditions "$cond" --support_partial False \
    --lr 5e-5 --save_interval 10000 --lr_anneal_steps $FT_STEPS \
    --resume_checkpoint $PRE_CKPT

  # ---- ARM-B: 한국만(사전학습 없음) ----
  OPENAI_LOGDIR=$ROOT/ckpt_kr/$sd/korean_only DIFFPLANNER_DATA_DIR=$KOREAN_DIR \
    $PY train.py --dataset rplan --batch_size $BS --set_name train \
    --support_boundary True --support_conditions "$cond" --support_partial False \
    --lr 1e-4 --save_interval 10000 --lr_anneal_steps $FT_STEPS
}

run_stage node_diff         node_diff         ""
run_stage adjacency_diff    adjacency_diff    "ncsl"
run_stage partitioning_diff partitioning_diff "ncsla"

echo "DONE — 체크포인트: $ROOT/ckpt_kr/<stage>/{pretrain,finetune,korean_only}/"
echo "다음: 한국 frozen test로 샘플링·렌더(cadrender) → T∥G·ARM-A∥B 비교"
