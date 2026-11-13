#!/bin/bash
# resume_chain.sh — links 를 남은 목록으로 마저 돌리고, openings 를 전수로 돈다.
set -u
cd /home/ju/plan2graph || exit 1
P=/home/ju/.local/share/mamba/envs/p2g/bin/python
EX=data/protected_plans.txt
NS=16
OUT=logs/apply_full
mkdir -p "$OUT"

echo "=== links2  start $(date +'%F %T')  (남은 $(wc -l < data/links_rest.txt)장)" >> "$OUT/main.log"
t0=$(date +%s)
for i in $(seq 0 $((NS-1))); do
  $P -I scripts/rebuild_links.py --plans-file data/links_rest.txt \
     --exclude-file $EX --shard "$i/$NS" --apply --progress 1000 \
     --stats-json "$OUT/links2_s$i.json" > "$OUT/links2_s$i.log" 2>&1 &
done
wait
t1=$(date +%s)
echo "=== links2  done  $(date +'%F %T')  $((t1-t0))s" >> "$OUT/main.log"

echo "=== openings  start $(date +'%F %T')" >> "$OUT/main.log"
t0=$(date +%s)
for i in $(seq 0 $((NS-1))); do
  $P -I scripts/fix_openings.py --sample -1 \
     --exclude-file $EX --shard "$i/$NS" --apply --progress 2000 \
     --stats-json "$OUT/openings_s$i.json" > "$OUT/openings_s$i.log" 2>&1 &
done
wait
t1=$(date +%s)
echo "=== openings  done  $(date +'%F %T')  $((t1-t0))s" >> "$OUT/main.log"
echo "##### ALL DONE $(date +'%F %T')" >> "$OUT/main.log"
