# Plan2Graph 데이터 현황

> **2026-06-04 실측·확정. 권위 기준 = `data/staging/<source>/`(매니페스트/그래프).**
> 모든 수치는 서버(115)에서 코드로 측정·검증됨. 검수: https://plan2graph.aines.kr
> ※ 이전 funnel 추정(2026-06-03)은 스코프·시점 달라 폐기. 아래가 라이브 기준.

## 1. 처분(disposition) 모델 — 도면 1장 = 한 칸 (상호배타, 합=다운로드)

검수·회계의 단일 모델. 도면 1장은 **우선순위 단일배정**으로 정확히 한 칸에 들어간다(사유가 겹쳐도 제일 센/근본 사유 1개). 구현: [dataset_status.py](src/plan2graph/dataset_status.py) `disposition_of/disposition_combo/disposition_groups`.

- **✅ 사용(use)**: 그래프화돼 데이터셋에 들어감(직접변환 또는 V2V 복구).
- **🛠 보정·복구 필요(fix)**: 살릴 수 있으나 아직 안 됨(위상 보정 대상, V2V 복구 대기, 변환 실패).
- **🚫 제외(excl)**: 영구 폐기(비-FP, OBJ/OCR만, 중복 사본).

> **불변식:** 출처별 `사용 + 보정·복구 + 제외 = 받은 원본 수`. 검수 콤보·종합 패널이 같은 소스를 읽어 항상 일치. 검수 화면=현재 상태 뷰, 회계=종합 패널.

## 2. 출처별 회계 (라이브, 합=다운로드 검증)

| 출처 | ✅사용 | 🛠보정·복구 | 🚫제외 | 합 = 받은 원본 |
|---|---|---|---|---|
| **AI-Hub** | 10,690 | 3,497 | 11,091 | **25,278** |
| **CubiCasa5k** | 3,018 | 1,958 | 24 | **5,000** |
| **RPLAN** | 80,371 | 417 | 0 | **80,788** |
| **합계** | 94,079 | 5,872 | 11,115 | **111,066** |

- AI-Hub는 **원본 도면(고유+중복사본) 기준**. 그래프는 다세대 분할로 더 많음(아래 §4).
- CubiCasa/RPLAN은 universe=그래프 레코드(전량 변환). 사용=정상, 보정=격리(위상 위반), 제외=중복/빈.

## 3. 데이터 출처
- **AI-Hub 건축도면**(71465): COCO. 라벨 SPA(방)·STR(문/창/벽)·OBJ·OCR. 도면종류 FP(평면도)·CS·EP·SD. 위상그래프는 **FP의 SPA+STR** 필요.
- **CubiCasa5k**: SVG 5,000 케이스. **RPLAN**: Network/data.mat 80,788(rType+gtBoxNew+rEdge 벡터).

## 4. AI-Hub 상세 — 원본 회계 매니페스트

`data/staging/aihub/manifest.jsonl`(원본 1장=1줄, [build_aihub.py](src/plan2graph/build_aihub.py) 생성). 라벨파일 43,219 → 고유 도면 24,988(+중복사본 290) = **받은 인스턴스 25,278**.

| 처분 | 사유 | 수 | 복구 경로 |
|---|---|---|---|
| ✅사용 | dual(SPA+STR) 직접변환 | 3,345 | — |
| ✅사용 | 방만→V2V STR복구 | 4,053 | V2V(SPA mAP0.90) |
| ✅사용 | 구조만→V2V SPA복구 | 3,292 | V2V |
| 🛠보정 | 변환실패(dual인데 품질게이트) | 1,451 | 재변환 대상 |
| 🛠복구 | 방만 V2V 대기 | 995 | V2V 추가 |
| 🛠복구 | 구조만 V2V 대기 | 1,051 | V2V 추가 |
| 🚫제외 | OBJ/OCR만 | 6,146 | (둘다 예측 필요, 우선순위 낮음) |
| 🚫제외 | 비-FP(단면/입면/구조) | 4,655 | 불가(위상 없음) |
| 🚫제외 | 중복(사본) | 290 | 1장만 채택 |

- 콤보=매니페스트(권위 카운트), 렌더=원천 zip 지문(sig) 조인. dedup: PNG의 CRC32+크기 지문([verify_dedup.py](scripts/verify_dedup.py) 전수 SHA256 0충돌 증명).

## 5. 그래프·릴리스 폴더 역할 (혼란 방지 — 꼭 읽기)

2단 구조: **staging(작업) → releases(버전 동결).** archive 없음(release가 곧 이력).

| 경로 | 역할 | 내용 |
|---|---|---|
| `staging/aihub/graphs` | AI-Hub **통합 작업본** | 23,406 그래프(=고유도면 10,690 다세대분할). provenance: direct 7,192 / v2v 16,214. graphs_dir("aihub")가 이걸 가리킴 |
| `staging/aihub/manifest.jsonl` | AI-Hub **원본 회계** | 25,278줄(§4) |
| `staging/cubicasa5k/graphs` | CubiCasa 작업본 | 5,000 |
| `staging/rplan/graphs` | RPLAN 작업본 | 80,788 |
| `releases/v0` | **동결 벤치마크(평가)** | dual 5,648 + **고정 test split**(모델 비교 기준, 불변). 보존 필수 |
| `processed/` | v0 벤치마크 **작업본 + ⚠/✅ 검수 큐** | graphs 5,648 + accepted/quarantine.csv(빌드 자동산출). 사람 결정 원장 없음 |

- **`releases/v2` 삭제됨**(미완 스모크 빌드. V2V 그래프는 staging/aihub에 100% 보존 확인 후 제거).
- **보존 절대**: 원본(`external/`·`aihub_raw/`)·V2V 산출물(`data/v2v` 5.4G, GPU 시간). 파생물만 재생성.

## 6. 검수 도구 (정부과제 — 수치마다 근거 도면)
- https://plan2graph.aines.kr — 🔍AI-Hub / 🌍CubiCasa / 🏙RPLAN 도면검수(처분 콤보, 합=다운로드) · 🧮검수 현황(종합, 처분 3분류) · ⚠격리/✅채택(벤치마크 품질게이트).
- 기동: `bash scripts/start_dashboard.sh` (115). [[dataset-version-scheme]]
