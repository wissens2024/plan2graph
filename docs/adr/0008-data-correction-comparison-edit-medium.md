# ADR-0008: T·G 재정의 — 기하 A/B 폐기, "입력 품질" 비교축, JSON 그래프 직접 편집, 웹 주석 툴

Status: Accepted
Date: 2026-06-14
Deciders: wissens2024

## Supersedes ADR-0007
ADR-0007의 ①"T·G=약한기하 vs 강한기하 A/B" ②"알바 보정=SVG 단계"를 뒤집는다.
유지: ADR-0006 소버린 엔진, ADR-0007 "돌림 방지" 원칙 — 오히려 강화(엔진 코어는 안 건드리고 비교축·편집매체만 정정).

## Context
- (사실) raw→그래프 변환 오류 = AI-Hub raster+노이즈 라벨에서 구조를 캐내는 비용. DiffPlanner(arXiv:2508.13738 "Eliminating Rasterization")/GSDiff(arXiv:2408.16258)는 RPLAN(이미 벡터그래프)으로 이를 회피, 우린 회피 못 함.
- (사실) AI-Hub 품질 낮음: 한쪽 라벨 결손/객체만 78%(dual 21%뿐), 퍼널 41,556→고유 15,108(대량 중복), 역할미상 16,527, 전실→현관 체계적 오라벨.
- (사실) 현 파이프라인은 raw→SVG→그래프 왕복. SVG 합성·재파싱이 별도 오류원. auto 경로엔 state가 SVG 이전에 이미 존재 → SVG는 사람편집 호스팅용 군더더기.
- (판단) ADR-0007의 "약한 기하(박스) baseline"은 '몰라서 낮춘 스톱갭'이라 비교 변수로 무의미. "위상만으론 도면 불가"는 이미 증명됨(반복 불요).
- (제약) 알바가 대량 보정. 유일 기준 = 알바 작업 편의(=조작 지연).

## Decision
1. **기하 엔진 단일화** — T·G 모두 ADR-0006 학습 기하 엔진 하나. "약한 기하(박스/treemap)" 비교 라인 폐기.
2. **비교축 = 입력 품질(엔진 고정)** — T=AI-Hub 자동(보정X), G=AI-Hub 자동+사람 정보보정. 측정 G−T = 보정의 가치.
3. **편집 매체 = 그래프 JSON 직접**(=최종 산출 스키마). SVG 중간단계·왕복 폐기. 보정=도면 위 '정보 보정'(역할·인접·문방향·현관·멀티유닛 분리), 기하 드로잉 아님. 원본 PNG=불변 배경 기준([[inspect-original-first]] — 해석 단독표시 금지).
4. **편집 도구 = 클라이언트 사이드 웹 주석 툴**(Streamlit 아님). 편집 즉시 반영, 도면당 1회 저장. 변환 사유(convert_plan why) 인라인 표시 → 고침→재변환 닫힌 루프.
5. **AI-Hub 전략** = dual(8,700) 클린 부분집합 베이스라인. 보정은 가치 높은 일부만(objocr 등 손대지 않음). raster→vector는 라벨 결손분에만 V2V.
6. **폴더 분리** — 원본(자동변환) 그래프와 작업(사람보정) 그래프를 물리적으로 분리: `data/staging/gline/graphs/`(원본·읽기전용) ↔ `data/staging/gline/corrected/`(작업). 둘 다 graphs/ '밖'이라 회계 캐시 무효화 회피(ADR-0003 단일폴더+flag 모델을 폴더분리로 정정).
7. (후속) 제3안=직접 수집 클린 소스(벡터/법적안전 우선) — 별도 ADR, 지금 보류.

## Considered Alternatives
1. **ADR-0007 유지(약한 vs 강한 기하 A/B)** — 기각: 입력+기하 동시 변경 = 혼동된 비교. "약한 기하"는 의미 없는 스톱갭.
2. **SVG를 편집 매체로 유지** — 기각: raw→SVG→그래프 왕복 손실·이중진실(drift). CubiCasa처럼 SVG가 '원천'이면 OK나 우린 '파생 중간물'이라 군더더기.
3. **Streamlit으로 에디터 통합** — 기각: 위젯마다 서버 전체 rerun=클릭당 지연. 대량 주석에 알바 생산성 치명. (클라 사이드 웹은 즉시반영+1회저장.)
4. **제3안 즉시 착수** — 보류: 래스터면 추출문제 잔존+법적검토 필요. 후속 ADR.

## Consequences
- Positive: 깨끗한 비교(보정 가치 격리) · 변환 오류원(SVG 왕복) 제거 · 단일 진실(JSON=산출=편집) · 알바 즉시반영 툴 · 엔진 단일화로 돌림 방지 강화.
- Negative: 별도 웹 서버 유지. edit_server를 의미주석으로 개편 비용. AI-Hub 천장 낮음(보정 ROI 한계→제3안 후속).
- Follow-up: ① auto 경로 SVG 왕복 제거(dr→GG.build 직결) 영향범위 조사 ② edit_server를 PNG+JSON 의미주석 툴로 개편 + commit(divergence 해소) ③ 원본/작업 폴더 분리 ④ T∥G(입력품질) 비교 측정 ⑤ 제3안 ADR.

## Assumptions
- ADR-0006 학습 기하 엔진이 우리 통일 그래프를 입력으로 동작한다(미구현이면 G축 지연).
- AI-Hub 폴리곤 기하는 대체로 멀쩡, 망가진 건 의미(라벨·인접) — 그래서 의미주석으로 충분.
- 알바는 PNG 대조로 의미 충실도 판정 가능(자동 오라클 없음).
- 잘 만든 클라 사이드 웹 툴은 Streamlit보다 빠르다(편집 무왕복).

## Related
- Supersedes: [ADR-0007](0007-geometry-model-ab-and-correction-plan.md)
- 정밀화: [ADR-0002](0002-tline-gline-separation.md), [ADR-0003](0003-gline-single-source.md)(단일폴더→폴더분리)
- 엔진: [ADR-0006](0006-sovereign-floorplan-engine.md)
