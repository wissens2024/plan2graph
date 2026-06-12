#!/bin/bash
# gate2_train_runbook.sh <FT_DS> [PRE_DS] — 한국형 엔진 학습 (ADR-0006/0007, Gate 2)
#
#   FT_DS  = ② 파인튜닝 데이터셋 이름  (또는 none)
#   PRE_DS = ① 사전학습 데이터셋 이름  (또는 none, 기본 rplan)
#   데이터셋 디렉터리: rplan→dataset/dataset_json · 그 외→dataset/dataset_json_<name>
#
#   콤보 정규화(옛 화면과 동일):
#     ① pre + ② ft  → 2단계: pre 사전학습 → ft 파인튜닝   · 모델 <ft>_pre-<pre>
#     ② ft 만        → ft 단독 학습(사전학습 없음)         · 모델 <ft>
#     ① pre 만       → pre 단독 학습(파인튜닝 없음)         · 모델 <pre>
#   ckpt: 사전학습 공유 = ckpt_kr/_pretrain/<pre>/<stage>/ · 본학습 = ckpt_kr/<model>/<stage>/
#
# 데이터셋 구성이 곧 실험 변수. 같은 13/18 엔진을 다른 조합으로 → 다른 모델 → 도면 차이.
# GPU1만(GPU0=WiSentinel). stage 레시피: node ""·adjacency ncsl·partitioning ncsla.
set -e
FT=${1:-korean}; PRE=${2:-rplan}
PY=/home/ju/.local/share/mamba/envs/p2g/bin/python
ROOT=~/diffplanner_work
export CUDA_VISIBLE_DEVICES=1
PRE_STEPS=${PRE_STEPS:-150000}; FT_STEPS=${FT_STEPS:-40000}; BS=${BS:-512}

ddir(){ if [ "$1" = rplan ]; then echo "../../dataset/dataset_json";
        else echo "../../dataset/dataset_json_$1"; fi; }
exists(){ [ -d "$ROOT/dataset/$(basename $(ddir $1))" ]; }

# 정규화 → VER(본학습 데이터) · PTR(사전학습 데이터 or none)
if [ "$FT" != none ] && [ "$PRE" != none ]; then VER=$FT; PTR=$PRE
elif [ "$FT" != none ]; then VER=$FT; PTR=none
elif [ "$PRE" != none ]; then VER=$PRE; PTR=none
else echo "①②둘다 '없음' — 학습할 데이터 없음"; exit 1; fi
MODEL=$VER; [ "$PTR" != none ] && MODEL=${VER}_pre-${PTR}
exists $VER || { echo "데이터셋 없음: $(ddir $VER) — korean_to_engine.py --variant $VER 먼저"; exit 1; }
echo "조합: 사전학습=$PRE 파인튜닝=$FT → VER=$VER PTR=$PTR  모델=$MODEL"
echo "PRE_STEPS=$PRE_STEPS FT_STEPS=$FT_STEPS BS=$BS"

last(){ ls -1v "$1"/*model*.pt 2>/dev/null | grep -vE "ema_|opt[0-9]" | tail -1; }

run_stage(){  # $1=stage_dir $2=cond
  local sd=$1 cond=$2
  echo "######## STAGE $sd cond='$cond' 모델=$MODEL ########"
  cd $ROOT/$sd/scripts
  if [ "$PTR" != none ]; then
    local predir=$ROOT/ckpt_kr/_pretrain/$PTR/$sd
    local preck=$(last $predir)
    if [ -z "$preck" ]; then                       # 공유 사전학습(<pre>) 없으면 1회
      OPENAI_LOGDIR=$predir DIFFPLANNER_DATA_DIR=$(ddir $PTR) \
        $PY train.py --dataset rplan --batch_size $BS --set_name train \
        --support_boundary True --support_conditions "$cond" --support_partial False \
        --lr 1e-4 --save_interval 10000 --lr_anneal_steps $PRE_STEPS
      preck=$(last $predir)
    fi
    OPENAI_LOGDIR=$ROOT/ckpt_kr/$MODEL/$sd DIFFPLANNER_DATA_DIR=$(ddir $VER) \
      $PY train.py --dataset rplan --batch_size $BS --set_name train \
      --support_boundary True --support_conditions "$cond" --support_partial False \
      --lr 5e-5 --save_interval 10000 --lr_anneal_steps $FT_STEPS --resume_checkpoint "$preck"
  else                                             # 단독 학습(사전학습 없음)
    OPENAI_LOGDIR=$ROOT/ckpt_kr/$MODEL/$sd DIFFPLANNER_DATA_DIR=$(ddir $VER) \
      $PY train.py --dataset rplan --batch_size $BS --set_name train \
      --support_boundary True --support_conditions "$cond" --support_partial False \
      --lr 1e-4 --save_interval 10000 --lr_anneal_steps $FT_STEPS
  fi
}

run_stage node_diff         ""
run_stage adjacency_diff    "ncsl"
run_stage partitioning_diff "ncsla"

echo "DONE 모델=$MODEL — ckpt_kr/$MODEL/<stage>/"
echo "다음: bash korean_sample.sh $MODEL $VER 200  → 도면 렌더·비교"
