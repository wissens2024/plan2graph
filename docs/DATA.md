# Plan2Graph 데이터 — 출처·카운트·처분·버전·스키마 (정본)

> **단일 진실 = 서버(115) `data/staging/<source>/`.** 모든 수치는 코드로 측정·검증. 검수 GUI: https://plan2graph.aines.kr
> 이 문서 = DATA_STATUS(현황) + DATASET_DESIGN(설계) 통합본. 휘발성 상세는 코드 포인터로만 둔다.
> G-라인 g0/g1 = **objocr 2패스 재빌드 완료**(2026-06-11, n_units 40,495 · n_err 0). §5 확정값.
>
> ⚠️ **[검증 2026-06-15] 용어·폴더 갱신**: 라인 용어는 이제 **Parsed / Corrected**(ADR-0009) — 본 문서의 "T-라인/v*"·"G-라인/g*"는 **레거시 별칭**(=Parsed/Corrected). 그래프 스키마는 **g-0.4**(ADR-0010). 실제 폴더(서버 확인): `staging/corrected`(옛 gline)·`releases/{parsed,corrected}`(신규). ⚠️ **전환 중** — 레거시 `releases/v0/`(옛 T-line)가 git 추적으로 잔존, 신규 `parsed/corrected`는 미추적. 폴더/네이밍 완전 정리(freeze 재발행)는 **보류**(데이터 surgery 위험, 별도 작업).

---

## 1. 출처별 회계 (받은 raw = 전체, 합=다운로드 검증)

| 출처 | ✅사용(use) | 🛠보정필요(fix) | 🚫제외(excl) | **받은 원본** | 세대(추출) |
|---|---|---|---|---|---|
| **AI-Hub** (71465) | 10,921 | 9,412 | 22,886 | **43,219** 도면 | **23,679** |
| **CubiCasa5k** | 3,018 | 1,958 | 24 | **5,000** 케이스 | — |
| **RPLAN** | 80,371 | 417 | 0 | **80,788** .mat | — |

- **전체 = 받은 raw 그 자체**(우리가 정하는 수 아님). dedup·분류는 전부 *기록된 제외 사유* — raw 보존 → 누구나 추적·검증 가능.
- **세대 = 실제 추출된 그래프 수**(사용 10,921도면→23,679세대, 2.17배 전개). 보정필요·제외=0(미변환 못세고, 중복은 원본과 같은 세대라 중복집계 안 함, 비FP는 세대 없음). ⚠️ 옛 **48,628은 중복본을 원본 세대수로 재계상한 더블카운트 버그**(2026-06-11 수정, `aihub_row_units`). 받은 도면 43,219는 불변.
- 처분은 **상호배타·우선순위 단일배정**(도면 1장=한 칸). 구현·규칙: `dataset_status.py`(`disposition_of`/`aihub_t_status`/`gline_status`).

---

## 2. AI-Hub 상세 — 라벨 구성 × 처분 (정본 manifest)

`data/staging/aihub/manifest.jsonl` (raw PNG 1건=1줄, `build_aihub.py` 생성). **43,219줄 = 다운로드 = 검증 가능.**

| 처분·사유 | 도면 | 복구 경로 |
|---|---|---|
| ✅ dual(SPA+STR) 직접변환 | 3,345 | — |
| ✅ dual(중복라벨복구) | 231 | 같은 지문 SPA/STR 라벨 합집합 재변환 |
| ✅ 방만→V2V STR복구 | 4,053 | V2V(SPA mAP 0.90) |
| ✅ 구조만→V2V SPA복구 | 3,292 | V2V |
| 🛠 변환실패(dual 품질게이트) | 1,220 | 재변환 |
| 🛠 구조만 V2V 대기 | 1,051 | V2V 추가 |
| 🛠 방만 V2V 대기 | 995 | V2V 추가 |
| 🛠 **OBJ/OCR만**(공간라벨 없음) | 6,146 | **이미지직접 검출(objocr 2패스)** → 6,139도면 예측SPA 확보, G빌드 12,812세대(보정필요 베이스라인) |
| 🚫 중복(byte-identical 사본) | 18,231 | 1장 채택, 나머지 명시 |
| 🚫 비-FP(단면/입면/구조) | 4,655 | 불가(평면도 아님) |
| **합** | **43,219** | = 받은 raw |

- **라벨 구성(고유 평면도)**: dual 4,796 · spa_only 5,048 · str_only 4,343 · objocr 6,146 · nonfp 4,655.
- **43,219 vs 48,033 (확정)**: AI-Hub 명세는 48,033이나 실제 개방배포 zip(8 Train+4 Val 전량)은 **43,219**(균일 90%, 구축≠개방). 신뢰 기준=실제 받은 43,219. (AI-Hub 문의 접수)
- **중복라벨복구**: AI-Hub는 동일 PNG를 라벨종류마다 다른 키로 중복배포 → 지문(CRC32+크기) 기준 SPA+STR 합집합 재변환(`recover_dedup_merge.py`)으로 231지문 정정. dedup 0충돌 증명(`verify_dedup.py`).

---

## 3. 처분 모델 (검수·회계의 단일 정의)

- **✅ 사용(use)**: 그래프화돼 데이터셋 편입(직접변환 또는 V2V/objocr 복구).
- **🛠 보정필요(fix)**: 살릴 수 있으나 아직 안 됨(위상 보정·V2V 대기·변환 실패·OBJ/OCR만). **변환·보정 성공 시 → 사용으로 이동**(보정필요↓·사용↑).
- **🚫 제외(excl)**: 영구 폐기 — 비-FP·중복 사본 둘뿐.
- **불변식**: 출처별 `사용+보정필요+제외 = 받은 원본`. 검수 콤보·종합 패널이 같은 소스 → 항상 일치.
- **검수 콤보 = 「라벨 구성 × 처분」 완전 회계** (한눈에 데이터 구성). 카테고리 고정 프레임, 숫자만 이동. → **ADR-0005**.

---

## 4. 저장 2단 구조 (staging=작업 → releases=동결)

```
data/
  staging/<source>/    # 작업장(가변): graphs + manifest + ledger. 항상 현재 단일진실
    aihub/  cubicasa5k/  rplan/  corrected/    # corrected=Corrected 단일진실(자동+사람보정, ADR-0003/0009). 옛 이름 gline. [검증 2026-06-15]
  releases/            # 동결 스냅샷(불변)
    parsed/  corrected/      # Parsed/Corrected (ADR-0009). 옛 이름 tline/gline
    v0/                      # ⚠️ 레거시(옛 T-line) — git 추적 잔존, 전환중
    recipes/<ver>.json       # 선언적 레시피(provenance 필터)
```

- **버전 = 선언적 조합**. provenance 필터로 v0/v2·g0/g1 구분(2026-06-11 구현, **ADR-0005**):
  T-라인=manifest provenance(`recipes/*.json`), G-라인=그래프 `provenance.source`(`build_gline_auto.VERSION_SOURCES`).
- staging은 출처별·라인무관. 라인 분기는 releases부터. 옛 `processed/`·`topo_human`·분리 g0/g1 폐기(ADR-0003).

---

## 5. 데이터셋 버전 (학습 입력) — 실측 카운트

**T-라인 (위상그래프, `layout.nodes`)**
| 버전 | 구성 | 그래프 | split(tr/val/test) |
|---|---|---|---|
| **v0** | dual 정상변환(클린·기준) | **5,648** (2,636시트) | 4,531 / 599 / 518 |
| **v2** | dual + V2V + CubiCasa 사전학습 | **23,856** | — |
| global_all / global_rplan | 사전학습 풀 | 83,399 / 80,371 | train/val만 |

**G-라인 (위상+기하, `rooms` 2층 스키마)** — objocr 2패스 재빌드 완료(2026-06-11, n_err 0)
| 버전 | 구성 | 도면 | 세대(그래프) |
|---|---|---|---|
| **g0** | dual | 4,150 | **8,700** |
| **g1** | dual + spa_only·str_only·objocr 복구 | 18,503 | **40,495** |
| g_global | GLOBAL(RPLAN+CubiCasa) 사전학습 | — | 85,083 |

- **g1 구성(세대)**: objocr 12,812 · spa_only 11,618 · dual 8,700 · str_only 7,365 (합 40,495).
- **g1 분류_자동(세대)**: 사용 22,455 · 보정필요 14,590 · 제외 3,450. 보정필요=역할미상 등(알바 보정→사용 증량 여지). 제외=필수공간없음 등 hard.
- **도면 수**=동결 데이터셋에 그래프가 1개 이상 든 **고유 시트**(`freeze` n_plans). 빌드 *시도* 시트(구성_도면 dual 4,796·전체 19,826)와 다름 — 일부 시트는 진입했으나 유효세대(현관 보유 연결요소) 0세대라 제외됨. g0=dual만(재빌드 전후 세대 동일 8,700, 시드·dual 불변).
- **대칭**: T v0 ↔ G g0 (dual) · T v1 ↔ G g1 (+추가본).
- **동결 test = 균형 소버린**: AI-Hub dual 한정, APT/DEH/ROW 매크로 평균(원시분포 APT 94% 편중 보정). v0 test=518그래프. RPLAN/CubiCasa=사전학습 전용(test 미포함) → v0~vN 비교 불변.
- scale: AI-Hub=OCR 역산(도면별, `scale_ocr.py`) · CubiCasa=SVG 치수 · RPLAN=없음.

---

## 6. 스키마 (라인별 — 휘발성 상세는 코드)

- **T-라인**: `layout.nodes`(type / source-target). 구현 `schema.py`.
- **G-라인**: `rooms`(role / from-to / polygon, 2층 기하). 구현 `geomgraph.py` · 설계 [GEOMETRY_SCHEMA 흡수→ARCHITECTURE].
- 공통 meta: `status`(success/quarantine) · `reason` · `provenance`(출처) · `role`(benchmark/pretrain) · `corrected`(G 사람보정).

---

## 7. 검수 도구 (GUI — 수치마다 근거 도면)

https://plan2graph.aines.kr — 🧮종합현황 · 🏢AI-Hub(T) · 🧩AI-Hub(G) · 🏠CubiCasa · 📐RPLAN · 📘T도면생성 · 📗G도면생성. 기동 `bash scripts/start_dashboard.sh`(115).

---
관련: [ARCHITECTURE.md](ARCHITECTURE.md)(AI기술·모델·파이프라인) · [EXPERIMENTS.md](EXPERIMENTS.md)(결과) · [adr/](adr/) · [NOTES.md](NOTES.md)(역사 로그) · [ROADMAP.md §8](ROADMAP.md)(열린 질문)
