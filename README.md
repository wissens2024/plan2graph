# Plan2Graph — 건축 도면 위상 그래프 데이터셋 & 공간배치 생성 AI

> 건축 도면을 **"방–문–방" 위상 그래프**로 변환하고, 온톨로지·법규 검증과 결합해
> **자연어 요구 → [제약그래프] → 생성 AI → [배치그래프] → 규제 AI 검증 → 무결 도면**
> 으로 잇는 데이터셋·AI 파이프라인.

## 무엇을 하나

- **도면 → 그래프**: 도면(이미지/벡터)에서 방(노드)·문/인접(엣지)을 추출하고, 무결성(R1~R5)·법규(채광 등)를 검증.
- **3개 출처를 공통 스키마로 통합**: AI-Hub(한국 실측 COCO) · CubiCasa5k(SVG 벡터) · RPLAN(.mat 벡터) → 동일 그래프 스키마([schema.py](src/plan2graph/schema.py)).
- **눈으로 검증하는 콘솔**: Streamlit 대시보드로 *원본 ∥ 그래프*를 전수 검수. 모든 도면은 **처분(✅사용 / 🛠보정·복구 / 🚫제외 + 사유)** 으로 분류되고, **각 출처 합 = 받은 원본 수**(누구나 추적·검증 가능).

## 데이터 현황 (라이브 — 상세 [docs/DATA.md](docs/DATA.md))

| 출처 | 받은 원본 | ✅ 사용 | 🛠 보정·복구 | 🚫 제외 | scale |
|---|---|---|---|---|---|
| **AI-Hub** | 43,219 | 10,921 | 9,412 | 22,886 | OCR 역산 |
| **CubiCasa5k** | 5,000 | 3,018 | 1,958 | 24 | SVG 치수(자동) |
| **RPLAN** | 80,788 | 80,371 | 417 | 0 | 없음(정규화 벡터) |

> "전체"는 우리가 정하는 수가 아니라 **받은 raw 그 자체**. 중복·배제는 전부 *기록된 사유*이고, raw가 보존돼 검증 가능 — 이게 데이터 신뢰의 기본.
> (AI-Hub 공식 명세는 48,033이나 실제 개방 배포분은 43,219 — 자세한 경위는 DATA.md.)

## 실행

- **서버(115)**: `bash scripts/start_dashboard.sh` → **https://plan2graph.aines.kr**
  - 🧮 검수 현황(종합) · 🏢 AI-Hub / 🏠 CubiCasa / 📐 RPLAN 도면검수 · 📏 scale 검수 · 📊 결과 대시보드
- **로컬**: 코드·문서 편집용. **대용량 데이터는 서버 단일 보관**(로컬엔 git 저장소만).

## 구조

```
plan2graph/
├─ README.md                  # 본 개요
├─ config.py  admin.py  doctor.py  requirements*.txt
├─ docs/                       # 문서 (아래 목록)
├─ src/plan2graph/             # 패키지 (adapters/{rplan,cubicasa}, schema, rules …)
├─ scripts/                    # 운영·셋업 (start_dashboard·*_setup·run_matrix·verify_dedup·systemd)
├─ tests/                      # 테스트
├─ legal/                      # 법규 규칙 DB (catalog/law_manifest/rules.json — 채광 등 법규 검증)
├─ ontology/                   # OWL 온톨로지 (floorplan.owl — 공간추론·SWRL; *.owl 미추적, 재생성)
├─ fonts/                      # NanumGothic.ttf — matplotlib·대시보드 한글 렌더
├─ artifacts/                  # 코드 그림 출력: visualize 오버레이 PNG + gate 채점(PNG는 .gitignore)
├─ data/                       # 데이터 (대부분 .gitignore, 서버 보관)
│   ├─ raw/{aihub,cubicasa5k,rplan}      # 원본 다운로드
│   ├─ staging/{aihub,cubicasa5k,rplan}  # 작업본: graphs + manifest + ledger
│   ├─ releases/{v0,v2}        # 동결 릴리스(v0=클린 dual, v2=dual+V2V+CubiCasa사전학습; 균형 test 공유)
│   └─ interim/  v2v/
└─ runs/                       # 실험 원장(provenance·비교표)
```

원칙: **raw(원본) → staging(작업) → releases(동결)**. 코드·문서는 git, 대용량 데이터는 서버(115) 단일.

## 스크립트 (`scripts/`)

대부분 **115 서버에서 실행**한다. 공통 전제: `cd ~/plan2graph`, micromamba env `p2g`, `PYTHONPATH=src`.

### 운영 — 대시보드

| 스크립트 | 하는 일 |
|---|---|
| **`start_dashboard.sh`** | 대시보드 수동 기동/재시작. `nginx(443)`→`streamlit(:8501)`. 환경변수(`PLAN2GRAPH_RAW`·`PLAN2GRAPH_RPLAN`)를 **export로** 전달(서버옵션은 `.streamlit/config.toml` — `micromamba run`이 CLI 플래그를 삼켜서), `fuser -k 8501/tcp`로 기존 종료 후 nohup 기동, health 체크. **재부팅·종료 후 이것만 다시 실행하면 됨.** |
| `plan2graph-dashboard.service` | systemd 유닛(상시기동 대안). 현재는 위 수동 방식을 사용. |

```bash
bash scripts/start_dashboard.sh        # → https://plan2graph.aines.kr/  (로그: logs/streamlit.log)
```

### 학습·실험 (GPU1만 — 운영 GPU0 보호)

| 스크립트 | 하는 일 |
|---|---|
| **`run_matrix.sh [버전]`** | 신뢰성 매트릭스: `noPretrain×5시드` + `preCubicasa×5시드`(버전 인자, 기본 v0). 각 학습 직후 eval+일반화+거주형태(dwelling) 진단을 `runs/index.jsonl`에 누적. 시드별 체크포인트(`gen_<v>_seed<s>.pt`)로 평가. `CUDA_VISIBLE_DEVICES=1` 고정. 끝나면 `python -m plan2graph.experiments agg` → 평균±표준편차 **'시드 노이즈' 판정**. |

### 데이터 업로드·배치 (노트북 → 서버, scp 재시도+크기검증)

| 스크립트 | 하는 일 |
|---|---|
| `v2v_upload.sh` | V2V용 **SPA+STR zip(~13.5GB)** 노트북→서버 `~/aihub_stage`. 파일단위 5회 재시도, 크기 일치 검증, 전송속도 표시. |
| `v2v_setup_raw.sh` | 스테이징 zip을 **RAW 한글 구조**(`{Training,Validation}/{01.원천데이터,02.라벨링데이터}`)로 배치. 이후 `export PLAN2GRAPH_RAW=<RAW>`로 파이프라인 사용. |
| `objocr_upload.sh` | **OBJ/OCR 원천 zip(~11.9GB)** 업로드 — OBJ/OCR-only 도면을 검수 GUI에 표시하기 위함(원천 PNG만). |
| `objocr_setup.sh` | OBJ/OCR zip을 RAW `01.원천데이터`로 배치. |

### 검증·유틸 (파이썬)

| 스크립트 | 하는 일 |
|---|---|
| `verify_dedup.py` | CRC32+크기 지문 기반 **'고유 도면' dedup 정확성** 검증(충돌 0 증명). |
| `define_frozen_test.py` | **균형 소버린 frozen test** 정의(AI-Hub dual, APT/DEH/ROW 각 K=100시트). 결정적 재현 → `releases/_frozen_test.json`. |
| `_render_test.py` | 카테고리별 오버레이 썸네일 1장씩 PNG 저장(육안 확인). |
| `_inject_pwindow.py` | 기존 `gen_<ver>.pt` 체크포인트에 `p_window`(타입별 창 보유 확률)만 주입. |

> 업로드 스크립트의 로컬 경로(`건축 도면 데이터/…`)는 **과거 노트북 기준**이며, 현재 원본은 서버 `data/raw/`에 단일 보관됨(재업로드 시 참고용).

## 문서 (`docs/`)

| 문서 | 내용 |
|---|---|
| [ARCHITECTURE](docs/ARCHITECTURE.md) | **AI 기술·모델·파이프라인·스키마** (SPEC+RENDER+V2V+GEOMETRY+KR관행 통합) |
| [DATA](docs/DATA.md) | **데이터 출처·카운트·처분·버전·스키마** (DATA_STATUS+DATASET_DESIGN 통합) |
| [ROADMAP](docs/ROADMAP.md) | 계획·마스터플랜 |
| [OPERATIONS](docs/OPERATIONS.md) | 115 배포·실행·디버그 |
| [EXPERIMENTS](docs/EXPERIMENTS.md) | 학습·평가·결과 |
| [V2V](docs/V2V.md) | V2V 검출 모델·실험 원장(fix→use) |
| [CORRECTION_GUIDE](docs/CORRECTION_GUIDE.md) | 사람 보정 가이드 |
| [NOTES](docs/NOTES.md) | 가정↔실제·결정 로그(역사) |
| [DECISIONS_NEEDED](docs/DECISIONS_NEEDED.md) | 사람 확인 필요 항목 |
| [adr/](docs/adr/) | 설계 결정 기록(ADR-0001~0005) |

## 기술 스택

Python 3.11 · `shapely`(기하) · `networkx`(그래프) · COCO · `owlready2`(온톨로지) · `streamlit`(검수 콘솔) · YOLO/ultralytics(V2V 라벨 복구) · `matplotlib` · `pandas`

## 출처·라이선스

AI-Hub 건축도면 데이터(dataSetSn=71465) · CubiCasa5k · RPLAN. 상업적 활용/기술이전 가능 여부는 [docs/DECISIONS_NEEDED.md](docs/DECISIONS_NEEDED.md) 참조.
