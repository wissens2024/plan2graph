# ADR-0010: 표현 g-0.4 — 벽사이클 방·경계 태그·완성 7레이어·가구 2계층

Status: Accepted (일부 refine: ADR-0012 — 문·창은 opening 토큰으로 생성, g-0.4는 불변조건 강화)
Date: 2026-06-14
Deciders: wissens2024, Claude

## Supersedes / Relates
- ADR-0006(소버린 엔진)의 **표현 결정을 구체화**(supersede 아님). ADR-0006은 "코너+벽+방폴리곤+방종류 + 완성층"을 정했고, 본 ADR은 그 *스키마(g-0.4)와 불변규칙*을 확정한다.
- ADR-0008/0009(Parsed/Corrected) 위에서, Corrected가 *라벨 canonical화*의 자리임을 명시.

## Context
목표는 박스+색깔이 아니라 **그려서 쓰는 완성 건축 도면**(벽·방·문·창·가구·치수) → **AutoCAD DXF** 연결이다(=완성도). 현 g-0.3은 갭이 크다: 가구 0%·축척 0%·문스윙 57%·벽두께 없음·방-방 경계 종류 없음. 또한 "오픈 거실+복도"를 어떻게 표현하나로 반복 흔들렸다.

확정 사실(2026-06 실측):
- AI-Hub OBJ 가구 어휘 = **정확히 5종**(변기·세면대·싱크대·가스레인지·욕조), 9,113파일에 90~99.7% 커버. attributes에 `rotation`(가구 방향) 보유. **소파·TV·침대는 0건.**
- 한국 거실은 비직사각·오픈플랜이 정상(RPLAN의 직사각 가정과 다름).
- 데이터에 "이미 자른 거실+복도"와 "흡수된(안 자른)" 형태가 혼재 → 같은 실물의 *비일관 인코딩* = 학습 ambiguity.

## Decision
1. **방 = 벽사이클 노드. 복도·전실도 항상 별도 노드로 분리**(RPLAN과 같은 위상 문법 → 일관성·전이). 흡수된 연결공간은 분리(SPLIT) 대상.
2. **방-방 경계에 `boundary: wall | open | door` 태그.** 오픈플랜=`open`(벽 **안** 그림 → 한국 모양), 벽 있음=`wall`, 문=`door`. **불변: 벽 없는 곳에 벽을 그리지 않는다.**
3. **SPLIT은 *벽이 있는데 흡수된* 경우에만.** 비직사각/오픈 거실은 정상으로 보존(종횡비 기준 폐기). 오픈 케이스의 분할선은 안 그려지므로 시각 영향 0 + 좁은 팔+사적방 문으로 자동 도출.
4. **완성 7레이어를 스키마로 보장**: ①벽(두께) ②방(폴리곤+역할) ③문(스윙) ④창 ⑤가구 ⑥치수(mm) ⑦DXF 레이어. (전체 필드 명세 = `docs/for_review/korean_dataset.md` g-0.4 절.)
5. **가구 = 2계층.** Tier A `obj_detected`(5종, OBJ + `rotation`→orientation) / Tier B `role_inferred`(역할 카탈로그+배치제약, 가독성용). `source`·`confidence`로 구분.
   - Tier A(검출): 변기·세면대·싱크대·가스레인지·욕조.
   - **Tier B(추론, 확정 카탈로그)**: 거실→소파·TV / 침실·안방→침대·옷장 / 주방→냉장고·식탁(+검출 싱크대·가스레인지) / 현관→신발장 / 드레스룸→붙박이장.
6. **축척**은 표준 문폭/치수텍스트 앵커로 해소(`scale{mm_per_px,source,confidence}`). 치수(⑥)는 scale+기하에서 *도출*(사람 재입력 금지).
7. **라벨 다양성(전실/현관, 거실/복도)은 Corrected에서 canonical화** + 온톨로지(`ontology/floorplan.owl`)로 계층 흡수.

g-0.4 신규/변경 키(요약): top `scale`,`units`,`bbox_mm`; rooms `connector_origin`,`fixture_ids`,`area_m2`; edges `boundary`,`shared_segment`; walls `type`,`thickness_mm`,`sides`; doors `width_mm`,`type`,`swing{hinge_room,direction,angle}`; windows `width_mm`,`sill_height_mm`,`type`; **신규** `fixtures[]`,`dimensions[]`; DXF 레이어 `A-WALL/DOOR/GLAZ/FURN/DIM/AREA/ANNO`.

## Considered Alternatives
1. **오픈 거실+복도를 한 폴리곤으로 두고 안 자름** — 복도는 위상 속성으로만. **기각**: (a) 거실 모양이 RPLAN과 너무 달라 전이 약화, (b) *이미 자른* 데이터와 충돌(비일관 잔존).
2. **항상 벽으로 분리(RPLAN식)** — 오픈에도 벽을 그어 직사각 거실 강제. **기각**: 없는 벽을 발명 → "어디 자르나" 주관성 부활 + 한국 오픈플랜 모양 상실(목적 위배).
3. **방=축정렬 박스** — **기각**(ADR-0006에서 이미 폐기, 정답조차 99% 겹침).
4. **가구=검출 5종만 사용** — **기각**: 소파·침대 없어 가독성 미달(사용자 목표 미충족). 역할추론 Tier B 필요.
5. **치수를 사람이 입력** — **기각**: 탐지·기하로 도출 가능(중복입력 모순).

## Consequences
- Positive: 같은 실물의 인코딩 통일(ambiguity 제거) + 한국 비직사각/오픈 보존. 완성 7레이어로 DXF까지 일관. 가구·orientation을 데이터에서 직접 획득(Tier A).
- Negative: SPLIT 보정·canonical 라벨 표준 정의가 필요(사람 작업). Tier B 가구는 추론이라 부정확 가능(가독성용, 치수 신뢰선 분리).
- Follow-up: ① `korean_dataset.md` 스키마 절 g-0.3→g-0.4 갱신 ② geomgraph 빌더에 `boundary`/`walls.thickness`/`fixtures`(OBJ rotation 매핑) 채움 ③ 역할→가구 카탈로그 작성 ④ 축척 앵커 해소기 ⑤ 검증기 g-0.4 불변(벽 없는 경계=open, 복도 노드 존재) 추가.

## Assumptions
- OBJ `rotation`이 가구 실제 방향과 일치(대부분 0°지만 회전값 신뢰 가정).
- 표준 문폭(현관 ~900–1000mm)으로 축척 근사가 도면용으로 충분(정밀 치수는 앵커 신뢰도에 의존).
- 역할→가구 카탈로그 추론이 가독성 목적엔 충분(설계 정밀도는 비목표).
- 이 전제가 깨지면(예: rotation 부정확, 축척 앵커 실패) 본 ADR 재검토.
