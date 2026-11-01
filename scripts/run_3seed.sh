#!/bin/bash
# 3-seed 재실험 드라이버 — GPU2 순차 실행 (nohup 용).
# 1) 기존 모델(K42·RK42·R) n=1000 평가 → 2) K/RK seed43·44 학습 → 3) 신규 4모델 평가
set -e
cd /home/ju/plan2graph
PY=/home/ju/.local/share/mamba/envs/p2g/bin/python
export CUDA_VISIBLE_DEVICES=2
export PYTHONPATH=src
LOG=results_3seed
mkdir -p $LOG

KV=data/staging/tokens_korean_gated/vocab.json
RV=data/staging/tokens_rplan/vocab.json
EV_K="--vocab $KV --n 1000 --seed 42 --country 0 --constrained --orthogonal --aspect-max 10 --fp-fill 0.60 --fp-convex 0.70"
EV_R="--vocab $RV --n 1000 --seed 42 --country 1 --constrained --orthogonal --aspect-max 8 --fp-fill 0.60 --fp-convex 0.75"

echo "[$(date)] PHASE1: 기존 모델 n=1000 평가"
$PY scripts/eval_3seed.py $EV_K --ckpt ckpts/korplan_ar_k_gated_targetonly_ep80.pt   --json-out $LOG/k_s42.json  > $LOG/eval_k_s42.log 2>&1
$PY scripts/eval_3seed.py $EV_K --ckpt ckpts/korplan_ar_rk_gated_seed42_roomperm_ep180.pt --json-out $LOG/rk_s42.json > $LOG/eval_rk_s42.log 2>&1
$PY scripts/eval_3seed.py $EV_R --ckpt ckpts/korplan_ar_r_roomperm_seed42_ep80.pt    --json-out $LOG/r_s42.json  > $LOG/eval_r_s42.log 2>&1
echo "[$(date)] PHASE1 done"

TR="--data data/staging/tokens_korean_gated/train.jsonl --vocab $KV --batch 32 --lr 1e-4 --d-model 512 --n-layer 24 --n-head 32 --max-len 1152 --diag-every 10 --ckpt-every 10 --constrained --orthogonal --dim-ff 1408 --grad-ckpt --amp --country 0 --room-perm --room-perm-prob 0.5"

for S in 43 44; do
  echo "[$(date)] PHASE2: K seed$S 학습 (100ep)"
  $PY scripts/train_wall_cycle.py $TR --epochs 100 --seed $S \
      --out ckpts/korplan_ar_k_gated_targetonly_s$S.pt > $LOG/train_k_s$S.log 2>&1
  echo "[$(date)] PHASE2: RK seed$S 학습 (FT 100ep, base ep80)"
  $PY scripts/train_wall_cycle.py $TR --epochs 180 --seed $S \
      --resume ckpts/korplan_ar_r_roomperm_seed42_ep80.pt \
      --out ckpts/korplan_ar_rk_gated_roomperm_s$S.pt > $LOG/train_rk_s$S.log 2>&1
done

echo "[$(date)] PHASE3: 신규 4모델 n=1000 평가"
for S in 43 44; do
  $PY scripts/eval_3seed.py $EV_K --ckpt ckpts/korplan_ar_k_gated_targetonly_s${S}_ep80.pt  --json-out $LOG/k_s$S.json  > $LOG/eval_k_s$S.log 2>&1
  $PY scripts/eval_3seed.py $EV_K --ckpt ckpts/korplan_ar_rk_gated_roomperm_s${S}_ep180.pt --json-out $LOG/rk_s$S.json > $LOG/eval_rk_s$S.log 2>&1
done
echo "[$(date)] DONE_ALL"
