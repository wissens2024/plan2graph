#!/bin/bash
cd ~/plan2graph
while pgrep -f _orchestrate_rk_gated >/dev/null; do sleep 20; done
sleep 5
BEST=$(/home/ju/.local/share/mamba/envs/p2g/bin/python scripts/_best_ep.py)
CK=ckpts/korplan_ar_rk_gated_seed42_roomperm_ep${BEST}.pt
echo "[per-rule] best RK ep${BEST} -> ${CK}" > logs_legal_rules.log
CUDA_VISIBLE_DEVICES=1 PYTHONPATH=src:scripts /home/ju/.local/share/mamba/envs/p2g/bin/python \
  scripts/diag_legal_rules.py --ckpt "${CK}" --vocab data/staging/tokens_korean_gated/vocab.json \
  --n 200 --country 0 --seed 42 --out results_legal_rules_rk.md >> logs_legal_rules.log 2>&1
echo DONE >> logs_legal_rules.log
