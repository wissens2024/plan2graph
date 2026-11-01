#!/bin/bash
# 부분 재생성 파일럿 드라이버 — 세션과 분리되어 단독 완주한다 (nohup/setsid 용).
# RK seed42(ep180) 기존 체크포인트 사용 → 재학습 불필요.
cd /home/ju/plan2graph
PY=/home/ju/.local/share/mamba/envs/p2g/bin/python
export CUDA_VISIBLE_DEVICES=3
export PYTHONPATH=src
OUT=results_pilot
mkdir -p $OUT

KV=data/staging/tokens_korean_gated/vocab.json
CK=ckpts/korplan_ar_rk_gated_seed42_roomperm_ep180.pt

echo "[$(date)] PILOT START — partial regeneration vs independent resampling"

$PY scripts/pilot_partial_regen.py \
    --ckpt $CK --vocab $KV \
    --trials 200 --max-attempts 10 --rollback-patience 3 \
    --seed 42 --country 0 \
    --aspect-max 10 --fp-fill 0.60 --fp-convex 0.70 \
    --json-out $OUT/pilot_rollback_rk_s42.json > $OUT/pilot_rollback.log 2>&1

echo "[$(date)] PILOT DONE rc=$?"
touch $OUT/PILOT_COMPLETE
