# Plan2Graph 데이터 현황

> **2026-06-04 실측·확정. 권위 기준 = `data/staging/<source>/`(매니페스트/그래프).**
> 모든 수치는 서버(115)에서 코드로 측정·검증됨. 검수: https://plan2graph.aines.kr
> ※ 이전 funnel 추정(2026-06-03)은 스코프·시점 달라 폐기. 아래가 라이브 기준.

## 1. 처분(disposition) 모델 — 도면 1장 = 한 칸 (상호배타, 합=다운로드)

검수·회계의 단일 모델. 도면 1장은 **우선순위 단일배정**으로 정확히 한 칸에 들어간다(사유가 겹쳐도 제일 센/근본 사유 1개). 구현: [dataset_status.py](src/plan2graph/dataset_status.py) `disposition_of/disposition_combo/disposition_groups`.

- **✅ 사용(use)**: 그래프화돼 데이터셋에 들어감(직접변환 또는 V2V 복구).
- **🛠 보정·복구 필요(fix)**: 살릴 수 있으나 아직 안 됨(위상 보정 대상, V2V 복구 대기, 변환 실패, **OBJ/OCR만**=평면도지만 공간라벨 없어 보정 필요).
- **🚫 제외(excl)**: 영구 폐기 — **비-FP(평면도 아님)·중복 사본** 둘뿐(대상 아님/사본).

> **불변식:** 출처별 `사용 + 보정·복구 + 제외 = 받은 원본 수`. 검수 콤보·종합 패널이 같은 소스를 읽어 항상 일치. 검수 화면=현재 상태 뷰, 회계=종합 패널.

## 2. 출처별 회계 (라이브, 합=다운로드 검증)

| 출처 | ✅사용 | 🛠보정·복구 | 🚫제외 | 합 = **받은 raw 전부** |
|---|---|---|---|---|
| **AI-Hub** | 10,921 | 9,412 | 22,886 | **43,219** (원천 PNG 그대로) |
| **CubiCasa5k** | 3,018 | 1,958 | 24 | **5,000** (케이스 그대로) |
| **RPLAN** | 80,371 | 417 | 0 | **80,788** (.mat 그대로) |
| **합계** | 94,310 | 11,787 | 22,910 | **129,007** |

- **전체 = 받은 raw 그 자체**(우리가 정하는 수가 아님). dedup·분류는 전부 *기록된 제외 사유*. raw 보존 → 누구나 raw에서 각 도면 운명 추적·검증 가능 = 신뢰.
- AI-Hub 제외 22,886 = **중복(byte-identical 사본) 18,231** + 비-FP 4,655. (사본도 합쳐 없애지 않고 raw에 '제외·중복'으로 남김.) **OBJ/OCR만 6,146은 보정필요로** — 원본은 평면도(방 있음)고 SPA/STR 라벨만 없어 알바가 보정 가능(제외 아님).

## 3. 데이터 출처
- **AI-Hub 건축도면**(71465): COCO. 라벨 SPA(방)·STR(문/창/벽)·OBJ·OCR. 도면종류 FP(평면도)·CS·EP·SD. 위상그래프는 **FP의 SPA+STR** 필요.
- **CubiCasa5k**: SVG 5,000 케이스. **RPLAN**: Network/data.mat 80,788(rType+gtBoxNew+rEdge 벡터).

## 4. AI-Hub 상세 — 원본 회계 매니페스트

`data/staging/aihub/manifest.jsonl`(**raw 원천 PNG 1건=1줄**, [build_aihub.py](src/plan2graph/build_aihub.py) 생성). **받은 raw 43,219장 = 전체(확정)**. 줄 수 = 다운로드 = 검증 가능.

> **43,219 vs 48,033 (확정·2026-06-04):** AI-Hub 페이지 "3-2-5 원천데이터 구축수량"은 **48,033**(APT/DEH/ROW×FP/CS/EP/SD×라벨). 그러나 **정식개방데이터의 실제 배포 원천 zip(8 Train+4 Val, 전량·온전)에는 43,219장**뿐 — 모든 셀이 균일 90%(구축≠개방, QA·비식별 누락 추정). **신뢰 기준 = 실제 받은 파일 43,219로 확정.** 48,033 차이는 AI-Hub에 문의 접수함. (재다운로드해도 채워지지 않음 — 배포본이 43,219.)

| 처분 | 사유 | 수 | 복구 경로 |
|---|---|---|---|
| ✅사용 | dual(SPA+STR) 직접변환 | 3,345 | — |
| ✅사용 | **dual(중복라벨복구)** | **231** | 같은 지문 SPA/STR(다른 키) 라벨 합집합 재변환 |
| ✅사용 | 방만→V2V STR복구 | 4,053 | V2V(SPA mAP0.90) |
| ✅사용 | 구조만→V2V SPA복구 | 3,292 | V2V |
| 🛠보정 | 변환실패(dual인데 품질게이트) | 1,220 | 재변환 대상(STR병합 시 현관/필수 깨짐 627 + 미완 593) |
| 🛠복구 | 구조만 V2V 대기 | 1,051 | V2V 추가 |
| 🛠복구 | 방만 V2V 대기 | 995 | V2V 추가 |
| 🛠보정 | OBJ/OCR만(공간라벨 없음) | 6,146 | 평면도+방 있음, SPA/STR 라벨만 없음 → 알바가 원본 위에서 보정 |
| 🚫제외 | **중복(byte-identical 사본)** | **18,231** | 1장만 채택, 나머지 raw에 명시 |
| 🚫제외 | 비-FP(단면/입면/구조) | 4,655 | 불가(평면도 아님) |
| | **합** | **43,219** | = 받은 raw |

- 콤보=매니페스트(권위 카운트), 렌더=원천 zip 지문(sig) 조인. dedup: PNG의 CRC32+크기 지문([verify_dedup.py](scripts/verify_dedup.py) 전수 SHA256 0충돌 증명).
- **중복라벨복구(2026-06-04):** AI-Hub는 동일 PNG를 라벨종류(OBJ/OCR/SPA/STR)마다 *다른 키*로 중복배포한다. 옛 빌드가 SPA·STR을 키로 짝지어 짝을 못 찾던 dual을, 지문(byte-동일) 기준으로 SPA+STR 라벨을 합쳐 재변환([recover_dedup_merge.py](src/plan2graph/recover_dedup_merge.py)) → **231지문(273세대)을 convert_failed에서 사용·dual로 정정**(문 정상). 나머지 1,220은 STR병합 시 현관/필수가 깨지거나(627, SPA만으론 문 0개 degenerate) 본래 미완(593)이라 동일 품질바를 못 넘어 보류.

## 5. 그래프·릴리스 폴더 역할 (혼란 방지 — 꼭 읽기)

2단 구조: **staging(작업) → releases(버전 동결).** archive 없음(release가 곧 이력).

| 경로 | 역할 | 내용 |
|---|---|---|
| `staging/aihub/graphs` | AI-Hub **통합 작업본** | 23,679 그래프(=고유도면 10,921 다세대분할). provenance: direct 7,192 / v2v 16,214 / 중복라벨복구 273. graphs_dir("aihub")가 이걸 가리킴 |
| `staging/aihub/manifest.jsonl` | AI-Hub **원본 회계** | 43,219줄(§4) |
| `staging/cubicasa5k/graphs` | CubiCasa 작업본 | 5,000 |
| `staging/rplan/graphs` | RPLAN 작업본 | 80,788 |
| `releases/v0` | **동결 벤치마크(클린 dual)** | 7,101 그래프(dual+dedup, V2V 제외). split **train 5,947 / val 689 / test 465**. recipe=`recipes/v0.json` |
| `releases/v2` | **동결(dual+V2V + CubiCasa 사전학습)** | 23,856 그래프(aihub 20,828 + cubicasa 3,028 pretrain). split **train 21,075 / val 2,316 / test 465**. recipe=`recipes/v2.json` |

- **동결 test = 균형 소버린 벤치마크(2026-06-05 재정의)**: AI-Hub dual 한정(RPLAN/CubiCasa=사전학습전용·test 미포함). **APT/DEH/ROW 각 100시트 = 300시트(465그래프: APT 230·DEH 108·ROW 127)**. 매크로 평균 헤드라인 — 원시분포(APT 94%)가 가리던 DEH/ROW 약점을 드러냄. 옛 APT편중(489/14/15, 240시트)을 교체. v0·v2 공유(비교 타당), 누수 0. 재현: [define_frozen_test.py](scripts/define_frozen_test.py).
- **v0·v2 재freeze(2026-06-05)**: 옛 동결이 현재 staging보다 stale·v0/v2 미구분이라, manifest provenance 필터(`recipes/*.json`)로 현재 staging에서 재동결. v0=dual만, v2=dual+V2V. [[dataset-version-scheme]].
- **`processed/` 은퇴(제거)**: ⚠격리/✅채택 검수는 AI-Hub 검수의 **🔗 그래프검수**(staging/aihub + ledger)로 흡수, scale_ocr·scale 메뉴는 `staging/aihub`를 읽게 전환. 백업: `/tmp/processed_retired_*`.
- **보존 절대**: 원본(`external/`·`aihub_raw/`)·V2V 산출물(`data/v2v`). data/ = external·interim·releases·staging·v2v.
- **scale**: AI-Hub=OCR(📏에서 보정, staging 적용), CubiCasa=SVG 치수 자동(95%, ㎡), RPLAN=없음. RPLAN 렌더는 rBoundary(실제 방 폴리곤).

## 6. 검수 도구 (정부과제 — 수치마다 근거 도면)
- https://plan2graph.aines.kr — 🧮검수 현황(종합, 처분 3분류) · 🔍AI-Hub(🔗그래프검수·결정 포함) / 🌍CubiCasa / 🏙RPLAN 도면검수(처분 콤보, 합=다운로드) · 📏scale 검수/보정.
- 기동: `bash scripts/start_dashboard.sh` (115). [[dataset-version-scheme]]
