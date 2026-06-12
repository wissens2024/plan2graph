# 보정 에디터를 `plan2graph.aines.kr/editor/`로 노출 (nginx)

대시보드 사이드바의 **✏️ 보정 에디터 (웹)** 링크는 `/editor/`를 가리킨다.
이게 동작하려면 nginx가 `/editor/` → 에디터 서버(:8600)로 프록시해야 한다.
(에디터 서버는 `scripts/start_dashboard.sh`가 자동 기동: 포트 8600.)

## 1) nginx 설정 추가 (sudo 필요)

`/etc/nginx/conf.d/plan2graph.conf` 의 `server { ... }` 블록 안에 추가:

```nginx
    location /editor/ {
        proxy_pass http://127.0.0.1:8600/;   # 끝 슬래시 필수 — /editor/ 접두 제거
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
```

> 에디터 HTML은 **상대경로**(`api/graphs`)로 호출하므로 `/editor/`에서도 `/editor/api/...`로
> 정확히 프록시된다. (터널로 `localhost:8600/` 루트 접속 시에도 동일하게 동작.)

적용:
```bash
sudo nginx -t && sudo systemctl reload nginx
```

→ 이후 대시보드의 **✏️ 보정 에디터** 링크가 바로 열린다 (`https://plan2graph.aines.kr/editor/`).

## 2) nginx 없이 임시로 보기 (터널)

```bash
ssh -fN -L 8600:localhost:8600 ju@sse.aines.kr
# 브라우저: http://localhost:8600
```

## 에디터 서버 수동 기동/확인
```bash
PYTHONPATH=src python scripts/edit_server.py --port 8600    # 수동 실행
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8600/   # health(200)
```
저장은 `data/staging/gline/graphs/_edits/<id>.json` (원본 미수정).
