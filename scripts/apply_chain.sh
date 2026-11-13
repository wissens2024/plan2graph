#!/bin/bash
# apply_chain.sh TAG NSHARD [PLANSFILE]
#   PLANSFILE 를 주면 그 목록만, 없으면 전수(--sample -1).
#   보호 대상(data/protected_plans.txt)은 모든 단계에서 제외된다.
set -u
cd /home/ju/plan2graph || exit 1
P=/home/ju/.local/share/mamba/envs/p2g/bin/python
EX=data/protected_plans.txt
TAG=$1; NS=$2; PF=${3:-}
OUT=logs/apply_$TAG
mkdir -p "$OUT"
if [ -n "$PF" ]; then SEL="--plans-file $PF"; else SEL="--sample -1"; fi

run_stage () {
  nm=$1; shift
  echo "=== $nm  start $(date +'%F %T')" >> "$OUT/main.log"
  t0=$(date +%s)
  for i in $(seq 0 $((NS-1))); do
    $P -I "$@" $SEL --exclude-file $EX --shard "$i/$NS" --apply \
      --progress 1000 --stats-json "$OUT/${nm}_s$i.json" > "$OUT/${nm}_s$i.log" 2>&1 &
  done
  wait
  t1=$(date +%s)
  echo "=== $nm  done  $(date +'%F %T')  $((t1-t0))s" >> "$OUT/main.log"
}

echo "##### chain $TAG  shards=$NS  sel=$SEL  $(date +'%F %T')" >> "$OUT/main.log"
run_stage snap1   scripts/snap_axis.py --tol 4
run_stage snap2   scripts/snap_axis.py --tol 4
run_stage snap3   scripts/snap_axis.py --tol 4
run_stage snap4   scripts/snap_axis.py --tol 6 --max-area-pct 6
run_stage rectify scripts/rectify_rooms.py
run_stage links   scripts/rebuild_links.py
run_stage openings scripts/fix_openings.py
echo "##### ALL DONE $(date +'%F %T')" >> "$OUT/main.log"
