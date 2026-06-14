# ADR-0012: 생성 타깃·완성 분할·평가·학습순서 (wall-cycle + opening 토큰)

Status: Accepted
Date: 2026-06-14
Deciders: wissens2024, Claude (+ 외부검토 GPT)

## Relates
- **ADR-0006(ONE 엔진) 유지 — supersede 아님.** 바뀌는 건 엔진 개수가 아니라 *생성 타깃*.
- ADR-0010(wall-cycle 표현)을 일부 **refine**: 문·창의 *존재/연결*은 생성, 세부는 완성층.

## Context
네이티브 생성기(geom_g0)가 **bbox 회귀로 붕괴**한다(순열 등변·위치인코딩 부재→중복방 동일박스 겹침, MSE 모드평균→중앙붕괴, [[geom-g0-failure-modes]]). 이 상태로 학습을 40k→800k 늘려도 "더 정확히 붕괴"할 뿐. ADR-0010이 표현을 wall-cycle로 정했고, 외부검토(GPT)가 *생성 타깃·평가지표·학습순서*를 구체화. "GSDiff로 엔진을 갈아탄다"는 혼동이 반복돼(메모리 유실 포함) 이를 정본으로 고정한다.

## Decision
1. **생성 타깃 = corner + wall + room-cycle + role 토큰** (GSDiff류 기법: 정렬손실·벽접합그래프·겹침0). **bbox 회귀 폐기 → 네이티브 생성기 재작성.** ADR-0006 ONE 엔진 유지 — *엔진 swap이 아니라 타깃 교체*. DiffPlanner = 작동 베이스라인/헤지.
2. **문·창 = opening 토큰 최소 생성** (존재·어느 wall에·door/window 타입·대략 위치 ratio). **완성층은 세부**(정확 폭·스윙·창호 치수·CAD 블록·치수선). 근거: 문·창은 장식이 아니라 *연결성·법규를 결정하는 구조요소*라 생성 표현 안에 있어야 함.
3. **g-0.4 = 필드보다 불변조건/validator 강화** (무효 도면을 표현 단계에서 차단):
   - 모든 room은 ≥1 closed wall cycle을 가진다.
   - 모든 door는 정확히 1개 wall segment 위에 있다.
   - door가 잇는 두 room은 그 wall을 공유한다.
   - window는 exterior wall 위에만.
   - 현관에서 모든 public/private room이 reachable.
   - room polygon 면적 ↔ wall-cycle polygon 면적 차 ≤ ε.
4. **평가 1차 지표 = CAD 품질** (FID는 그 다음): DXF open rate · closed wall-loop rate · room overlap rate · wall-cycle consistency · door-on-wall rate · entrance reachability · exterior-window validity · legal verifier pass rate.
5. **데이터 정제 우선순위 = 위상오류 > 축척/기구**: ① 복도/전실/파우더룸 split ② 전실→현관 오라벨 교정 ③ 침실↔침실 문 검토 ④ role unknown ⑤ 축척/기구/치수. (축척·기구는 규칙으로 채우나, 위상오류는 모델이 "잘못된 한국 아파트"를 배움.)
6. **학습 순서**: ① 생성 표현 수정 → ② 500~2,000 **진단 미니셋**에서 collapse 재현/해소 → ③ 장기학습(40k). 미니셋은 성능용이 아니라 *붕괴 진단 실험실*, 난이도 티어로 구성:
   - Tier1 단순형(현관1·거실1·침실1~2·욕실1, 복도 없음/짧음, 직교)
   - Tier2 한국 정준형(현관→복도→거실, 침실/욕실 복도연결, 발코니)
   - Tier3 안방 복합형(안방→파우더룸/전실→드레스룸/전용욕실, split 필요)
   - Tier4 노이즈형(Parsed 원본, 흡수/오라벨, repair 검증)
   - 판독: T1 붕괴=모델/표현 문제 · T1 OK·T3 실패=한국 위상/데이터 · T4 실패=parser noise robustness.
7. **법규 = 처음부터 loss에 넣지 않음. verifier → repair → reranking 순서.**
8. **CE2EPlan/TLC-Plan/FMLM/RLVR = 방향전환 근거 아님. 부품/비교군으로만.**

## Considered Alternatives
1. **GSDiff를 별도 엔진으로 교체(ADR-0006 supersede)** — 기각: 엔진 개수 문제가 아니라 *표현/타깃* 문제. ONE 엔진 유지가 범위폭발(LLM+diffusion+GSDiff+FMLM+RLVR+repair+CAD compiler) 방지(GPT 동의).
2. **문·창 전부 후처리(완성층)** — 기각: 연결성·법규를 결정하는 구조요소라 생성에 *존재*해야. (ADR-0010 원안 일부 수정.)
3. **표현 그대로 장기학습부터** — 기각: 붕괴 원인이 표현이라 "더 정확히 붕괴".
4. **g-0.4를 필드 추가로만** — 기각: 무효 도면 차단은 *불변조건/validator*가 함.
5. **FID 우선 평가** — 기각: 목표는 DXF로 열리는 도면이지 논문 이미지가 아님.

## Consequences
- Positive: bbox 붕괴 근본 회피, open/벽·겹침0이 표현에서 보장, 문·창 연결성/법규를 생성에서 확보, 평가가 목표(도면)와 정렬, 미니셋으로 싸게 진단.
- Negative: 네이티브 생성기 재작성 필요, opening 토큰·정렬손실 구현 부담, validator 작성·CAD 지표 파이프라인 필요.
- Follow-up: ① 네이티브 생성기 wall-cycle+opening 타깃 재작성 ② g-0.4 validator(§3 불변) 구현(ADR-0010 follow-up ⑤와 통합) ③ CAD 지표 모듈 ④ 진단 미니셋 4티어 큐레이션 ⑤ legal verifier→repair→rerank 루프.

## Assumptions
- wall-cycle+opening 토큰 표현이 한국 비직사각/오픈플랜을 담을 수 있다(ADR-0010 전제).
- 미니셋 collapse 진단이 대규모 학습 실패를 예측한다.
- DiffPlanner 베이스라인이 작동 파이프라인·비교군으로 계속 유효.
- 이 전제가 깨지면(예: 토큰 표현이 학습 안 됨) 재검토.
