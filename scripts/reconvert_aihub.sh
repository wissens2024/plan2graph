#!/usr/bin/env bash
# T-라인 재변환: 임계(P2G_*) 적용해 라벨→그래프 전체 재변환 → staging 통합 → manifest 재생성.
# 안전: 별도 dir에 빌드 → 충분히 나오면만 교체(백업 보존) → build_aihub로 disposition 재계산.
# 호출: P2G_OPEN_MAX_GAP_PX=.. P2G_OPEN_MIN_RATIO=.. P2G_MIN_ETC_AREA_PX=.. PYEXE=<python> \
#       setsid nohup bash scripts/reconvert_aihub.sh > logs/reconvert.log 2>&1 &
set -u
cd "$(dirname "$0")/.."
PY="${PYEXE:-python}"
TS="$(date +%Y%m%d_%H%M%S)"
echo "[reconvert $TS] 임계 GAP=${P2G_OPEN_MAX_GAP_PX:-default} RATIO=${P2G_OPEN_MIN_RATIO:-default} ETC=${P2G_MIN_ETC_AREA_PX:-default}"

RECONV="data/staging/aihub_reconv"
rm -rf "$RECONV"
echo "[reconvert] 1/3 라벨→그래프 재변환 (build_dataset, 무거움)..."
PYTHONPATH=src "$PY" -u -m plan2graph.build_dataset --split all --jobs 4 --out "$RECONV"
RC=$?
N="$(ls "$RECONV/graphs" 2>/dev/null | wc -l)"
echo "[reconvert] build_dataset rc=$RC · 새 그래프 $N개"
if [ "$RC" -ne 0 ] || [ "$N" -lt 1000 ]; then
  echo "[reconvert] 중단 — 실패 또는 그래프 너무 적음($N). staging 변경 안 함."
  exit 1
fi
echo "[reconvert] 2/3 staging 교체 (백업: graphs.bak.$TS)..."
mv data/staging/aihub/graphs "data/staging/aihub/graphs.bak.$TS"
mv "$RECONV/graphs" data/staging/aihub/graphs
echo "[reconvert] 3/3 manifest 재생성 (build_aihub) → disposition 갱신..."
PYTHONPATH=src "$PY" -u -m plan2graph.build_aihub --at "$TS"
echo "[reconvert $TS] 완료 — use/fix는 다음 화면 로드에서 갱신(크기 기반 캐시). 백업: graphs.bak.$TS"
