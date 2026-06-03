#!/bin/bash
# V2V용 SPA+STR zip(13.5GB) 노트북→서버 업로드. scp(파일 단위 재시도, 크기검증).
#   타깃은 ASCII 스테이징(~/aihub_stage). 한글 구조 구성은 별도 서버 스크립트가 처리.
SRC="건축 도면 데이터/01-1.정식개방데이터"
HOST="ju@sse.aines.kr"
ssh -o BatchMode=yes "$HOST" 'mkdir -p ~/aihub_stage' || exit 1
files=(
  "Training/01.원천데이터/TS_SPA_1.zip"
  "Training/01.원천데이터/TS_SPA_2.zip"
  "Training/01.원천데이터/TS_STR_1.zip"
  "Training/01.원천데이터/TS_STR_2.zip"
  "Training/02.라벨링데이터/TL_SPA.zip"
  "Training/02.라벨링데이터/TL_STR.zip"
  "Validation/01.원천데이터/VS_SPA.zip"
  "Validation/01.원천데이터/VS_STR.zip"
  "Validation/02.라벨링데이터/VL_SPA.zip"
  "Validation/02.라벨링데이터/VL_STR.zip"
)
for f in "${files[@]}"; do
  base=$(basename "$f")
  lsize=$(stat -c%s "$SRC/$f" 2>/dev/null)
  ok=0
  for try in 1 2 3 4 5; do
    rsize=$(ssh -o BatchMode=yes "$HOST" "stat -c%s ~/aihub_stage/$base 2>/dev/null || echo 0")
    if [ "$rsize" = "$lsize" ]; then echo "[$(date +%H:%M:%S)] OK $base ($lsize)"; ok=1; break; fi
    echo "[$(date +%H:%M:%S)] scp $base try$try (local=$lsize remote=$rsize)"
    t0=$(date +%s)
    scp -q "$SRC/$f" "$HOST:~/aihub_stage/$base"
    t1=$(date +%s)
    [ $((t1-t0)) -gt 0 ] && echo "    $base: $(( lsize/1048576/(t1-t0+1) )) MB/s"
  done
  [ "$ok" = 0 ] && echo "FAIL $base"
done
echo "UPLOAD DONE"
