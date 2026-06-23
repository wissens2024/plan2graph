# 개발/실행 환경 (115 서버) — 단일 진실

> ⚠️ 이 문서가 환경의 **유일한 정본**이다. 새 가상환경을 만들거나 시스템 python에
> 직접 설치하지 말 것. 과거 세션들이 제각각 환경을 만들어 streamlit·python 실행이
> 반복적으로 깨졌다(2026-06-23 정리). 헷갈리면 여기부터 읽어라.

## 접속
- `ssh ju@sse.aines.kr` (내부 192.168.0.115). 키인증.
- 코드: `~/plan2graph` (git clone, branch `main`).
- 작업 원칙: **서버에서 코딩·실행·커밋**, 로컬은 `git pull`만(READ-ONLY).

## 정본 가상환경 = ENV1 (이것만 쓴다)
```
경로:  /home/ju/.local/share/mamba/envs/p2g
python: /home/ju/.local/share/mamba/envs/p2g/bin/python   (3.11.15)
torch:  2.5.1+cu121   (CUDA 12.1, RTX3080 ×2 작동 확인)
패키지: 104개 (streamlit·torch·ultralytics·pytorch-fid·shapely·ezdxf 등 완전)
동결:   docs/env/requirements.env1.lock
```

### 실행 방법 (셋 중 하나, 결과 동일)
1. **절대경로 python (가장 안전, 비대화형/스크립트 권장)**
   ```
   /home/ju/.local/share/mamba/envs/p2g/bin/python -m ...
   ```
2. **micromamba + root prefix 명시** (비대화형에서도 ENV1로 정확히 떨어짐)
   ```
   ~/bin/micromamba run -r /home/ju/.local/share/mamba -n p2g python -m ...
   ```
3. **대화형 셸 alias** (`.bashrc` 로드된 로그인 셸에서만)
   ```
   p2g-run python -m ...      # = ~/.local/bin/micromamba run -n p2g
   ```

### ❌ 절대 하지 말 것 (과거 사고 원인)
- `~/bin/micromamba run -n p2g ...` 처럼 **root prefix(-r) 없이** 실행 — 비대화형
  SSH에선 `MAMBA_ROOT_PREFIX` 미설정 → micromamba 기본값 `~/micromamba`로 빠져
  **불완전한 다른 env**를 쓰게 된다(과거 ENV2 사고).
- 시스템 python3.8 (`/usr/bin/python3`)이나 `pip install --user`로 직접 설치 —
  `~/.local/lib/python3.8/site-packages`에 torch 2.4.1 등 별도 스택이 생긴다.
- `~/.local/bin/streamlit` / `~/.local/bin/uvicorn` 호출 — 시스템 py3.8을 가리킨다
  (2026-06-23 `.disabled`로 차단함).
- 새 startup 스크립트 자작 — 아래 정본 스크립트만 쓴다.

## 앱 기동 (정본 스크립트)
```
bash scripts/start_dashboard.sh
```
- streamlit 대시보드 `:8501` (nginx https://plan2graph.aines.kr/)
- 정보보정 에디터 `:8600` (nginx https://plan2graph.aines.kr/editor/)
- 두 스크립트 모두 **ENV1 절대경로 python**으로 기동(모범). 재부팅·종료 후 이것만 재실행.
- 서버옵션은 `.streamlit/config.toml`(CLI 플래그는 micromamba run이 삼킴).
- 종료/재시작은 포트 PID로만(`pkill -f streamlit`은 ssh 셸까지 죽임).
- "반영됐나"는 코드가 아니라 **브라우저 화면**으로 검증(chrome-devtools).

## GPU
- RTX3080 ×2 (각 10GB), driver 570.x. 둘 다 평소 유휴.
- 학습은 수동 관리. autoresume 크론 금지(과거 churn 주범, 삭제됨).

## 정리 이력
- **2026-06-23**: 환경 이중화 정리. ENV1을 정본 확정. 중복 ENV2
  (`~/micromamba/envs/p2g`, torch 2.7.1+cu118, 불완전 84pkg, 세션이 만든 쓰레기)는
  `~/micromamba/envs/p2g.REMOVE`로 rename(관찰 후 삭제 예정). matrix 스크립트 3개에
  `-r` root prefix 추가. rogue 진입점(`~/.local/bin/streamlit`·`uvicorn`) `.disabled`.
  복구 잔해(start_streamlit.sh·test_*.py·*.old/.backup/.fix)는 `~/_attic_p2g_20260623/`로 이동.
