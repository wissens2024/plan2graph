#!/bin/bash
# Plan2Graph 관리자 대시보드 수동 시작 (115 서버).
#   nginx(plan2graph.aines.kr:443) → 이 streamlit(:8501).
#   서버 설정(주소/포트/CORS·XSRF off)은 .streamlit/config.toml 에 둠 —
#   micromamba run 이 CLI `--server.*` 플래그를 삼키므로 config 파일로 고정해야 함.
#   환경변수는 export 로 전달(micromamba run 이 상속).
#   재부팅·종료 시 이 스크립트만 다시 실행하면 됨. (systemd 대신 수동 배포)
cd "$HOME/plan2graph" || exit 1
export PLAN2GRAPH_RAW="$HOME/plan2graph/aihub_raw/01-1.정식개방데이터"
# RPLAN: 받은 패키지는 snapshot_train(렌더 도면 67k)가 본체, Img는 외곽선 썸네일이라 본체만 검수
export PLAN2GRAPH_RPLAN="$HOME/plan2graph/data/external/rplan/Interface/static/Data/snapshot_train"
export PYTHONPATH=src
fuser -k 8501/tcp 2>/dev/null; sleep 1
mkdir -p logs
nohup "$HOME/bin/micromamba" run -n p2g streamlit run admin.py \
  < /dev/null > logs/streamlit.log 2>&1 &
sleep 10
code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8501/_stcore/health 2>/dev/null)
echo "대시보드 시작 (health=$code) → https://plan2graph.aines.kr/   로그: logs/streamlit.log"
