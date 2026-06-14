# 한국형 데이터 표현 (외부 검토용 — ChatGPT 등)

> plan2graph가 한국 AI-Hub 아파트 평면도를 변환한 **통일 그래프(geomgraph g-0.4, ADR-0010)**.
> 실제 샘플: `sample_parsed.json`(파서출력 7방) · `sample_corrected.json`(사람보정 9방).
> 모든 수치는 실측(2026-06). 좌표 단위 = 픽셀(px), 축척 mm/px는 대부분 미정(아래).

## 1. 규모·처분 (세대 단위)
- 변환된 통일 그래프: **40,495 세대**(unit). 도면(시트) 1장 = 여러 세대 타일.
- 중복 정정: 다운로드 41,556 엔트리 → **고유 평면도 15,108**(같은 도면 중복 배포).
- 처분(자동 분류): **사용 22,455 / 보정필요 14,590 / 제외 3,449**. 사람 보정완료(edits/) = 현재 소수(시작 단계).
- 라벨 구성(원본 라벨 종류별): dual(공간+구조 둘다) **8,700** / spa_only **11,618** / str_only **7,365** / objocr(객체만, 방라벨 없음) **12,812**. → *온전한 dual은 21%뿐.*

## 2. 데이터 채움률 (300 표본 실측)
| 요소 | 채움률 | 비고 |
|---|---|---|
| 방 폴리곤 | 100% | 좌표 있음 |
| 문 | 97% | 위치·폴리곤 |
| 창 | 96% | 외벽 위 |
| 문 스윙 방향 | **57%** | 폴리곤서 추론 가능분만 |
| 기구(가구) | **0%** | 없음 → 역할 기반 추론 필요 |
| 축척(mm/px) | **0%** | 절대치수 미정 → 앵커/사람입력 필요 |

## 3. 스키마 (geomgraph g-0.4 — ADR-0010)
> g-0.3 → g-0.4 변경: 완성 7레이어(벽두께·문스윙·창·가구·치수) + 방-방 경계 태그 + 축척 해소.
> **★=g-0.4 신규/변경.** g-0.3 산출물은 빌더 갱신 후 점진 채움.

최상위 키: `schema_version("g-0.4"), plan_id, house, ★scale{mm_per_px,source,anchor,confidence}, ★units("mm"), bbox_px, ★bbox_mm, n_*, rooms, edges, walls, doors, windows, ★fixtures, ★dimensions, validation, meta, unit_id, corrected, provenance`

- **rooms** (dict, key=str(int)): `role`·`base`·`is_connector`(복도·전실=별도 노드)·★`connector_origin`("wall|open|derived")·`centroid`·`bbox_px`·`area_px`·`area_m2`(scale서 도출)·`aspect_ratio`·`touches_exterior`·`has_window`·`n_windows`·★`fixture_ids[]`·`privacy`·`wall_ids`·`door_ids`·`window_ids`·`polygon`·`dist_from_entrance`·★`label{name,place_at}`
- **edges** (list): `{from, to, ★boundary:"wall"|"open"|"door", door_id, ★shared_segment[[x,y],[x,y]], privacy_transition, dist_from_entrance}` — **★boundary가 렌더의 벽 그림 여부 결정**(open=벽 안 그림→오픈플랜).
- **doors** (list): `{id, connects:[a,b], position, polygon, ★width_mm, ★type("single|double|sliding|중문|folding|pocket"), ★swing{hinge_room,hinge_point,direction:"in|out",angle}, on_wall, needs_orientation_review}`
- **windows** (list): `{id, belongs_to, position, bbox_px, ★width_mm, ★sill_height_mm, ★type("sliding|casement|fixed"), on_wall}`
- **walls** (list): `{id, a, b, ★type("exterior|interior"), ★thickness_mm(외~200/내~120), ★sides[room,room]}` — **open 경계는 walls에 없음(=안 그림)**.
- ★**fixtures** (list, 신규): `{id, name, category, room_id, position, footprint, orientation_deg, size_mm[w,d], source("obj_detected"|"role_inferred"), confidence}` — Tier A(OBJ 5종, rotation→orientation) + Tier B(역할 카탈로그 `fixture_catalog.py`).
- ★**dimensions** (list, 신규): `{id, type("linear"|"overall"), from, to, value_mm, refers_to}` — scale+기하서 *도출*(사람 입력 X).
- **validation**: `{passed, reasons[], warnings[], info[]}` (R1~R8 + ★g-0.4 불변: 벽 없는 경계=open, 복도 노드 존재).
- **meta**: `{house_type, scale_mm_per_px, status, reason, n_*}` + ★**다중도메인 조건(ADR-0013)**: `country`(CN/KR/EU)·`dataset`(RPLAN/AIHUB_KR/CubiCasa, provenance)·`housing_type`(apartment/detached/rowhouse)·`label_schema`(rplan_6cat/korean_13cat/cubicasa_Ncat). AI-Hub=KR/AIHUB_KR/korean_13cat. (max_rooms는 엔진 config, 그래프 아님 — 13=종류수≠18=방수)
- **DXF 레이어**(렌더 규약): `A-WALL/A-DOOR/A-GLAZ/A-FURN/A-DIM/A-AREA/A-ANNO`.

⚠️ **두 개의 품질 게이트가 있음**(헷갈리기 쉬움):
- `geomgraph validation`(R1~R8): 분리덩어리·문없는방·도달불가·현관없음·미해소문 등 *구조 무결성*.
- `plan_quality.classify`(엔진 GATE-0): 현관≠1·거실≠1·거실오라벨·발코니/기타 과다·침실<화장실 등 *엔진 입력 적격성*.
- 둘은 기준이 달라 한쪽 통과·한쪽 실패가 생김.

## 4. 역할 어휘 (23종, 사람 지정)
`거실, 주방, 현관, 침실, 안방, 화장실, 욕실, 전용화장실, 전용욕실, 드레스룸, 파우더룸, 발코니, 실외기실, 다목적공간, 복도, 전실, 기타, 구조물, 실외, 엘리베이터홀, 계단실, 엘리베이터, 알파룸`
- **알파룸** = 한국형 유연 기타공간(문 자유, 공부방/손님방/취미방, 도면 표시명 "알파룸").
- 연결공간 = 복도·전실(파우더룸도 기능상 허브).

## 5. 한국 아파트 정준 위상 (목표 템플릿)
```
현관 →[좁은 복도]→ 거실  (복도에 욕실·침실 문이 달림)
안방 →[화장대 전실/파우더룸]→ 드레스룸 · 전용욕실
```
이 **연결공간 2종(복도, 파우더룸/전실)이 R2G 파서에서 옆 큰 방에 흡수**되어 위상 허브가 사라질 수 있음. ⚠️ **단 ADR-0010: 길쭉/비직사각 거실 자체는 결함 아님(한국 정상 기하)** — 결함은 *벽이 있는데 흡수된* 경우뿐. 그 경우만 사람 보정 SPLIT으로 분리·복원(거실→거실+복도, 문/엣지 재분배). 벽 없는 오픈플랜은 보존하되 방-방 경계를 `boundary:"open"`으로 표기(벽 안 그림). 종횡비 기준의 "흡수 24%"는 과대계상이라 폐기.

## 6. 알려진 데이터 이슈 (사실, 보정 대상)
- **전실 → 현관 오라벨**: AI-Hub가 전실을 전부 "현관"으로 라벨(전실 0건). 체계적 오류.
- **연결공간 흡수**: §5 참조(~24% 길쭉 거실/드레스룸).
- **역할미상 16,527** · **문폭없음 27,408** · 침실↔침실 '문' 78.2%(데이터 의심).
- 발코니 확장 옛 벽선이 비전추출 교란. 안방/침실 구분 약함.

## 7. Parsed vs Corrected (비교축의 두 입력)
- **Parsed** = R2G 파서 출력 그대로(사람 손 0). `sample_parsed.json`. — 위 이슈(오라벨·흡수·미상) 그대로.
- **Corrected** = 파서 출력 + 사람 정보보정(역할 교정·인접 선언·노드 합치기/나누기·현관 지정). `sample_corrected.json`. — 보정된 상태.
- 둘을 *같은 엔진*에 넣어 도면 품질 비교 = "사람 보정의 가치" 측정(human-correction ablation).

## 8. 외부 전문가에게 묻는 데이터 관련 질문
1. 이 그래프 표현(방 폴리곤 + 인접 + 문/창, 벽은 코너집합)으로 **완성도 도면 생성**에 충분한가? 부족하면 무엇을 더 표현해야?
2. 축척 0%·기구 0% 갭을 학습으로 풀지(데이터 부족), 규칙/추론으로 풀지?
3. 노이즈(오라벨·흡수·미상)가 큰데 — 사람 보정으로 일부만 정제 vs 깨끗한 부분집합(dual 8,700)만 사용, 어느 전략이 학습에 유리?
4. 한국 정준 위상(§5)을 생성에 **사전지식/제약**으로 넣는 좋은 방법은?
