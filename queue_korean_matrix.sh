#!/bin/bash
# 한국 비교 매트릭스 큐 — v2(RPLAN pretrain) 종료 후 4개 순차 학습.
# target-only / RPLAN→FT  ×  nosnap / snap. 모두 동일 레시피(d512/L24/H32/dimff1408).
cd /home/ju/plan2graph
PY=/home/ju/.local/share/mamba/envs/p2g/bin/python
export PYTHONPATH=src
V2_PID=1759385
PRETRAIN=ckpts/korplan_ar_r_fmlm80m_pretrain_v2_ep50.pt

echo "[queue] v2(pid $V2_PID) 종료 대기 시작 $(date)"
while kill -0 $V2_PID 2>/dev/null; do sleep 60; done
echo "[queue] v2 종료 감지 $(date)"
# 안전: pretrain ep50 ckpt 존재 확인(FT용)
for i in 1 2 3 4 5 6 7 8 9 10; do [ -f "$PRETRAIN" ] && break; sleep 30; done

COMMON="--batch 32 --d-model 512 --n-layer 24 --n-head 32 --max-len 1152 --dim-ff 1408 \
  --lr 1e-4 --constrained --orthogonal --grad-ckpt --amp --diag-every 10 --ckpt-every 10 --country 0"

run() {  # $1=dataset $2=out $3=epochs $4=resume(optional)
  local data=$1 out=$2 epochs=$3 resume=$4
  local R=""; [ -n "$resume" ] && R="--resume $resume"
  echo "[train] $out (data=$data epochs=$epochs $R) $(date)"
  CUDA_VISIBLE_DEVICES=1 $PY scripts/train_wall_cycle.py \
    --data data/staging/$data/train.jsonl --vocab data/staging/$data/vocab.json \
    $COMMON --epochs $epochs --out ckpts/$out $R \
    > logs_${out%.pt}.log 2>&1
  echo "[done] $out $(date)"
}

# target-only (fresh, 50ep)
run tokens_korean_clean_nosnap korplan_ar_k_nosnap.pt 50
run tokens_korean_clean_snap   korplan_ar_k_snap.pt   50
# RPLAN→FT (resume ep50, +50ep → ep100)
run tokens_korean_clean_nosnap korplan_ar_rk_nosnap.pt 100 "$PRETRAIN"
run tokens_korean_clean_snap   korplan_ar_rk_snap.pt   100 "$PRETRAIN"

echo "[queue] 매트릭스 4개 완료 $(date)"
