# Plan2Graph 데이터셋 개념 설계

> 범위: **데이터셋 생산 파이프라인만**. 출처 병합·학습·튜닝은 별도 문서.
> 목적: 여러 출처의 도면을 *출처별로 분리 처리*하되 **모든 도면이 그래프 변환까지 시도**되고,
> **정상/격리**로 분기되며, **GUI에서 원본 ∥ 그래프를 육안 검수**하고, **버전 조합**이 무한히
> 늘어도 재현 가능하게 한다.

상태: 개념 확정(구현 전). 구현은 §10 로드맵을 단계별 승인.

---

## 0. 결정 로그 (ADR 요약)

| # | 결정 | 대안 | 근거 |
|---|------|------|------|
| D1 | **통합 `data/staging/<source>/`** 로 출처별 작업장 일반화 | 글로벌을 `releases/global_*`에 그대로 둠 | AI-Hub가 이미 쓰는 `processed/`(작업장)→`releases/`(동결) 분리를 전 출처로 확장. 검수·교정·버전 메커니즘을 글로벌에도 그대로 재사용. |
| D2 | **버전 = 선언적 레시피 `recipe.json`** | 버전마다 명령형 freeze 스크립트 | 조합(출처×상태×역할)이 계속 늘어남 → 선언만 추가하면 재현·비교가 명시적. |
| D3 | **출처별 개별 검수 페이지 유지** (공통 불변식 강제) | 출처 파라미터화 단일 페이지 | 출처마다 원본 표현·필터가 달라(배제 카테고리/색렌더 등) 개별 페이지가 더 명확. 단 "원본∥그래프+정상/격리+결정버튼"과 `render_graph`는 공통 강제. |
| D4 | **출처 역할 = benchmark / pretrain** | 모든 출처에 test split | benchmark(AI-Hub)만 test 동결 보유 → 새 출처를 더해도 test 불변 → v0~vN 비교 타당성 유지. pretrain(글로벌)은 train/val만. 기존 `provenance="global_pretrain"` 태그를 정식화. |
| D5 | **T-라인 / G-라인 완전 분리** (폴더·GUI), 성능만 합쳐 비교 | 상위개념 스키마 통합 / 한 메뉴 위아래 스택 | 두 방식(자동 detection→그래프 vs 사람 SVG→추출)이 데이터셋·스키마·생성방식 모두 다른 별개 패러다임. 섞으면 혼선·은폐. 잣대=도면 품질. → [ADR-0002](adr/0002-tline-gline-separation.md) (§11) |

---

## 1. 불변식 (모든 출처가 지킴)

1. 도면 1장은 반드시 **"그래프 변환 시도"** 까지 도달한다 (Tier1 실패 → Tier2 폴백 → 그래도 실패면 격리).
2. 출력은 항상 **{정상(success) | 격리(quarantine)}**. 격리는 폐기가 아니라 *교정 대기*.
3. 정상·격리 도면은 GUI에서 **원본 ∥ 그래프**를 나란히 검수할 수 있다.
4. 버전은 **출처·상태·교정수준의 선언적 조합**이다(코드 아님).
5. 출처별 분리 처리. 단 레코드 스키마는 단일([schema.py](src/plan2graph/schema.py)).

---

## 2. 엔티티 모델

### ① Source (출처) — 정적 레지스트리
| 필드 | 값 예 |
|------|-------|
| `id` | `aihub` · `cubicasa5k` · `rplan_render` · `rplan_vector` |
| `modality` | `label_coco` · `svg_vector` · `index_map` · `render_raster` |
| `tier` | `1`(파싱) · `2`(비전 세그멘테이션) |
| `role` | `benchmark`(test 보유) · `pretrain`(train/val만) |

수명: 코드 상수(레지스트리). 원천 데이터는 **참조만**(zip/디렉터리 경로), 복제 안 함.

### ② Drawing (도면) — "모든 도면은 변환 시도"의 단위
`{source, native_id, raw_ref, tier_used}` · 수명 영구.

### ③ GraphRecord (그래프 레코드) — 변환 산출물
- 도면 1 → N 레코드 (AI-Hub는 세대 단위 분할, 글로벌은 보통 1).
- 형식 = 기존 `schema.py` + **신규 meta 필드**:
  - `meta.status` = `success | quarantine`
  - `meta.reason` = 격리 사유(문자열)
  - `meta.role` = `benchmark | pretrain` (§6) — **신규**
  - `meta.tier` = `1 | 2`
  - ⚠️ `meta.provenance`는 **기존 의미 유지**(`aihub_label` / `v2_pred` / `global_pretrain` = 라벨 출처·품질). role과 별개.
- 저장: `staging/<source>/graphs/<graph_id>.json`

### ④ Decision (검수 결정 / ledger) — append-only 감사
`{ts, source, graph_id, action, params, result, note}` ·
`action ∈ {approve, correct, exclude, requarantine}`.
출처 무관 동일 포맷(기존 AI-Hub ledger를 일반화). 저장: `staging/<source>/ledger.csv`.

### ⑤ Release / Version (버전) — 동결 스냅샷(불변)
`{version, recipe, graphs/, splits/, manifest.json}` · `releases/<version>/`.

---

## 3. 도면 생애주기 (출처 불문 동일 상태기계)

```
                   ┌─ Tier1 파싱 성공 ─┐
ingested ─attempt──┤                   ├─→ converted ─validate─┬─ success ──검수(approve)──→ RELEASE-able
                   └─ 실패 → Tier2 비전 ┘                       │
                                                               └─ quarantine ←─ fail/저품질
                                                                      │
                                              교정 알고리즘/수동 ──→ 재validate ──┘
```

- AI-Hub의 기존 `quarantine→교정→채택` 흐름을 **전 출처 표준**으로 승격.
- Tier 분기: 출처 modality로 결정. `svg_vector/index_map/label_coco(dual)` → Tier1. `render_raster/label_coco(single·zero)` → Tier2.
- RPLAN 렌더(Tier2)는 격리가 많이 나올 수 있음 — 정상적 결과(원칙: 시도+분기는 보장).

---

## 4. 저장 레이아웃 (D1)

```
data/
  staging/                         # 작업장: 검수·교정이 계속 일어남 (가변)
    aihub/        graphs/  accepted.csv  quarantine.csv  ledger.csv
    cubicasa5k/   graphs/  accepted.csv  quarantine.csv  ledger.csv
    rplan_render/ graphs/  accepted.csv  quarantine.csv  ledger.csv
  releases/                        # 동결 스냅샷 (불변) — 라인별 분리(ADR-0002, §11)
    tline/  v0/ v1/ v2/ ...        # T-라인(위상): 각 recipe.json graphs/ splits/{train,val,test}.txt manifest.json
    gline/  g0/ g_global/ ...      # G-라인(위상+기하)
    _frozen_test/  aihub.json      # benchmark 출처별 test 동결(§6)
```

- 기존 `processed/` → `staging/aihub/`, `releases/global_cubicasa` → `staging/cubicasa5k/`,
  `releases/global_rplan` → `staging/rplan_render/` 로 이전(§9 마이그레이션).

---

## 5. 버전 = 선언적 레시피 (D2)

**정본(입력)** = `releases/recipes/<version>.json` — 손으로 작성·편집하는 단일 진실원천.
`freeze(version)`이 이것을 읽는다(`release.load_recipe`). `rmtree(releases/<version>)` 재freeze에서
살아남도록 버전 디렉터리 *밖*에 둔다. ⚠️ 이 파일을 지우면 freeze가 기본 레시피로 추락(예: cubicasa pretrain 유실).

**생성(출력)** = `releases/<version>/recipe.json` — freeze가 동결 시점에 박아두는 사본(provenance).
손으로 편집 금지(다음 freeze가 덮어씀). 정본과 충돌하면 정본(`recipes/`)이 이긴다.

⚠️ **버전 구분 가능성(distinguishability)**: recipe의 `status`/`role`만으로는 v0(dual)과 v2(+V2V)를
구분할 수 없다 — staging "success"가 V2V 복구분을 포함하게 된 뒤로 두 버전이 같은 필터가 됨.
버전을 *선언적으로* 재현하려면 레코드에 **출처 품질(provenance: dual / v2v_pred …)** 이 박혀 있고
freeze가 그것으로 필터해야 한다(현재 미구현 — graph_id→manifest.reason 조인 필요).

정본 예 `releases/recipes/<version>.json`:

```jsonc
{
  "version": "v2",
  "include": [
    { "source": "aihub",      "status": "success", "correction": "applied" },
    { "source": "cubicasa5k", "status": "success", "role": "pretrain" }
  ],
  "test_from": ["aihub"],            // benchmark 출처의 동결 test만 사용
  "clean_filter": "현관==1"           // 단일세대 필터(기존 release._is_clean)
}
```

`freeze(version)` = 레시피를 읽어 staging에서 **선택 → 복사 → split → manifest** (현재 [release.freeze](src/plan2graph/release.py#L57)의 일반화). diff 아닌 완전 스냅샷.

| 버전 | 조합(예시) |
|------|-----------|
| v0 | aihub success-only (baseline) |
| v1 | aihub success + corrected |
| v2 | v1 + cubicasa5k (pretrain) |
| v3 | v2 + rplan_render (pretrain, Tier2) |

---

## 6. 출처 역할 — benchmark vs pretrain (D4)

조합이 섞일 때 **비교 타당성**의 핵심.

- **benchmark (AI-Hub)**: test 동결 보유 → 모든 버전의 공통 평가 잣대. test는 v0에서 한 번 동결, 전 버전 공유(기존 `_frozen_test.json` 불변식).
- **pretrain (CubiCasa·RPLAN)**: **train/val만**, test 없음. 사전학습용. 새 출처를 더해도 test가 안 변해 v0~vN 비교가 안 깨짐.
- 레코드 `meta.role`(benchmark|pretrain)로 태깅. `meta.provenance`(aihub_label/v2_pred/global_pretrain)와는 별개 축.

---

## 7. GUI 검수 모델 (D3 — 출처별 개별 페이지)

각 출처는 admin.py에 **자기 페이지**를 갖되, 아래 **공통 불변식**을 반드시 만족:

1. **좌: 원본 ∥ 우: 그래프** 동시 표시.
2. 목록은 **정상/격리** 상태로 필터.
3. 하단 **결정 버튼**: 승인 / 교정 / 제외 (→ ledger 기록).

**공통 부품(중복 방지)**:
- `render_graph(record)` = `review.record_to_graph` → `review.render_graph_fig` — **이미 존재**, 전 출처 공유. ← 글로벌 페이지에 현재 빠진 부분, 이걸 끼우면 "원본∥그래프" 충족.
- `decision()` / `ledger` 기록 = 기존 AI-Hub UI 재사용.

**출처별 고유(개별 페이지에 남김)**:
- 원본 렌더: AI-Hub=PNG+라벨오버레이 · CubiCasa=PNG+SVG폴리곤 · RPLAN=색칠 인덱스맵/렌더.
- 필터: AI-Hub=배제 카테고리 · 글로벌=정상/격리.

---

## 8. 스키마 버전 0.1 → 0.2 (가산적)

- 신규 `meta.status/reason/role/tier` 전부 **추가(backward compatible)**. 구 레코드 읽기 안 깨짐. (`provenance`는 기존 필드·의미 유지)
- `SCHEMA_VERSION = "0.2"`.

---

## 9. 마이그레이션 (무손실·롤백 가능)

1. `processed/graphs` → `staging/aihub/graphs` (이동), accepted/quarantine/ledger 동반.
2. `releases/global_cubicasa/graphs` → `staging/cubicasa5k/graphs`, `global_rplan` → `staging/rplan_render/`.
3. 백필: 구 레코드 `status` = 큐 소속에서, `role` = source 역할에서 유도(1회 스크립트).
4. 기존 `releases/v0` 는 **그대로 유지**(동결 불변). 새 freeze부터 recipe 기반.

---

## 10. 단계별 구현 로드맵 (각 단계 승인 후 진행)

| 단계 | 내용 | 산출 |
|------|------|------|
| P1 | `Source` 레지스트리 + 스키마 0.2 필드 + 백필 스크립트 | `sources.py`, schema 패치 |
| P2 | `staging/` 레이아웃 마이그레이션(무손실) | 이동 스크립트, 경로 상수 |
| P3 | 글로벌 페이지에 `render_graph` 끼워 "원본∥그래프" 충족 + 정상/격리·결정버튼 | admin.py(출처별) |
| P4 | `recipe.json` 기반 `freeze` 일반화 | release.py v2 |
| P5 | RPLAN 렌더 Tier2 진입로(앵커 없는 전체이미지 세그멘테이션) — 별도 설계 후 | v2v 확장 |

> P5(비전 Tier2)는 품질 리스크가 커 별도 설계. P1~P4는 데이터셋 구조 정비라 선행.

---

## 11. T-라인 / G-라인 분리 (ADR-0002 · 2026-06-09)

이 문서 §1~§10은 **T-라인(위상, 자동 detection→그래프)** 의 데이터 파이프라인을 정의한다. 이후 **G-라인(위상+기하, 사람 SVG→추출)** 이 추가됐고, 둘은 **데이터셋·스키마·생성 방식이 모두 다른 별개 패러다임**이라 **절대 섞지 않는다**.

| | T-라인 | G-라인 |
|---|---|---|
| 데이터 | `releases/tline/` (v0~) | `releases/gline/` (g0~) |
| 스키마 | `layout.nodes` (type / source-target) | `rooms` (role / from-to / polygon, [GEOMETRY_SCHEMA](GEOMETRY_SCHEMA.md)) |
| 원천 방식 | 검출→자동 변환 (SVG 없음) | 사람 SVG 편집→추출 (SVG=단일 진실) |
| 기하 생성 | 규칙기반 treemap (모델 없음) | 학습 기하모델 (train_geom) |
| 모델 | `runs/tline/` (gen-*) | `runs/gline/` (geom-*) |

- **분리 위치**: 폴더(위)·GUI(라인별 섹션). **성능은 한 화면에 합쳐 비교**(잣대 = 도면 품질).
- **§4 레이아웃 갱신**: `releases/<version>/` → `releases/{tline,gline}/<version>/`.
- staging(원천 작업장)은 출처별(aihub/cubicasa/rplan)로 **라인 무관**. 라인 분기는 **releases(빌드 산출)부터**. G-라인 사람 SVG는 `staging/topo_human/`.
- **지금은 자동화만으로 구조·품질↑**, 사람(알바) 검수·편집은 이후 단계.
