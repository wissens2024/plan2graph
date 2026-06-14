#!/bin/bash
# Plan2Graph 관리자 대시보드 수동 시작 (115 서버).
#   nginx(plan2graph.aines.kr:443) → 이 streamlit(:8501).
#   서버 설정(주소/포트/CORS·XSRF off)은 .streamlit/config.toml 에 둠(CLI 플래그 대신).
#   ⚠️ env python 절대경로로 직접 실행 — `micromamba run -n p2g`은 비대화형 ssh에서
#      MAMBA_ROOT_PREFIX 미설정으로 env를 못 찾아 기동 실패함(2026-06-14 실측).
#   재부팅·종료 시 이 스크립트만 다시 실행하면 됨. (systemd 대신 수동 배포)
cd "$HOME/plan2graph" || exit 1
export PLAN2GRAPH_RAW="$HOME/plan2graph/data/raw/aihub/01-1.정식개방데이터"
# RPLAN: 받은 패키지는 snapshot_train(렌더 도면 67k)가 본체, Img는 외곽선 썸네일이라 본체만 검수
export PLAN2GRAPH_RPLAN="$HOME/plan2graph/data/raw/rplan/Interface/static/Data/snapshot_train"
export PYTHONPATH=src
_PY=/home/ju/.local/share/mamba/envs/p2g/bin/python
fuser -k 8501/tcp 2>/dev/null; sleep 1
mkdir -p logs
nohup "$_PY" -m streamlit run admin.py \
  < /dev/null > logs/streamlit.log 2>&1 &

# 정보 보정 웹 에디터(ADR-0008) — :8600. nginx /editor/ 프록시 또는 ssh -L 터널로 접속.
fuser -k 8600/tcp 2>/dev/null; sleep 1
PYTHONPATH=src setsid nohup "$_PY" -u scripts/edit_server.py --port 8600 \
  < /dev/null > logs/edit_server.log 2>&1 &

sleep 15
code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8501/_stcore/health 2>/dev/null)
ed=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8600/ 2>/dev/null)
echo "대시보드 시작 (health=$code) → https://plan2graph.aines.kr/   로그: logs/streamlit.log"
echo "보정 에디터 시작 (health=$ed) → :8600 (nginx /editor/ 또는 ssh -L 8600 터널)"
