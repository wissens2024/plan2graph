#!/bin/bash
# measure_run.sh TAG NSHARD [PLANSFILE]
set -u
cd /home/ju/plan2graph || exit 1
P=/home/ju/.local/share/mamba/envs/p2g/bin/python
TAG=$1; NS=$2; PF=${3:-}
OUT=logs/measure_$TAG
mkdir -p "$OUT"
EXTRA=""
[ -n "$PF" ] && EXTRA="--plans-file $PF"
t0=$(date +%s)
for i in $(seq 0 $((NS-1))); do
  $P -I scripts/measure_geom.py --shard "$i/$NS" \
     --exclude-file data/protected_plans.txt $EXTRA \
     --out "$OUT/m_s$i.json" > "$OUT/m_s$i.log" 2>&1 &
done
wait
$P -I scripts/measure_geom.py --label "$TAG" --merge "$OUT"/m_s*.json > "$OUT/report.txt" 2>&1
t1=$(date +%s)
echo "measured in $((t1-t0))s" >> "$OUT/report.txt"
cat "$OUT/report.txt"
