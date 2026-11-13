#!/bin/bash
# measure_dir.sh TAG NSHARD DIR PLANSFILE
set -u
cd /home/ju/plan2graph || exit 1
P=/home/ju/.local/share/mamba/envs/p2g/bin/python
TAG=$1; NS=$2; DIR=$3; PF=$4
OUT=logs/measure_$TAG
mkdir -p "$OUT"
for i in $(seq 0 $((NS-1))); do
  $P -I scripts/measure_geom.py --shard "$i/$NS" --dir "$DIR" \
     --exclude-file data/protected_plans.txt --plans-file "$PF" \
     --out "$OUT/m_s$i.json" > "$OUT/m_s$i.log" 2>&1 &
done
wait
$P -I scripts/measure_geom.py --label "$TAG" --merge "$OUT"/m_s*.json
