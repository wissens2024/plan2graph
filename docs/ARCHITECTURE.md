# Plan2Graph 아키텍처 — AI 기술·모델·파이프라인·스키마 (정본)

> 시스템 전체상. 데이터·카운트는 [DATA.md](DATA.md), 학습 결과·측정값은 [EXPERIMENTS.md](EXPERIMENTS.md),
> 결정은 [adr/](adr/)(요약 색인 [adr/README.md](adr/README.md)), 운영은 [OPERATIONS.md](OPERATIONS.md).
> **표기: `[검증]`=코드·측정·실행으로 확인 / `[추론]`=아직 미검증 가설.** 휘발성 상세(시그니처·임계)는 코드 포인터로만.
> 최신화 2026-06-15 (ADR-0006~0012 반영, 옛 T/G·treemap·g-0.3 폐기 표기).

---

## 1. 목표와 파이프라인

**목표:** 입력(방 구성·관계) → **완성 건축 도면**(벽·방·문·창·가구·치수) → 이미지 + **AutoCAD DXF**, 한국 법규 준수. 박스+색칠이 아니라 *그려서 쓰는 도면*. (ADR-0006, [adr/README.md](adr/README.md) §1)

```
AI-Hub(한국) 원본 PNG+COCO 라벨  +  RPLAN·CubiCasa(글로벌)
  → R2G 파싱(비전검출 V2V + 조립 + 규칙)
  → 통일 그래프 (geomgraph g-0.4)              [스키마 §4]
  → 두 조건 비교: [Parsed=파서출력]  ∥  [Corrected=사람 정보보정]   (ADR-0008/0009)
  → 생성 엔진                                   [생성 §5]
  → 완성층(neuro-symbolic): 문·창 세부·가구·치수·축척
  → cadrender → 이미지 + DXF                    (ADR-0004)
```

- **`[검증]` 비교축 = Parsed vs Corrected** (= 사람 보정의 가치, human-correction ablation). **옛 "T-라인/G-라인"·"treemap"·"기하 A/B"는 폐기**(ADR-0008/0009, adr/README §5).

---

## 2. 생성은 두 부분 — 위상은 풀렸고, 기하가 미해결 (가장 중요)

> "도면이 안 그려진다"의 정체를 정확히: **위상(어느 방이 연결되나)은 작동, 기하(실제 좌표 도면)는 미해결.**

| 부분 | 무엇 | 상태 | 모듈 |
|---|---|---|---|
| **위상 생성** | program(방 구성) → 방-방 인접 그래프 | **`[검증]` 작동·측정 완료** (EXPERIMENTS.md: 5시드·동결test·ablation) | `train_gen`·`generators`·`gen_loop`·`eval_gen` |
| **기하 생성** | 위상 → 실제 좌표 도면(벽·방 폴리곤) | **`[검증]` 미해결** — 박스 회귀 사망, DiffPlanner 베이스라인은 산포(비응집) | DiffPlanner(`~/diffplanner_work`)·(폐기)`train_geom`/geom_g0 |

### 2-A. 위상 생성 (작동) — set-transformer 링크예측기
- 방 타입을 **순서무관 집합**으로 보고 어떤 방끼리 무엇(door/open/balcony)으로 연결되는지 예측. `Embedding(48)`→`TransformerEncoder`(2층·4헤드)→방쌍 `[h_i+h_j,|h_i−h_j|,g]`→edge(BCE)+via(CE). (`train_gen._build_model`)
- ADR-0001 Generator 추상화(arch 레지스트리·체크포인트 디스패치). type조건(house 임베딩)=`generators.typed`.

### 2-B. 기하 생성 (미해결) — 실제 도면 좌표
- **`[검증]` 박스 회귀(geom_g0/`train_geom`) 사망**: 방=축정렬 bbox라 정답(GT)조차 99% 겹침(ADR-0006). 폐기.
- **`[검증]` DiffPlanner = 작동 베이스라인/헤지**(ADR-0006): 벡터직접 디퓨전 3스테이지(node→adjacency→partitioning), RPLAN FID 1.23 재현. 한국 샘플 생성·렌더 가능하나 **8k 얕은 학습 + 박스/산포 표현 → 배치 비응집**(방 산포·경계이탈·중복, 2026-06-15 실측 `docs/runs/`).
- **`[검증]` DiffPlanner는 RPLAN→한국 전이 불가**: 임베딩 차원 불일치(RPLAN 8방/6범주 vs 한국 18방/13역할). 한국은 맨바닥 학습뿐.
- **`[추론]` 목표 타깃 = wall-cycle + opening 토큰**(ADR-0012, GSDiff류): bbox 폐기, ONE 엔진 유지하되 *생성 타깃*을 코너+벽+방사이클+역할로. 아직 데이터 표현·모델 미구현(연구 트랙).

### 2-C. `[검증]` 학습에서 이미 측정된 사실 (EXPERIMENTS.md — 재실험 금지)
- **사전학습(RPLAN/CubiCasa)은 무익하거나 악화**(5시드 확정). 클린 한국 v0이 최선(마이크로 adj_L1 0.088), 글로벌만은 처참(0.50~0.76). → *균형 한국 데이터가 핵심*(소버린 논지).
- **균형 매크로에선 신경망 > 규칙기반**(0.188 vs 0.205) — 옛 "규칙기반 최강"은 APT편중 착시.
- **type조건(주거형태)이 최대 레버**(매크로 0.123), 모델 용량 2배도 개선(0.166).
- ⚠️ adj_L1 한계: 방 타입이 역할 무구분(안방/침실·부부/공용 화장실 미분리) → 역할 위상 오류엔 관대(EXPERIMENTS §5-1).

---

## 3. 검출 (V2V — Vision-to-Vector, YOLO-seg)

이미지에서 방(SPA)·구조(STR)를 폴리곤 검출. 라벨 없는 도면(objocr·str_only)도 흡수.

**모델 원장** (`runs/segment/v2v_runs/`)
| 모델 | weights | imgsz·ep | mask mAP50 | 비고 |
|---|---|---|---|---|
| SPA | `spa_yolov8n-seg_768_e100` | 768·≤100 | **0.964** | 현행 |
| STR | `str_yolov8n-seg_1024_e50` | 1024·50 | **0.68** | 해상도↑가 레버(현행) |

- **`[검증]` mAP가 아니라 게이트 통과율(fix→use)로 최적화** — mAP↑가 게이트↑ 보장 안 함(STR 0.565→0.68 교체 시 −122 회귀 사례).
- **union not replace** — 신·구 모델 모두 추론, 하나라도 게이트 통과 시 채택 → 회귀 0. 실라벨 우선.
- **objocr 이미지직접 2패스**: 라벨 없는 다세대 시트 → 전체시트 SPA로 세대 군집분할 → 세대별 크롭 재검출. `v2v_infer.run_objocr`.
- 검출 약점: SPA 강(0.90+)·STR 약(문/창) → 위상 게이트 병목=현관문. wall은 검출 아닌 **방 폴리곤 경계서 기하 유도**.
- 사업계획서 "Mask R-CNN"은 옛 명칭 — 실제는 **YOLOv8-seg**.

---

## 4. 통일 그래프 스키마 (geomgraph g-0.4, ADR-0010)

> 위상은 도면의 **문법**(제약·무결성 골격)이지 그림이 아니다. geometry-rich graph = 생성모델이 학습·소비하는 그라운드 트루스. 전체 필드 = `docs/for_review/korean_dataset.md` g-0.4 절 · 구현 `geomgraph.py`.

- **2층 구성**: Layer1(원시 폴리곤, 검출+사람 교정) → Layer2(파생, `geomgraph.build`가 계산, 사람 입력 X).
- **방 = 벽사이클 노드**(복도·전실도 별도 노드). 방-방 **경계 = `wall|open|door` 태그** — `open`이면 벽 안 그림(한국 오픈플랜). **불변: 벽 없는 곳에 벽 안 그림.** (ADR-0010)
- **g-0.4 신규(★)**: `walls.thickness_mm`(외200/내120) · `edges.boundary` · 상위 `fixtures[]`(Tier A 검출 5종 + Tier B 역할추론 `fixture_catalog.py`) · `dimensions[]` · `scale{mm_per_px,source}` · 검증 불변조건(room=closed cycle, door=1 wall 위, 창=exterior, 현관 reachable — ADR-0012).
- ⚠️ `[검증]` 데이터 채움률(300표본): 방폴리곤 100%·문 97%·창 96%·**문스윙 57%·축척 0%·기구(데이터) 0%**(OBJ는 욕실/주방 5종만, 소파·침대 없음 → 역할추론). 갭은 완성층이 neuro-symbolic으로 채움(ADR-0006).

---

## 5. 생성·렌더 (도면 산출물 — ADR-0004)

**렌더 = 두 산출물**(위상도면/버블 폐기): ① 생성형 도면(SVG/PNG) ② AutoCAD DXF(ezdxf, 레이어 분리).
- **공용 렌더 코어** `cadrender.py`: `from_geomgraph` → 공통 `Geometry` → `render_fig`/`render_dxf`. 어댑터 `diffplanner_to_cadrender.py`(엔진 출력→렌더).
- **자기교정 루프** `autocorrect`(verify→fix→재검사): R1 겹침·R2 벽틈·R3 문 off-wall·R4 기구 방밖·R5 외곽 미폐합·R6 폭이상·R7 고립방·R8 치수불가. 잔여는 렌더에 경고(보정필요).
- **`[검증]` 현재 병목 = 기하 생성(§2-B)**, 렌더·완성층 아님. DiffPlanner 8k 출력은 렌더는 되나 배치가 비응집. 개선 레버 = 학습 깊이 + 표현(wall-cycle, ADR-0012).

---

## 6. 무결성·법규·한국 관행

**무결성**(`rules.py` R1~R5 / G판 `geomgraph.validate`): 고립덩어리·문없는방·현관도달성·현관존재·미해소문.

**법규 엔진**(Neuro-Symbolic): 수치·위상은 파이썬이 grounding → boolean 단언 → **SWRL(산술 없는 논리)+HermiT**가 위반 클래스 추론(`rules_swrl`). 빠른 대량주석=`rules_legal`. 근거=국가법령정보센터 API(`law_api`), 카탈로그 175조문(`legal_harvest`). 생성 루프에 verify→repair 연결.
- **`[검증]` scale 종속성**: 채광·환기 면적비(1/10·1/20)=scale-invariant(축척 없이 검증 가능) / 최소면적(㎡)만 절대축척 필요(문폭 앵커로 해결).

**한국 아파트 관행**(`KR_CONVENTIONS` — 법규≠관행, 룰=레지스트리):
- **hard(불변)**: 방통과 금지(사적방은 connector 경유)·문없는방 금지·거주실 채광·안방 전용위생 접근·현관 도달성.
- **soft(유형변형)**: 메인동선(현관→전실→거실)·마스터스위트(신축 파우더룸/구축 직접 택1)·습식군집·발코니확장.
- over-repair 금지(위상 오류를 억지 문 추가로 덮지 말 것).

---

## 7. 기술 스택·재현
- Python 3.11, `shapely`·`networkx`·`torch`+DiffPlanner(디퓨전)·`ultralytics`(YOLO)·`owlready2`(SWRL/HermiT)·`ezdxf`·`rapidocr-onnxruntime`·`matplotlib`·`streamlit`(GUI).
- 서버 115(env p2g, **GPU1만** 학습/추론, GPU0=WiSentinel). 명령은 [OPERATIONS.md](OPERATIONS.md). 엔진 별도 트리 `~/diffplanner_work`(체크포인트 `ckpt_kr/`).

---
관련: [DATA.md](DATA.md) · [EXPERIMENTS.md](EXPERIMENTS.md) · [OPERATIONS.md](OPERATIONS.md) · [adr/README.md](adr/README.md)(결정 색인 0001~0012) · [docs/runs/](runs/)(실행 기록)
