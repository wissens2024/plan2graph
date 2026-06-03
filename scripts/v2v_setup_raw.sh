#!/bin/bash
# 스테이징(~/aihub_stage)의 zip을 unpack.discover_zips가 기대하는 한글 구조로 배치.
#   RAW 루트(ASCII 최상위) 아래 {Training,Validation}/{01.원천데이터,02.라벨링데이터}.
#   실행 후: export PLAN2GRAPH_RAW=<RAW> 로 파이프라인 사용.
RAW="$HOME/plan2graph/aihub_raw/01-1.정식개방데이터"
mkdir -p "$RAW/Training/01.원천데이터" "$RAW/Training/02.라벨링데이터" \
         "$RAW/Validation/01.원천데이터" "$RAW/Validation/02.라벨링데이터"
cd "$HOME/aihub_stage" || { echo "스테이징 없음"; exit 1; }
mv_to() { [ -f "$1" ] && mv -f "$1" "$2/" && echo "  $1 -> $2"; }
for z in TS_SPA_1 TS_SPA_2 TS_STR_1 TS_STR_2; do mv_to "$z.zip" "$RAW/Training/01.원천데이터"; done
mv_to TL_SPA.zip "$RAW/Training/02.라벨링데이터"
mv_to TL_STR.zip "$RAW/Training/02.라벨링데이터"
mv_to VS_SPA.zip "$RAW/Validation/01.원천데이터"
mv_to VS_STR.zip "$RAW/Validation/01.원천데이터"
mv_to VL_SPA.zip "$RAW/Validation/02.라벨링데이터"
mv_to VL_STR.zip "$RAW/Validation/02.라벨링데이터"
echo "RAW=$RAW"
echo "배치된 zip 수: $(find "$RAW" -name '*.zip' | wc -l)"
