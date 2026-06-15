# 문헌·부품 카탈로그 (도면 생성 SOTA + 상용 분석)

> 목적: 외부(GPT) 추천 논문을 **우리 설계의 부품/비교군**으로 정리한다.
> **이 문서는 방향 전환 문서가 아니다.** ADR-0012 §8·README §6 안티-플립 규칙대로,
> 새 SOTA는 *부품·비교군*일 뿐 ONE-엔진(ADR-0006)·wall-cycle 타깃(ADR-0012)을 바꾸지 않는다.
> 각 논문에서 **우리가 가져올 알고리즘 한 조각**과 **안 가져올 부분·이유**를 못 박는다.
> 관련: [ADR-0006](adr/0006-sovereign-floorplan-engine.md) · [ADR-0010](adr/0010-representation-g04-completion-layers.md) · [ADR-0012](adr/0012-generation-target-opening-tokens-eval-order.md)

---

## 0. 가장 중요한 답 — "상용과 우리 차이가 생성형 AI인가? 그들은 다른 방식인가?"

**아니다. 차이는 '생성형 AI 유무'가 아니라 '표현(representation)과 풀이 방식'이다.**

상용 도구(Planner5D·RoomSketcher·Cedreo·Maket·Lovart·Autodesk Forma/TestFit 등)의 기하는
대부분 **신경망이 좌표를 직접 뱉어서** 나오지 않는다. 그들은:

1. **파라메트릭 CAD 커널 + 타입 있는 객체 모델**을 가진다.
   - 벽 = 두께를 가진 *파라메트릭 객체*(끝점·접합이 규칙으로 자동 정렬).
   - 문·창 = 벽에 **스냅되는 CAD 블록**(벽 위에만 존재 — 구조적으로 보장).
   - 가구 = 3D 모델 카탈로그(치수·회전 파라미터).
   - 좌표는 *학습·샘플링*하는 게 아니라 **스냅 그리드 위에서 제약 해결(constraint solving)로 구성**된다.
2. **자동 생성을 하는 경우(Maket·Forma·TestFit·Finch)도 디퓨전이 아니라**
   조합 최적화 / 절차적 규칙 / 파라메트릭 솔버다. 인접·면적·치수가 **하드 제약**으로 들어간다.
3. **AI/LLM을 쓰는 곳은 기하가 아니라 프런트엔드**다 — 요구사항 해석(자연어→스펙),
   스타일 추천, 가구 채우기. **최종 좌표를 LLM이 직접 만들지 않는다.**

> **결론: 우리의 세 난제(겹침·정렬·축척)는 그들에겐 *발생하지 않는다*.**
> 제약 솔버가 기하를 *구성*하므로 겹침이 구조적으로 불가능하고, 모든 게 처음부터 실mm·스냅이다.
> 우리는 신경망에게 "겹치지 마라·정렬해라·축척 맞춰라"를 **데이터로 배우게** 강요하는 중인데,
> 그건 그들이 솔버로 *공짜로* 보장하는 것이다.

**그래서 격차를 좁히는 길은 '더 큰 디퓨전'이 아니다.** 길은 두 가지를 합치는 것:
- (A) **유효성이 표현에 내장된 구조적 표현** — 무효 상태를 표현 불가능하게 만든다
  (wall-cycle·FML식 DSL·patch division). = ADR-0010/0012가 이미 정한 방향.
- (B) **검증→교정(verify→repair) / 검증가능보상(RLVR) 루프** — 솔버의 제약검사를
  생성 뒤에 강제. = ADR-0012 §7(verifier→repair→reranking)이 이미 정한 방향.

논문들은 **새 방향이 아니라 이 결정의 확증·정교화**다. 특히 FML(=신경망 CAD-IR)과
RLVR(=제약검사를 보상으로)이 "상용이 솔버로 하던 일"을 신경망에 이식한 형태다.

### 따름정리 — "2단계(DiffPlanner급)와 3단계(상용급)가 한 번에 일어나야 한다"의 해소
2·3단계가 *동시에 일어나야 하는 것처럼 느껴지는 이유*는, 둘이 **다른 표현 영역**이기 때문이다:
- 2단계 = "좋은 벡터 레이아웃"(방 폴리곤·벽).
- 3단계 = "진짜 CAD"(문·창·가구·치수·실mm·레이어).

raw 박스로 2단계를 풀면 3단계로 못 넘어간다(박스엔 벽·접합·문이 없음).
**구조적 CAD 표현(FML/wall-cycle)으로 생성하면 2와 3이 *같은 산출물*이 된다** — 분리된 단계가 아니라.
이게 FML의 핵심 주장이고, 우리 wall-cycle+opening 타깃(ADR-0012)이 가는 방향이다.

---

## 1. 단일-모델 계열 (생성 타깃·표현 관련)

### FML / FMLM — Unified Vector Floorplan Generation via Markup Representation (CVPR 2026)
- **알고리즘**: 도면을 **Floorplan Markup Language(FML)** 라는 마크업 문장으로 표현 →
  생성 = **next-token prediction**. 방·경계·그래프·조건을 하나의 문법화된 시퀀스로 직렬화,
  Transformer가 토큰 생성. **constrained decoding**으로 무효 토큰 차단.
- **★ 우리가 가져올 것**: ① **DSL/마크업을 1급 표현으로** — 우리 g-0.4를 "벽 그래프 JSON"이 아니라
  *생성 가능한 문법(corner/wall/room/door/window 태그 시퀀스)* 으로도 직렬화. ② **constrained decoding**
  = ADR-0012 §3 불변조건을 *생성 시점에 강제*(벽 안 닫힘·문 벽이탈을 디코딩에서 마스킹).
- **안 가져올 것**: 모델 통째 교체(ONE-엔진 유지). FML 어휘를 그대로 쓰지 말고 **g-0.4 스키마를 토큰화**.
- **⚠️ 귀속 주의 — "토큰화"는 FMLM 고유기술이 아니다.** "구조화 객체를 DSL/마크업 토큰 시퀀스로
  직렬화 + autoregressive 생성 + constrained decoding"은 **분야 표준 패러다임**이다:
  PolyGen(2020, 메시)·DeepSVG(2020)·SketchGraphs(2020)·DeepCAD(2021, CAD 명령 시퀀스)·Vitruvion·LayoutTransformer 계열.
  **FMLM 고유 기여 = ① FML 어휘(평면도 마크업) ② unified conditioning(한 모델·한 문법이 여러 task 능가).**
  → **우리 g-0.4 토큰화는 우리 소유**(우리 스키마·어휘·불변조건 마스크). 인용은 **원조 패러다임(PolyGen/DeepCAD) + FMLM(평면도 적용)** 둘 다. FMLM만 걸면 우리 novelty가 과소평가됨.
- **추천도: 매우 높음.** 2·3단계 통합(따름정리)의 직접 근거. 표현을 "벽 그래프 하나"로 굳히지 말고
  **g-0.4 DSL**로 잡으면 wall·room·door·window·dimension·fixture·layer를 한 문법에 확장 가능.
- ⚠️ arxiv ID(2604.04859 등)·공개코드 라이선스 **미검증** — 인용 전 확인.

### CE2EPlan — Controllable End-to-End Vector Floor Plan Generation (TVCG 2026)
- **알고리즘**: 경계(boundary) 입력 → 완성 레이아웃까지 **end-to-end diffusion**(중간표현·멀티스텝 파이프라인 제거 주장).
  공식 GitHub + RPLAN 학습/샘플 스크립트 + pretrained.
- **★ 우리가 가져올 것**: 경계조건 직접 생성(우리 "한국 아파트 틀" 조건과 정합) · end-to-end 단순화 아이디어.
- **안 가져올 것 / 정직**: README상 샘플 후 **`post_processing.py`로 정렬** 필요 = "end-to-end"라도
  **CAD 품질엔 후처리·정렬이 여전히 필요**. "완전 자동 DWG"로 착각 금지(= ADR-0012 verify→repair가 옳다는 증거).
- **추천도: 높음 (비교 베이스라인·경계조건 부품).** DiffPlanner 옆 두 번째 작동 베이스라인 후보.

### TLC-Plan — Two-Level Codebook Network for End-to-End Vector Floorplan (Eurographics 2026)
- **알고리즘**: **계층 VQ-VAE + autoregressive Transformer.** top-level code=전역 방 배치,
  bottom-level code=정밀 방 polygon. rasterization artifact 없는 벡터 직접.
- **★ 우리가 가져올 것**: **전역 레이아웃 / 지역 기하 분리**(2계층) — 우리 모드붕괴·겹침
  ([[geom-g0-failure-modes]])에 직접 처방. "한 블랙박스가 모든 좌표"보다 안정적. ADR-0012 단계분해와 정합.
- **안 가져올 것**: 자연어/요구조건 제어는 약함(FML/LLM 계열로 보완).
- **추천도: 높음 (안정성 부품).** 경계→벡터 품질·겹침 완화에 좋음.

### GSDiff — Geometry-enhanced Structural Graph Generation (AAAI 2025) — *보유*
- **알고리즘**: wall **junction 생성 + wall segment 예측**으로 structural graph.
  **alignment loss + random self-supervision**으로 정렬·겹침·gap 해결. 코드 보유(`~/gsdiff_work`).
- **★ 우리가 가져올 것**(ADR-0006/0012 이미 채택): **정렬 손실 + 랜덤 자기지도** =
  코너/벽 연결포인트 정합. 상용 솔버의 "스냅"을 학습으로 근사하는 핵심 부품.
- **안 가져올 것**: 전체 표현을 GSDiff wall-graph에만 묶기 — 자연어·면적조건·레이어 확장이 막힘.
- **추천도: 중상 (정렬 부품, 이미 채택).**

### WallPlan — Learning to Generate Wall Graphs (TOG 2022)
- **알고리즘**: GraphNet+LabelNet으로 wall graph + room label 동시 생성.
- **정직한 한계**: 논문이 **창·실내문은 heuristic으로 생성**한다고 명시 → "완성 CAD"엔 별도 규칙엔진 필요.
- **추천도: 참고용** (벽그래프 표현 설계 역사). 주엔진 후보는 GSDiff/CE2EPlan/FML이 우위.

### FloorplanSBS — Patch-Based Floorplan Segmentation (ACM MM 2025)
- **알고리즘**: 설계공간을 **rectangular patch로 분할 + patch마다 semantic label** → 벡터 직접.
  복잡한 post-processing/optimization 회피 주장.
- **★ 우리가 가져올 것**: **patch division으로 겹침 원천 차단** 아이디어
  (모든 공간이 패치라 room overlap이 구조적으로 불가 — 상용 CAD 그리드와 같은 원리).
  겹침이 큰 우리 파이프라인에 직접 처방.
- **안 가져올 것**: 창·실내문은 여기서도 rule-based 추가 → core generator로만. 비정형 벽·복잡 평면 확장엔 추가설계.
- **추천도: 중상 (겹침 차단 아이디어).**

---

## 2. 제약·검증 계열 (우리 novelty=법규-인식 생성의 직접 부품)

### LLM + RLVR — Generative Floor Plan with Verifiable Rewards (ACL Findings 2026)
- **알고리즘**: LLM fine-tune로 도면을 **구조화 텍스트(JSON)** 생성 →
  **RLVR(검증 가능한 보상)** 으로 방 면적·치수·연결성·**polygon overlap** 개선.
  출력 = room polygons·areas·IDs·doors·coordinates의 structured JSON(= 우리 CAD-IR에 매우 근접).
- **★ 우리가 가져올 것**: ① **검증가능보상 = 상용 솔버의 제약검사를 RL 보상으로** —
  우리 `rules_legal`(채광 1/10·환기 1/20·최소면적)과 겹침·연결성을 **보상함수**로.
  ADR-0012 §7(verifier→repair→**reranking**)의 reranking을 RLVR로 강화 가능. ② **structured JSON 출력**이
  우리 g-0.4와 정합 → DXF 컴파일 용이.
- **안 가져올 것 / 위치**: **주엔진 아님, 제약 제어·검증·리랭킹 엔진**으로.
  LLM이 곧장 *아름다운 기하*를 만들진 못함 — 요구조건 구조화·검증에 강함.
- **추천도: 매우 높음 (법규-인식 novelty의 실현 부품).**

### HouseLLM — LLM-Assisted Two-Phase Text-to-Floorplan (2024)
- **알고리즘**: LLM이 초기 layout → conditional diffusion이 refine(2단계).
  direct text→floorplan은 fine-grained 기하·수치제약 만족이 어렵다고 지적.
- **★ 우리가 가져올 것**: **인터페이스 설계** — "자연어 요구 → 설계의도 구조화"는 LLM,
  최종 CAD는 DSL/diffusion. 우리 입력단(방 구성·관계 받기)에 참조.
- **추천도: 인터페이스 참고용.**

### HouseDiffusion (CVPR 2023) — *이미 채택*
- **부품**: 이산+연속 디노이즈(직각·평행·코너공유 기하관계). ADR-0006 채택 유지.

### DiffPlanner (2025, FID 1.23 재현) — *보유·작동 베이스라인*
- **부품**: 벡터직접 diffusion 골격 + 경계조건 + 3단계(node→adjacency→partition). 헤지 베이스라인.
- **확인된 한계**(2026-06-15 run): 임베딩 테이블 **8방/6범주 고정** → 한국 18방/13역할 전이 차원불일치로 막힘.
  → 네이티브 엔진(고정차원 비종속)의 근거.

---

## 3. 데이터셋 — "RPLAN만으로는 논문 데모, CAD 품질 부족"

| 데이터셋 | 규모 | 내용 | 우리 쓰임 / 주의 |
|---|---|---|---|
| **ResPlan** | 17,000 realistic residential | 벡터/그래프, walls·doors·windows·balconies·room functions annotation. clean vector+graph 지향(RPLAN/LIFULL/CubiCasa 한계 극복 주장) | **★ 강력 후보.** 한국 AI-Hub 보강·사전학습용 글로벌 코퍼스로 검토. 발코니·창·문 annotation이 우리 완성층과 정합. 라이선스·한국과 위상격차 확인 필요 |
| **FloorPlanCAD** | 15,000+ CAD drawings | SVG+PNG, **30 카테고리 line-grained primitive 라벨**(doors·windows·furniture·appliances·wall) | CAD **심볼/레이어 학습·도면 이해**엔 좋음. **주의: 20m×20m crop block** → "완성 주거단위 생성"용으로 그대로 쓰면 안 됨. 심볼·레이어 부품 학습으로 한정 |

- **현 자산**: RPLAN(글로벌)·AI-Hub 한국(문98%·창98%·기구48%)·CubiCasa(기구). ResPlan을 **글로벌 사전학습 보강**,
  FloorPlanCAD를 **CAD 심볼/레이어 보조**로 더하는 것을 *측정 후* 결정(콤보 매트릭스에 추가, ADR-0006 §5).

---

## 4. 종합 — 부품 배치도 (우리 ONE-엔진 위에)

| 우리 단계 (ADR-0006/0012) | 차용 부품 | 출처 |
|---|---|---|
| **표현 = g-0.4 DSL(토큰화)** | 마크업 next-token + constrained decoding | **FML/FMLM** |
| **전역배치 / 지역기하 분리** | 2-level codebook | **TLC-Plan** |
| **겹침 원천 차단** | patch division / wall-cycle 불변 | **FloorplanSBS** / 우리 g-0.4 |
| **코너·벽 정렬(스냅 근사)** | alignment loss + 랜덤 자기지도 | **GSDiff** (채택) |
| **기하관계 디노이즈** | 이산+연속 | **HouseDiffusion** (채택) |
| **경계조건·end-to-end 골격** | boundary→layout diffusion | **DiffPlanner**(보유)·**CE2EPlan** |
| **법규-인식 생성(novelty)** | RLVR 검증가능보상 = `rules_legal` 보상화 | **RLVR** |
| **요구 해석 입력단** | LLM 2단계 인터페이스 | **HouseLLM** |
| **완성층 문·창·치수·레이어** | (우리 neuro-symbolic + CAD 심볼) | 우리 + **FloorPlanCAD** 보조 |
| **글로벌 사전학습 보강** | realistic vector+graph 코퍼스 | **ResPlan** (측정 후) |

**바뀌지 않는 것(ADR 정본)**: ONE-엔진 유지 · wall-cycle+opening 생성 타깃 · 평가는 CAD품질>FID ·
법규는 loss 아닌 verifier→repair→rerank · 위상오류 정제 우선. **위 부품들은 이 골격을 *채울 뿐* 바꾸지 않는다.**

## 5. 후속 작업 (구현 시 ADR로 승격)
- [ ] g-0.4 → **토큰 직렬화 스펙**(FML식) 1장: corner/wall/room-cycle/role/opening 토큰 문법 + constrained-decoding 마스크 규칙(= ADR-0012 §3 불변조건).
- [ ] **2-level**(전역배치→지역폴리곤) 분리로 네이티브 생성기 v1 시도(모드붕괴 진단 미니셋에서, ADR-0012 §6).
- [ ] **RLVR 리랭킹** PoC: `rules_legal`+겹침+연결성 보상으로 샘플 reranking(법규-인식 novelty 1차 실증).
- [ ] **ResPlan/FloorPlanCAD** 라이선스·스키마 검토 → 사전학습/심볼 보강 콤보에 추가할지 측정.
