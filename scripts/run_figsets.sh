#!/bin/bash
# R / K / RK 도면 세트 생성 — 박재완 연구자 요청분. GPU2 (파일럿은 GPU3).
cd /home/ju/plan2graph
PY=/home/ju/.local/share/mamba/envs/p2g/bin/python
export CUDA_VISIBLE_DEVICES=2
export PYTHONPATH=src:scripts
OUT=figset_20260815
rm -rf $OUT && mkdir -p $OUT

KV=data/staging/tokens_korean_gated/vocab.json
RV=data/staging/tokens_rplan/vocab.json
N=40

echo "[$(date)] FIGSET START (n=$N per model, unfiltered)"

$PY scripts/fig_model_sets.py --tag R  --n $N --outdir $OUT/R \
    --ckpt ckpts/korplan_ar_r_roomperm_seed42_ep80.pt --vocab $RV \
    --country 1 --aspect-max 8  --fp-fill 0.60 --fp-convex 0.75 > $OUT/R.log 2>&1

$PY scripts/fig_model_sets.py --tag K  --n $N --outdir $OUT/K \
    --ckpt ckpts/korplan_ar_k_gated_targetonly_ep80.pt --vocab $KV \
    --country 0 --aspect-max 10 --fp-fill 0.60 --fp-convex 0.70 > $OUT/K.log 2>&1

$PY scripts/fig_model_sets.py --tag RK --n $N --outdir $OUT/RK \
    --ckpt ckpts/korplan_ar_rk_gated_seed42_roomperm_ep180.pt --vocab $KV \
    --country 0 --aspect-max 10 --fp-fill 0.60 --fp-convex 0.70 > $OUT/RK.log 2>&1

cat > $OUT/README.txt <<'EOF'
KorPlan 생성 도면 세트 (2026-08-15)

구성
  R/   RPLAN-only          ckpt korplan_ar_r_roomperm_seed42_ep80.pt   (epoch 80)
  K/   Target-only         ckpt korplan_ar_k_gated_targetonly_ep80.pt  (epoch 80)
  RK/  Pretrained+finetune ckpt korplan_ar_rk_gated_seed42_roomperm_ep180.pt (epoch 180)

각 모델 폴더
  drawable/   drawable-clean 통과분
  failed/     불통과분 (버리지 않고 전부 포함)
  manifest.json  전 도면의 판정·실패항목·지표

파일명   {모델}_seed42_ep{에폭}_{번호}_{label|nolabel}.png
  label   : 실명 표기
  nolabel : 실명 없음

표본 선정
  고정 시드(42)로 앞에서부터 40장. 선별·교체 없음.
  drawable/failed 는 사후 분류일 뿐이며 어느 쪽도 골라낸 것이 아님.

개구부 표기 — 중요
  직렬화 코덱은 개구부를 {종류, 부착 엣지, 위치비율}로만 인코딩한다. 폭 정보가 없다.
  기존 렌더러는 고정 상수(width_px)로 문·창을 그려서 데이터에 없는 치수를 암시했다.
  이 세트에서는 기본 심볼을 끄고 위치 마커만 표시한다.
      주황 원  = 문 위치        (빨간 테두리 = 현관)
      파란 사각 = 창 위치
  개구부 폭은 표현되지 않는다 — 데이터에 없기 때문.

임계값 (논문과 동일)
  R  : aspect 8,  fill 0.60, convex 0.75   (RPLAN 표현)
  K/RK: aspect 10, fill 0.60, convex 0.70  (타깃 표현)
  overlap 0.25 공통
EOF

echo "[$(date)] FIGSET DONE"
for M in R K RK; do
  D=$(ls $OUT/$M/drawable/*_label.png 2>/dev/null | wc -l)
  F=$(ls $OUT/$M/failed/*_label.png 2>/dev/null | wc -l)
  echo "  $M: drawable=$D failed=$F"
done
tar czf $OUT.tar.gz $OUT
echo "[$(date)] PACKED $OUT.tar.gz"
touch $OUT/FIGSET_COMPLETE
