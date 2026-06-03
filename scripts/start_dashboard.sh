#!/bin/bash
# Plan2Graph 관리자 대시보드 수동 시작 (115 서버).
#   nginx(plan2graph.aines.kr:443) → 이 streamlit(:8501). CORS/XSRF off(프록시 origin 허용).
#   재부팅·종료 시 이 스크립트만 다시 실행하면 됨. (systemd 대신 수동 배포)
cd "$HOME/plan2graph" || exit 1
RAW="$HOME/plan2graph/aihub_raw/01-1.정식개방데이터"
fuser -k 8501/tcp 2>/dev/null; sleep 1
mkdir -p logs
nohup env PLAN2GRAPH_RAW="$RAW" PYTHONPATH=src \
  "$HOME/bin/micromamba" run -n p2g streamlit run admin.py \
  --server.address 0.0.0.0 --server.port 8501 --server.headless true \
  --server.enableCORS false --server.enableXsrfProtection false \
  < /dev/null > logs/streamlit.log 2>&1 &
sleep 10
code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8501/_stcore/health 2>/dev/null)
echo "대시보드 시작 (health=$code) → https://plan2graph.aines.kr/   로그: logs/streamlit.log"
