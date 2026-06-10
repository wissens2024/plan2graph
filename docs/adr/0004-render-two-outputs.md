# ADR-0004: 도면 렌더링 — 두 산출물(생성형 + AutoCAD DXF) 공용 코어 + 자기교정 루프

Status: Accepted
Date: 2026-06-10
Deciders: wissens2024

## Context
"도면 생성"의 산출물이 모호했다. 위상도면(버블 다이어그램)은 사용자에게 **의미 없다**.
사용자가 필요로 하는 것은: ① 화면에서 바로 보는 **생성형 도면**(CAD 프로그램 불필요), ②
건축 사무소가 열어 편집하는 **AutoCAD 작업도면(DXF)**. 또 T-라인 treemap이 "이상한 집"을
내는 원인은 위상이 아니라 **배치/정합**([[geometry-realization-is-bottleneck]]).

## Decision
**도면 렌더는 두 산출물만 낸다. 위상도면 렌더는 폐기.** `src/plan2graph/cadrender.py` 단일 코어.

1. **두 산출물** (같은 기하, 렌더만 다름):
   - **생성형 도면** = `render_fig`(matplotlib) → SVG/PNG. 벽·문(arc)·창·기구 심볼·실명+면적·치수(mm).
   - **AutoCAD 도면** = `render_dxf`(ezdxf) → DXF. 레이어 분리(WALL/DOOR/WINDOW/FIXTURE/DIM/TEXT/ROOM)·치수·실명.
2. **T·G 공용 코어, 어댑터만 다름**(ADR-0002 스키마 분리 유지 — 출력만 공용 = 도면 품질 비교):
   - G: `from_geomgraph(g-0.3)` — 실측형(진짜 벽·문·창·기구).
   - T: `from_tline_graph(G)` — `floorgeom.layout_rooms`(treemap) → **박스형 그대로**("이상한 집" 유지). geo모델 불요.
3. **자기교정 루프** `autocorrect`(검사→고치기→재검사, 동일 패턴): `verify`(겹침·문off-wall·기구 방밖·scale)
   → `fix`(기구 방안 clamp·문 벽 스냅) → 재검사. **라운드별 기록(`correct_log`)을 GUI에 표시**
   (몇 바퀴·무엇을 고쳤나 — 사용자가 GUI만 봐도 보임).
4. **GUI**: 📗 G-라인 도면생성·📘 T-라인 도면생성 각각 **🖼 생성형 도면 + 📐 AutoCAD(DXF) 받기** 버튼.
   ⚖️ 비교 화면은 T∥G 생성형 도면 + 각 DXF(후속).

## Considered Alternatives
- **이미지 생성형(디퓨전)으로 진짜 도면** — 기각: 정밀 좌표·치수·편집·법규검증 불가(그림일 뿐). 구조형(벡터)이 사무소 요건.
- **BIM(IFC)** — 보류(후순위): 우리 스키마가 객체·의미라 자연스럽지만, 우선 SVG/DXF.
- **위상도면 유지** — 기각: 산출물로 의미 없음.

## Consequences
- Positive: 화면용 도면 + 사무소 작업도면을 **한 코어**로. T(박스형)↔G(실측형) **공정 비교**(같은 렌더). 자기교정 과정 가시화.
- Negative: 정확성은 입력 그래프 품질에 묶임(알바 보정 후↑). 전문 CAD 디테일(가구·구조그리드)은 후속.
- TODO: NL→모델생성 결과 렌더(geom_gen→geomgraph 어댑터) · T 어댑터 비교화면 · 가구/그리드 · DXF 치수체인 고도화 · IFC.

## 구현 (2026-06-10)
`cadrender.py`(Geometry·어댑터·autocorrect·render_fig/png/svg·render_dxf), `docs/RENDER.md`(설계·슈도),
ezdxf 서버 설치. 📗·📘에 버튼. 합성·실데이터 검증(G 실측형/T 박스형 도면 출력 확인).

관련: [[ADR-0002]](T·G 분리) · [GEOMETRY_SCHEMA](../GEOMETRY_SCHEMA.md) · [RENDER](../RENDER.md) · [[geometry-realization-is-bottleneck]]
