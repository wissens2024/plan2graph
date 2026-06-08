# GEOMETRY_SCHEMA — 위상+기하 결합 표현 (계약서)

> 위상은 도면의 **문법**이지 그림이 아니다. 이 스키마는 생성 모델(Graph-to-Geometry,
> Wall/Door/Window)이 학습·소비하고 검증기가 검사하는 **그라운드 트루스 표현**이다.
> 단순 room-door-room 그래프로는 정교한 도면 생성에 부족하다 → geometry-rich graph.

## 0. 2층 원칙
- **Layer 1 (원시)** = 검출기(Mask R-CNN/YOLO)가 뽑고 **사람이 위상편집에서 교정**.
  편집·저장 단위(**SVG = 단일 진실**). 사람은 여기만 만진다.
- **Layer 2 (파생)** = SVG에서 **추출기가 계산**한 geometry-rich graph(JSON). 모델 입력.
  **파생 필드는 사람이 입력하지 않는다**(중복·모순 방지). `topoedit.extract_topology` 확장.

```
검출 → Layer1 초안 → [사람 교정] → SVG → 추출기 → Layer2 JSON(gold)
```

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

## 3. Layer 2 — 파생 (geometry-rich graph JSON, 모델 입력)
각 필드 = **계산 규칙**. 손으로 안 넣는다.

### room
| 필드 | 도출 |
|---|---|
| area | polygon.area × scale² |
| aspect_ratio | minimum_rotated_rect 가로/세로 |
| centroid | polygon.centroid |
| exterior_boundary_length | polygon 경계 ∩ unit_boundary 길이 |
| has_window / n_windows | 방 경계에 스냅된 window 수 |
| fixtures[] | 방 폴리곤에 포함된 fixture |
| privacy_class | role→{public:거실·주방·현관, private:침실·안방·드레스룸·욕실, service:발코니·실외기실·복도·전실} |

### wall (유도 — 검출 안 함)
| 필드 | 도출 |
|---|---|
| segment | 방 폴리곤 경계 선분(허용오차 스냅) |
| type | 두 방이 공유 = `interior` / 한 방만(외곽) = `exterior` |
| openings[] | 그 선분 위에 놓인 door_id·window_id |

### door
| 필드 | 도출 |
|---|---|
| connects[a,b] | 사람 선언 edge / 문 최근접 2방 |
| width | arc 폴리곤 현(弦) 길이 × scale |
| orientation | arc 폴리곤서 hinge·스윙(애매하면 사람 확인 플래그) |
| wall_seg | 문↔최근접 wall.segment 스냅 |
| is_entrance | 한쪽 방이 `공간_현관` |

### window
| 필드 | 도출 |
|---|---|
| wall_seg / room | 최근접 외벽·방 |
| orientation | wall_seg 법선(남향 등 방위는 도면 방위 알면 추가) |

### edge (위상)
| 필드 | 도출 |
|---|---|
| from / to | 방 id |
| via | door(문 존재) / open(벽 미피복 개구부 ratio≥임계, config.OPEN_*) |
| privacy_transition | from.privacy_class → to.privacy_class (예: public_to_private) |
| distance_from_entrance | 현관에서 그래프 BFS 홉수 |

## 4. 주의 (현실 한계)
- **벽 정밀도 천장**: 사람 클릭 폴리곤은 근사 → 공유 변이 정확히 안 맞을 수 있음. 스냅·허용오차 필요. 정밀 벽 좌표는 여기서 결정. **정밀 렌더 디테일은 생성 AI 몫**.
- 도면 방위(남향) 메타 없으면 orientation은 상대값까지만.
- 신규 캡처는 **window·fixture·door 기하 보존**뿐. 나머지는 전부 계산.

## 5. 소스별 채움 (제한 리소스 분업)
| 소스 | Layer1 채움 | 용도 |
|---|---|---|
| AI-Hub | 전부(기하+위상, 사람 보정) | **rich gold** (소수 정밀·균형 APT/DEH/ROW) |
| RPLAN | 위상/인접만(기하·벽 없음) | **위상 prior 대량** (Text-to-Graph) |
| CubiCasa | 기하·벡터 | **기하 사전학습** |

관련: `KR_CONVENTIONS.md`(무엇이 정상인가, 법규와 분리) · `DATASET_DESIGN.md` · `ROADMAP.md`.
