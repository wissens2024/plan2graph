# 한국형 데이터 표현 (외부 검토용 — ChatGPT 등)

> plan2graph가 한국 AI-Hub 아파트 평면도를 변환한 **통일 그래프(geomgraph g-0.3)**.
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

## 3. 스키마 (geomgraph g-0.3)
최상위 키: `schema_version, plan_id, house, scale_mm_per_px, bbox_px, n_rooms, n_edges, n_walls, n_doors, n_windows, rooms, edges, walls, doors, windows, validation, meta, unit_id, corrected, provenance`

- **rooms** (dict, key=str(int)): `role`·`base`(역할/원라벨)·`is_connector`(복도·전실)·`centroid`·`bbox_px[x,y,w,h]`·`area_px`·`area_m2`(null=축척미정)·`aspect_ratio`·`touches_exterior`·`has_window`·`n_windows`·`fixtures`(현재 [])·`privacy`(public/private/service)·`wall_ids`·`door_ids`·`window_ids`·`polygon`([[x,y],...])·`dist_from_entrance`
- **edges** (list): `{from:int, to:int, via:"door"|"open", door_id, privacy_transition, dist_from_entrance}` — 방-방 인접(무방향). via=door(문) / open(개방연결).
- **doors** (list): `{id, connects:[a,b], via, position[x,y], polygon, width_px(null많음), subtype, orientation(null많음), needs_orientation_review}`
- **windows** (list): `{id, belongs_to:int, position, bbox_px, width_px, on_wall}`
- **walls** (list): 코너 간 선분(벽).
- **validation**: `{passed, reasons[], warnings[], info[]}` (geomgraph 무결성 R1~R8).
- **meta**: `{house_type(APT/DEH/ROW), scale_mm_per_px, status(success/quarantine), reason, n_*}`

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
이 **연결공간 2종(복도, 파우더룸/전실)이 R2G 파서에서 옆 큰 방에 흡수**되는 게 핵심 결함 → 길쭉한 거실/드레스룸(전체 ~24%). 위상 허브가 사라짐. (사람 보정 SPLIT으로 분리·복원 = 거실→거실+복도, 드레스룸→드레스룸+파우더룸. 문/엣지 기하 재분배.)

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
