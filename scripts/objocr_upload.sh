#!/bin/bash
# OBJ/OCR 원천 zip(~11.9GB) 업로드 — OBJ/OCR-only 도면을 검수 GUI에 표시하기 위함.
#   (라벨 zip은 그래프에 안 쓰니 원천 PNG만. scp 파일단위 재시도·크기검증.)
SRC="건축 도면 데이터/01-1.정식개방데이터"
HOST="ju@sse.aines.kr"
ssh -o BatchMode=yes "$HOST" 'mkdir -p ~/aihub_stage' || exit 1
files=(
  "Training/01.원천데이터/TS_OBJ_1.zip"
  "Training/01.원천데이터/TS_OBJ_2.zip"
  "Training/01.원천데이터/TS_OCR_1.zip"
  "Training/01.원천데이터/TS_OCR_2.zip"
  "Validation/01.원천데이터/VS_OBJ.zip"
  "Validation/01.원천데이터/VS_OCR.zip"
)
for f in "${files[@]}"; do
  base=$(basename "$f")
  lsize=$(stat -c%s "$SRC/$f" 2>/dev/null)
  for try in 1 2 3 4 5; do
    rsize=$(ssh -o BatchMode=yes "$HOST" "stat -c%s ~/aihub_stage/$base 2>/dev/null || echo 0")
    if [ "$rsize" = "$lsize" ]; then echo "[$(date +%H:%M:%S)] OK $base ($lsize)"; break; fi
    echo "[$(date +%H:%M:%S)] scp $base try$try"
    scp -q "$SRC/$f" "$HOST:~/aihub_stage/$base"
  done
done
echo "OBJOCR UPLOAD DONE"
