#!/bin/bash
# 스테이징의 OBJ/OCR 원천 zip을 RAW 구조(원천데이터)로 배치 — OBJ/OCR-only 도면 표시용.
RAW="$HOME/plan2graph/data/raw/aihub/01-1.정식개방데이터"
cd "$HOME/aihub_stage" || { echo "스테이징 없음"; exit 1; }
for z in TS_OBJ_1 TS_OBJ_2 TS_OCR_1 TS_OCR_2; do
  [ -f "$z.zip" ] && mv -f "$z.zip" "$RAW/Training/01.원천데이터/" && echo "  $z → Training"
done
for z in VS_OBJ VS_OCR; do
  [ -f "$z.zip" ] && mv -f "$z.zip" "$RAW/Validation/01.원천데이터/" && echo "  $z → Validation"
done
echo "배치된 원천 zip 총: $(find "$RAW" -name '*.zip' | wc -l)"
