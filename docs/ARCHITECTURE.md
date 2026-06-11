# Plan2Graph 아키텍처 — AI 기술·모델·파이프라인·스키마 (정본)

> 시스템 전체상. SPEC(원charter) + V2V + RENDER + GEOMETRY_SCHEMA + KR_CONVENTIONS 통합본.
> 데이터 출처·카운트·버전은 [DATA.md](DATA.md), 학습 결과·모델 zoo는 [EXPERIMENTS.md](EXPERIMENTS.md), 결정은 [adr/](adr/).
> **휘발성 상세(시그니처·임계·코드)는 코드 포인터로만.** GUI(https://plan2graph.aines.kr)와 동일 내용을 문서로도 보존.

---

## 1. 목표와 두 패러다임 (T-라인 / G-라인, ADR-0002)

평면도(PNG+COCO 라벨 또는 이미지) → **방-문-방 위상 그래프** → 무결성 검증 → 학습 데이터셋 → **도면 생성**.

**두 라인은 데이터·스키마·생성방식이 다른 별개 패러다임, 절대 섞지 않음**(폴더·GUI 분리, 성능만 합쳐 비교=도면 품질).

| | **T-라인** (위상) | **G-라인** (위상+기하) |
|---|---|---|
| 원천 | 검출→자동 변환(SVG 없음) | 이미지/라벨→SVG→추출(SVG=단일진실) |
| 스키마 | `layout.nodes` (type, src-target) `schema.py` | `rooms` 2층 g-0.3 (role, polygon) `geomgraph.py` |
| 기하 생성 | 규칙기반 treemap `floorgeom.py` | **학습 기하모델** `train_geom`·`geom_gen` |
| 데이터 | `releases/tline/` (v0,v2,global) | `releases/gline/` (g0,g1,g_global) |
| 모델 | `runs/tline/` | `runs/gline/` |

---

## 2. AI 기술·모델 인벤토리 (실측 — runs/·src/)

| 기술 | 역할 | 모듈 | 학습 모델/산출 |
|---|---|---|---|
| **NL→제약 파싱** | 자연어 요구 → program(방 구성) | `text2graph.py` | 규칙기반(모델 없음) |
| **T-라인 위상생성** | program → 위상 그래프 (Neuro) | `train_gen`·`gen_loop`·`generators`·`model_baseline` | `runs/tline/` **~30개** — set-transformer(typed·v1·v2) × 사전학습(없음·global_all·global_cubicasa·global_rplan) × seed(1~4,42), + baseline(규칙) |
| **G-라인 기하생성** | 위상 → 실측 좌표 도면 | `train_geom`·`geom_gen`·`geom_correct`·`geomgraph` | `runs/gline/geom_g0` |
| **V2V 비전검출** | 이미지 → 방/구조 폴리곤(라벨 복구·생성) | `v2v_infer`·`v2v_export`·`yolo_train` | `runs/segment/v2v_runs/` **6개** (아래 §3) |
| **OCR** | 방이름·치수(scale) 역산 | `scale_ocr.py` (RapidOCR) | 사전학습 ONNX |
| **무결성·법규** | 위상/법규 검사·자기교정 | `rules`·`rules_legal`·`rules_swrl`·`ontology`·`law_api`·`legal_harvest` | legal/catalog 175조문, SWRL+HermiT |
| **도면 렌더** | 그래프 → 생성형 도면 + DXF | `cadrender.py`·`floorgeom.py` | — (ADR-0004) |

> 사업계획서 "Mask R-CNN"은 **옛 명칭** — 실제 검출은 **YOLOv8-seg(ultralytics)**. R-CNN 앙상블은 STR 보강 옵션(미구현).

---

## 3. 검출 (V2V — Vision-to-Vector, YOLO-seg)

이미지에서 방(SPA)·구조(STR)를 폴리곤으로 검출. 라벨 없는 도면(objocr·str_only)도 흡수 → T·G 공용 엔진.

**모델 원장** (`runs/segment/v2v_runs/`)
| 모델 | weights | imgsz·ep | mask mAP50 | 비고 |
|---|---|---|---|---|
| SPA | `spa_e5` | 768·5 | 0.90 | 미수렴 |
| SPA | `spa_yolov8n-seg_768_e100` | 768·≤100 | **0.964** | str_only 복구용(현행) |
| STR | `str_e50` | 768·50 | 0.565 | — |
| STR | `str_yolov8n-seg_1024_e50` | 1024·50 | **0.68** | 해상도↑가 레버(현행) |

**핵심 원칙**
- **mAP가 아니라 게이트 통과율(fix→use 전환)로 최적화** — mAP↑가 게이트↑를 보장 안 함(STR 0.565→0.68 교체 시 −122 회귀 사례). 상세 EXP는 [EXPERIMENTS.md].
- **union not replace** — 신·구 모델 모두 추론, 하나라도 게이트 통과 시 채택 → **회귀 0**. 실라벨 있으면 예측보다 우선.
- **objocr 이미지직접 2패스**(2026-06-11): 라벨 없는 다세대 시트 → ①전체시트 SPA로 방위치→세대 군집 분할 ②세대별 크롭 재검출(SPA·STR) → 시트좌표 누적. `v2v_infer.run_objocr`(`_cluster_units`). 결과 `data/v2v/predicted_img/`.
- 검출 약점: **SPA 강(0.90+)·STR 약**(문/창) → 위상 게이트 병목은 현관문 검출. wall은 검출 아닌 **방 폴리곤 경계서 기하 유도**.

---

## 4. 위상+기하 스키마 (G-라인 2층, g-0.3)

> 위상은 도면의 **문법**이지 그림이 아니다. geometry-rich graph = 생성모델이 학습·소비하는 그라운드 트루스.

- **Layer 1 (원시·SVG)**: 검출(YOLO-seg)+사람 교정. 편집 단위=SVG. `rooms/doors/windows/fixtures` 폴리곤.
- **Layer 2 (파생·JSON)**: SVG에서 **추출기가 계산**(`geomgraph.build`) — 사람이 입력 안 함(중복·모순 방지).
- 흐름: `원본 ─[SVG 변환]→ SVG ─[빌드]→ Layer2 JSON`. "SVG 변환 ≠ 빌드"(빌드=그래프 생성, 반복 가능 → 보정필요→사용 전환).

**Layer2 도출 필드(요약, 상세=`geomgraph.py`)**: room(role·polygon·area_m2·privacy·has_window·wall/door/window_ids) · wall(유도: interior/exterior+openings) · door(connects·width·subtype·orientation arc·is_entrance) · window(belongs_to·on_wall·orientation) · edge(via=door/open·privacy_transition·distance_from_entrance) · validation(hard→quarantine / soft→warning).

⚠️ 한계: 벽 공유판정 buffer tol≈18px(근사) · door.orientation은 arc 폴리곤일 때만 · SVG 자기완결성(창·기구 직렬화)은 `topoedit.to_svg` 잔여.

---

## 5. 생성·렌더 (도면 산출물 — ADR-0004)

**도면 렌더 = 두 산출물만**(위상도면/버블 다이어그램 폐기):
1. **생성형 도면(SVG/PNG)** — 화면에서 바로 보는 도면.
2. **AutoCAD(DXF)** — 사무소가 편집하는 작업도면(ezdxf, 레이어 분리).

- **T·G 공용 렌더 코어** `cadrender.py`: `from_geomgraph`(G)·`from_floorgeom`(T) → 공통 `Geometry` → `render_fig`/`render_dxf`.
- **자기교정 루프**(`autocorrect`=verify→fix→재검사): R1 겹침·R2 벽틈·R3 문 off-wall·R4 기구 방밖·R5 외곽 미폐합·R6 폭이상·R7 고립방·R8 치수불가. 잔여는 렌더에 경고(보정필요).
- 생성 경로(G): NL → `text2graph` program → 방 구성 → g0 면적 prior → 관례 인접 → `geom_correct` 자기교정 → 도면. GUI 📗(번호 흐름 1.파이프라인 2.모델 3.생성 4.데이터그래프보기).
- ⚠️ 병목 = 배치 실현(treemap 50%대) — 학습 기하모델·자기교정 고도화 대상.

---

## 6. 무결성·법규·한국 관행

**무결성 규칙**(`rules.py`, R1~R5): 고립덩어리·문없는방·현관도달성·현관존재·미해소문. T·G 공통(G판=`geomgraph.validate`).

**법규 엔진**(Neuro-Symbolic): 수치·위상은 파이썬이 grounding → boolean 단언 → **SWRL(산술 없는 논리)+HermiT**가 위반 클래스 추론(`rules_swrl`). 빠른 대량주석=`rules_legal`. 근거=국가법령정보센터 API(`law_api`), 카탈로그 175조문(`legal_harvest`, design 80/proc 40/gen 55).

**한국 아파트 관행**(`KR_CONVENTIONS` — 법규 ≠ 관행, 룰=데이터/레지스트리):
- **hard(불변)**: 방통과 금지(사적방은 connector 경유)·문없는방 금지·거주실 채광·안방 전용위생 접근·현관 도달성.
- **soft(유형변형, 점수)**: 메인동선(현관→전실→거실)·마스터스위트(신축 파우더룸 / 구축 직접 — 택1 정상, 유무 감점 금지)·습식군집·발코니확장.
- over-repair 금지(위상 오류를 억지 문 추가로 덮지 말 것).

---

## 7. 기술 스택·재현
- Python 3.11, `shapely`·`networkx`·`ultralytics`(YOLO)·`owlready2`(SWRL/HermiT)·`ezdxf`·`rapidocr-onnxruntime`·`matplotlib`·`streamlit`(GUI).
- 서버 115(env p2g, GPU1만 학습/추론). 빌드·학습 명령은 [OPERATIONS.md](OPERATIONS.md).

---
관련: [DATA.md](DATA.md) · [EXPERIMENTS.md](EXPERIMENTS.md) · [OPERATIONS.md](OPERATIONS.md) · [adr/](adr/)(0001~0005) · [ROADMAP.md §8](ROADMAP.md)(열린 질문) · [NOTES.md](NOTES.md)(역사 로그)
