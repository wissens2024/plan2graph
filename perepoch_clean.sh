#!/bin/bash
# RPLAN v2 사전학습 per-epoch clean율 측정 (GPU0, 200W 캡). ep10~50로 과학습/평탄 확인.
cd /home/ju/plan2graph
PY=/home/ju/.local/share/mamba/envs/p2g/bin/python
export PYTHONPATH=src
for ep in 10 20 30 40 50; do
  CK=ckpts/korplan_ar_r_fmlm80m_pretrain_v2_ep${ep}.pt
  echo "##### ep${ep} ##### $(date +%H:%M:%S)"
  CUDA_VISIBLE_DEVICES=0 $PY scripts/eval_ar_geom.py \
    --ckpt $CK --vocab data/staging/tokens_rplan/vocab.json \
    --n 100 --render 0 --constrained --orthogonal --country 1 \
    --out /tmp/perepoch_ep${ep}.png 2>&1 | grep -E 'clean|selfint|overlap|single|rooms|decoded'
done
echo "##### DONE $(date +%H:%M:%S) #####"
