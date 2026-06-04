# Plan2Graph — 건축 도면 위상 그래프 데이터셋 & 공간배치 생성 AI

> 건축 도면을 **"방–문–방" 위상 그래프**로 변환하고, 온톨로지·법규 검증과 결합해
> **자연어 요구 → [제약그래프] → 생성 AI → [배치그래프] → 규제 AI 검증 → 무결 도면**
> 으로 잇는 데이터셋·AI 파이프라인.

## 무엇을 하나

- **도면 → 그래프**: 도면(이미지/벡터)에서 방(노드)·문/인접(엣지)을 추출하고, 무결성(R1~R5)·법규(채광 등)를 검증.
- **3개 출처를 공통 스키마로 통합**: AI-Hub(한국 실측 COCO) · CubiCasa5k(SVG 벡터) · RPLAN(.mat 벡터) → 동일 그래프 스키마([schema.py](src/plan2graph/schema.py)).
- **눈으로 검증하는 콘솔**: Streamlit 대시보드로 *원본 ∥ 그래프*를 전수 검수. 모든 도면은 **처분(✅사용 / 🛠보정·복구 / 🚫제외 + 사유)** 으로 분류되고, **각 출처 합 = 받은 원본 수**(누구나 추적·검증 가능).

## 데이터 현황 (라이브 — 상세 [docs/DATA_STATUS.md](docs/DATA_STATUS.md))

| 출처 | 받은 원본 | ✅ 사용 | 🛠 보정·복구 | 🚫 제외 | scale |
|---|---|---|---|---|---|
| **AI-Hub** | 43,219 | 10,690 | 3,497 | 29,032 | OCR 역산 |
| **CubiCasa5k** | 5,000 | 3,018 | 1,958 | 24 | SVG 치수(자동) |
| **RPLAN** | 80,788 | 80,371 | 417 | 0 | 없음(정규화 벡터) |

> "전체"는 우리가 정하는 수가 아니라 **받은 raw 그 자체**. 중복·배제는 전부 *기록된 사유*이고, raw가 보존돼 검증 가능 — 이게 데이터 신뢰의 기본.
> (AI-Hub 공식 명세는 48,033이나 실제 개방 배포분은 43,219 — 자세한 경위는 DATA_STATUS.)

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
├─ scripts/  tests/  notebooks/  legal/  ontology/  fonts/
├─ data/                       # 데이터 (대부분 .gitignore, 서버 보관)
│   ├─ raw/{aihub,cubicasa5k,rplan}      # 원본 다운로드
│   ├─ staging/{aihub,cubicasa5k,rplan}  # 작업본: graphs + manifest + ledger
│   ├─ releases/v0             # 동결 벤치마크(평가 test 고정)
│   └─ interim/  v2v/
└─ runs/                       # 실험 원장(provenance·비교표)
```

원칙: **raw(원본) → staging(작업) → releases(동결)**. 코드·문서는 git, 대용량 데이터는 서버(115) 단일.

## 문서 (`docs/`)

| 문서 | 내용 |
|---|---|
| [DATASET_DESIGN](docs/DATASET_DESIGN.md) | 데이터 모델·처분·manifest 설계 |
| [DATA_STATUS](docs/DATA_STATUS.md) | 라이브 데이터 현황(수치·43,219 등) |
| [ROADMAP](docs/ROADMAP.md) | 계획·마스터플랜 |
| [OPERATIONS](docs/OPERATIONS.md) | 115 배포·실행·디버그 |
| [EXPERIMENTS](docs/EXPERIMENTS.md) | 학습·평가·결과 |
| [NOTES](docs/NOTES.md) | 가정↔실제·결정 로그 |
| [DECISIONS_NEEDED](docs/DECISIONS_NEEDED.md) | 사람 확인 필요 항목 |
| [SPEC](docs/SPEC.md) | 원본 작업 명세(데이터 스펙·태스크·스키마) |

## 기술 스택

Python 3.11 · `shapely`(기하) · `networkx`(그래프) · COCO · `owlready2`(온톨로지) · `streamlit`(검수 콘솔) · YOLO/ultralytics(V2V 라벨 복구) · `matplotlib` · `pandas`

## 출처·라이선스

AI-Hub 건축도면 데이터(dataSetSn=71465) · CubiCasa5k · RPLAN. 상업적 활용/기술이전 가능 여부는 [docs/DECISIONS_NEEDED.md](docs/DECISIONS_NEEDED.md) 참조.
