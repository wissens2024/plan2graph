# GEOMETRY_SCHEMA — 위상+기하 결합 표현 (계약서)

> ⚠️ **이 문서 = G-라인(위상+기하) 스키마, schema `g-0.3`.** T-라인(자동 detection→그래프, `layout.nodes`)과 **절대 섞지 않는다**(폴더·GUI 분리, [ADR-0002](adr/0002-tline-gline-separation.md)).
>
> **구현 상태(2026-06-10):** 추출기 `plan2graph/geomgraph.py`가 이 사양을 **자동으로 산출**한다(프로그램이 1차 완전 그래프를 냄 — 사람 없이 성립, 사람은 이후 업그레이드 레이어). g-0.3에서 이전 TODO를 모두 채웠다: **wall 유도(interior/exterior+openings)·door 여닫이 방향(arc)·window 벽귀속/방위·room bbox/외곽접촉·검증기(meta.status/reason 사유)**. 입력 경로는 자동(`topoedit.init_state(dr)`)·사람보정 동일 추출기. ⚠️ `door.orientation`은 검출 문이 **arc 폴리곤일 때만** 산출(bbox 사각형이면 `null`+`needs_orientation_review`) — 데이터 의존.

> 위상은 도면의 **문법**이지 그림이 아니다. 이 스키마는 생성 모델(Graph-to-Geometry,
> Wall/Door/Window)이 학습·소비하고 검증기가 검사하는 **그라운드 트루스 표현**이다.
> 단순 room-door-room 그래프로는 정교한 도면 생성에 부족하다 → geometry-rich graph.

## 0. 2층 원칙
- **Layer 1 (원시)** = 검출기(Mask R-CNN/YOLO)가 뽑고 **사람이 위상편집에서 교정**.
  편집·저장 단위(**SVG = 단일 진실**). 사람은 여기만 만진다.
- **Layer 2 (파생)** = SVG에서 **추출기가 계산**한 geometry-rich graph(JSON). 모델 입력.
  **파생 필드는 사람이 입력하지 않는다**(중복·모순 방지). `topoedit.extract_topology` 확장.

```
원본 ─[SVG 변환]→ SVG ─[빌드]→ Layer2 JSON(그래프)
                  ↑ 사람 교정(보정필요분)
```

## 0-1. 용어 — "SVG 변환" ≠ "빌드" (두 작업을 분리한다)
**빌드 = 그래프를 만드는 단계** (T·G 공통). 선행 단계만 라인마다 다르다:

| 라인 | 선행 (= SVG 변환) | **빌드** (= 그래프 생성) |
|---|---|---|
| **T** | (없음) | 원본 → 그래프 추출 |
| **G** | 원본 → SVG (**SVG 변환**) | SVG → 그래프 추출 (원본 dr의 기하 동반) |

- G에서 원본→SVG는 **빌드가 아니라 'SVG 변환'**이다. 한 작업으로 뭉치면 개념이 꼬인다 → **반드시 분리**.
- **빌드는 반복 가능**: 알바가 보정필요 도면의 SVG를 교정한 뒤 **빌드만 다시** 돌리면 분류(사용/보정필요/제외)가 갱신된다 → **보정필요 → 사용가능** 전환. 학습은 사용가능분으로만.
- 그래서 사용/보정필요 숫자는 시간에 따라 **계속 변한다** → 검수 화면 **분류 콤보에 도면·세대 숫자가 반드시 표시**돼야 한다.
- 구현(`scripts/build_gline_auto.py`): `--stage svg`(① SVG 변환) · `--stage build`(② 빌드, 반복) · 무인자(①후②).

## 1. 검출기 매핑 (현 환경 실제 클래스)
| Layer1 요소 | 검출 | 클래스(config) | 품질 |
|---|---|---|---|
| room polygon | Mask R-CNN (SPA) | `공간_*` 13종 | mAP~0.90, 정밀 폴리곤 |
| door | Mask R-CNN (STR) | `구조_출입문` | arc 폴리곤(방향 내재) |
| window | Mask R-CNN (STR) | `구조_창호` | 발코니/실외기실 창호는 통로 승격 |
| wall | (STR `구조_벽체` **약함**) | — | **검출 대신 방 폴리곤 경계서 유도** |
| fixture | YOLO (OBJ) | `객체_{변기,세면대,싱크대,욕조,가스레인지}` | 박스 |
| role hint | OCR | `OCR` | 사람 확정 |

## 2. Layer 1 — 원시 (SVG, 사람 교정)
```jsonc
{
  "meta": { "plan_id", "house": "APT|DEH|ROW", "scale_mm_per_px",
            "unit_boundary": "polygon(세대 외곽)" },
  "rooms":   [ { "id", "polygon", "class": "공간_*", "role": "세분역할", "source": "label|human" } ],
  "doors":   [ { "id", "polygon": "arc", "subtype" } ],
  "windows": [ { "id", "polygon" } ],          // 신규 캡처
  "fixtures":[ { "id", "bbox", "class": "객체_*" } ] // 신규 캡처(선택)
}
```
- role = 사람이 확정한 세분역할(거실/안방/침실/전용욕실/드레스룸/**파우더룸**/복도/전실 …).
- 연결(edge)은 사람이 "이 두 방 연결"만 선언 → via는 파생(아래).

## 3. Layer 2 — 파생 (geometry-rich graph JSON, 모델 입력) · schema `g-0.3`
각 필드 = **계산 규칙**(`geomgraph.build`). 손으로 안 넣는다. **호환 불변식**: `rooms` dict 키 = 정수 node id, `edges.from/to`·`doors.connects` = 정수(GUI `_disp_id`·집합비교가 정수 의존 → 문자열化 금지).

상위 구조:
```jsonc
{ "schema_version":"g-0.3", "plan_id","house","scale_mm_per_px",
  "bbox_px":[x,y,w,h],                      // 세대 외곽
  "n_rooms","n_edges","n_walls","n_doors","n_windows",
  "rooms":{ "<nid>": {room} }, "walls":[wall], "doors":[door], "windows":[window],
  "edges":[edge], "validation":{...}, "meta":{status,reason,...} }
```

### room  (키 = 정수 node id)
| 필드 | 도출 |
|---|---|
| base / role | 라벨 원종류 / 세분역할 |
| polygon | 외곽 좌표열(px) |
| bbox_px | polygon.bounds → [x,y,w,h] |
| centroid / centroid_norm | polygon.centroid / 세대 bbox 기준 0~1 |
| area_px / area_m2 | polygon.area / × scale² |
| aspect_ratio | minimum_rotated_rect 장변/단변(≥1) |
| perimeter_px | 외곽 둘레 |
| exterior_len_px / touches_exterior | 다른 방과 공유 안 한 경계 길이 / >5% 둘레 |
| has_window / n_windows | 방에 귀속된 window 수 |
| fixtures[] | 방 폴리곤에 포함된 fixture(객체_*) |
| privacy | role→{public,private,service,structure,exterior} (geomgraph.PRIVACY, 전 role 커버) |
| wall_ids / door_ids / window_ids | 이 방에 접한 wall·door·window id |

### wall (유도 — 검출 안 함) ✅구현
| 필드 | 도출 |
|---|---|
| id | `w{k}` |
| segment | 방 폴리곤 경계 선분([[x1,y1],[x2,y2]]) |
| type | 두 방 공유(buffer tol≈18px 판정)=`interior` / 한 방(외곽)=`exterior` |
| length_px / length_m | 선분 길이 / × scale |
| rooms | interior=[a,b] / exterior=[a] |
| openings[] | 그 벽에 매핑된 door_id·window_id |

### door ✅구현 (via=door 엣지 ↔ 검출 문 결합)
| 필드 | 도출 |
|---|---|
| id / connects[a,b] | `d{k}` / 엣지 양방 |
| position / polygon / bbox_px | 검출 문 중심 / arc 폴리곤(있으면) / COCO bbox |
| width_px / width_m | bbox 단변 / × scale |
| subtype | 검출 세부유형(여닫이문 등) |
| orientation | **arc 폴리곤서 hinge·swing_dir_deg·radius**(부채꼴 등거리로 추정). bbox면 `null` |
| needs_orientation_review | arc 없음/저신뢰 → 사람확인 플래그 |
| on_wall | 문 위치 최근접 wall.id(같은 방쌍 벽 우선) |
| is_entrance | 한쪽 방이 `현관` |

### window ✅구현
| 필드 | 도출 |
|---|---|
| id / belongs_to | `win{k}` / 최근접 방 |
| position / bbox_px / width_px·m | 중심 / COCO bbox / 단변 |
| on_wall | 최근접 벽(외벽 우선) |
| orientation_deg | on_wall 선분 법선(상대값 — 도면 방위 알면 절대화) |

### edge (위상)
| 필드 | 도출 |
|---|---|
| from / to | 방 id(정수) |
| via | door(문 존재) / open(벽 미피복 개구부) |
| door_id | via=door면 결합된 door id |
| privacy_transition | from.privacy → to.privacy (예: public_to_private) |
| distance_from_entrance | 현관에서 그래프 BFS 홉수 |

### validation / meta — 회계 사유(`geomgraph.validate`) ✅구현
T-라인 `rules.validate`의 G판. 데이터셋 숫자·분류·사유표의 소스(데이터셋 본질=숫자·카테고리).
- **hard(→`meta.status=quarantine`, 제외후보)**: `면적없음`·`방부족`(<5)·`필수공간없음`(현관·거실·침실·주방·화장실 결손)·`위상단절`(비연결).
- **soft(→warning, 보정후보)**: `역할미상`(privacy=other 또는 role=기타)·`현관없음`·`문폭없음`·`via미상`.
- `validation` = `{passed, reasons:[hard], warnings:[soft]}`. `meta.status/reason` = `dataset_status.scan_status`가 읽어 use/fix/excl 회계로 집계.

## 4. 주의 (현실 한계)
- **벽 정밀도 천장**: 사람/검출 폴리곤은 근사 → 공유 변이 정확히 안 맞음. buffer(tol≈18px, 벽두께 가정)로 공유 판정. 픽셀 수천 좌표계에선 안정(실측 23방 → exterior 44/interior 50 합리). 작은 좌표계는 tol 재설정 필요. **정밀 렌더 디테일은 생성 AI 몫**.
- `door.orientation`은 **검출 문이 arc 폴리곤일 때만**. bbox 사각형이면 `null`+`needs_orientation_review`(데이터 의존 — STR 검출 품질에 좌우).
- 도면 방위(남향) 메타 없으면 `window.orientation_deg`는 상대값까지만.
- ⚠️ **SVG 자기완결성(남은 항목)**: `geomgraph.build`는 창·기구·문 arc를 `dr`(검출 원시)에서 읽는다. `topoedit.to_svg`가 아직 이들을 직렬화하지 않아 **SVG만으로는 재추출 불가**(dr 동반 필요). "SVG=단일 진실"을 완성하려면 `to_svg`에 window/fixture/door-arc 직렬화 추가 필요 — `topoedit`(GUI) 소관.

## 5. 데이터셋 관리 (별도 골드셋 없음)
- **데이터셋은 하나.** 자동 검출 결과도 데이터셋, 사람 보정도 데이터셋(보정=품질↑/증량).
  geometry-rich JSON = **full**(전체 세대). **소량 골드 코퍼스 따로 만들지 않음.**
- 레코드 메타로만 구분: `corrected: true/false`, 출처, 신뢰도 → 학습 레시피가 플래그로 필터.
- **'골드셋'은 평가용일 때만** = 데이터셋에서 유형 균형(APT/DEH/ROW, 신축/구축) **선별 subset**
  (기존 frozen balanced test). 별도 생성 아니라 selection.

| 소스 | Layer1 채움 | 용도 |
|---|---|---|
| AI-Hub | 전부(기하+위상). 자동 + 일부 사람 보정 | 데이터셋 본체(기하+위상) |
| RPLAN | 위상/인접만(기하·벽 없음) | 위상 prior 대량 (Text-to-Graph) |
| CubiCasa | 기하·벡터 | 기하 사전학습 |

> 주의: "수량≠품질"은 *노이즈 확장이 해로울 수 있다*는 경험칙이지, 소량 골드를 따로 만들라는 뜻 아님.

관련: `KR_CONVENTIONS.md`(무엇이 정상인가, 법규와 분리) · `DATASET_DESIGN.md` · `ROADMAP.md`.
