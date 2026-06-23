#!/bin/bash
# 신뢰성 매트릭스 — noPretrain × 5시드 + preCubicasa × 5시드 (버전 인자).
#   각 학습 직후 해당 시드 체크포인트로 eval + 일반화 진단 → 원장(runs/index.jsonl) 누적.
#   끝나면: python -m plan2graph.experiments agg  → 평균±표준편차로 '시드 노이즈' 판정.
#   GPU1만 사용(운영 GPU0 보호). 배치학습이라 버전당 ~40분.
#
#   사용:  bash scripts/run_matrix.sh [VERSION] [PRETRAIN]
#     VERSION  : 파인튜닝/평가 버전 (기본 v0)
#     PRETRAIN : 사전학습 풀 (기본 global_cubicasa)
#   예) bash scripts/run_matrix.sh v0
#       bash scripts/run_matrix.sh v2
cd ~/plan2graph || exit 1
MM="$HOME/bin/micromamba run -r /home/ju/.local/share/mamba -n p2g"
export CUDA_VISIBLE_DEVICES=1
export PYTHONPATH=src
VER="${1:-v0}"
PRE="${2:-global_cubicasa}"

# 해당 시드 체크포인트(gen_<VER>_seed<S>.pt)로 평가 — 다중시드 덮어쓰기 모호성 제거.
# eval(전체) + generalization(seen/unseen) + dwelling(APT/DEH/ROW 매크로 — 소버린 핵심).
ev() {
  local S="$1"
  $MM python -m plan2graph.eval_gen --versions "$VER" --seed "$S" >/dev/null 2>&1
  $MM python -m plan2graph.eval_gen --version "$VER" --seed "$S" --generalization >/dev/null 2>&1
  $MM python -m plan2graph.eval_gen --version "$VER" --seed "$S" --dwelling >/dev/null 2>&1
}

echo "[$(date +%H:%M:%S)] MATRIX ver=$VER pretrain=$PRE"
for S in 42 1 2 3 4; do
  echo "[$(date +%H:%M:%S)] noPretrain seed=$S"
  $MM python -m plan2graph.train_gen train --finetune "$VER" --epochs 100 --seed "$S" >/dev/null 2>&1
  ev "$S"
  echo "[$(date +%H:%M:%S)] pre_$PRE seed=$S"
  $MM python -m plan2graph.train_gen train --pretrain "$PRE" --finetune "$VER" \
       --pretrain-epochs 50 --epochs 100 --seed "$S" >/dev/null 2>&1
  ev "$S"
done
echo "[$(date +%H:%M:%S)] ALL DONE ($VER)"
