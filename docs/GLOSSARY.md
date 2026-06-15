# 기술 용어집 (논문용 · Glossary)

> **목적:** Phase 1 논문 작성을 위한 기술 용어·정의·구현·분류 단일 정본. 각 용어는
> 정확한 영문 학술명 + 정의 + 우리 구현(코드/ADR) + **분류(우리 기여 / 차용 기법 / 표준)** 으로 적는다.
> **원칙(정직):** 과장도 누락도 금지. 추출·백본 같은 표준 요소를 novelty로 주장하지 않는다
> (사실↔의견 분리, ADR-0009). 평가 수치는 학습 완료 후 확정 — 현재값은 `[잠정]` 표기.
>
> 분류 기호: ✅ **우리 기여(novelty 후보)** · ⚪ **차용 기법(출처 인용)** · ○ **표준/established**
> 최신화 2026-06-15.

---

## 1. 표현 (Representation)

- **g-0.4 geometry-rich graph schema** (기하 풍부 그래프 스키마) — 도면을 코너·벽·방 폴리곤·문·창·가구·치수로 담는 자체(과제) 통합 그래프 형식. COCO 등 공공 검출 표준이 못 담는 도면 *구조·완성도*를 담고, 다국가 데이터를 한 형식으로 통합하는 그릇. 스키마 4번째 리비전(0.1→0.4, 가산적·하위호환). 구현 `geomgraph.py`(ADR-0010). 분류: ✅
- **Wall-cycle representation** (벽-사이클 표현, room-as-closed-wall-cycle) — 방을 닫힌 벽 사이클(corner+wall)로 표현. 벽이 1급, 인접 방이 코너를 공유 → **겹침이 구조적으로 불가능**. 박스(bbox) 회귀 폐기의 대안. 구현 `wallcycle_codec.py`(ADR-0012/0015). 분류: ✅(우리 표현 설계, GSDiff식 벽-그래프 차용 위)
- **Opening tokens** (개구부 토큰) — 문(door)·창(window)·열린경계(open)를 1급 토큰으로 명시. door=벽 위 corner-pair, window=외벽, open=방쌍 인접(벽 안 그림, ADR-0010 `boundary=open`). 연결성·법규를 결정하는 구조요소라 생성 표현에 포함. 분류: ✅
- **Grid quantization** (격자 양자화) — bbox 정규화 후 N격자(128)로 좌표 이산화. 벽두께로 어긋난 끝점이 같은 셀로 모여 전역 junction 자동 통합 + 토큰 어휘 유한화. 분류: ✅(적용), ○(기법 자체는 표준)
- **Polygon regularization/simplification** (폴리곤 정제) — 양자화 전 Douglas-Peucker 단순화로 벽두께 jagged 노이즈 제거(토큰 max 4649→1726). 구현 `wallcycle_codec._simplify_poly`. 분류: ○
- **Token serialization codec** (토큰 직렬화 코덱) — g-0.4 그래프 ↔ 정수 토큰 시퀀스 양방향 변환(encode/decode). 무손실 라운드트립이 전제. vocab 260. 구현 `wallcycle_codec.py`(ADR-0015). 분류: ✅

## 2. 생성 (Generation)

- **Autoregressive token generation** (자기회귀 토큰 생성) — decoder-only Transformer가 토큰을 왼쪽→오른쪽 하나씩 생성. 이전 토큰을 조건으로 다음을 정해 겹침·모드붕괴 회피. 구현 `generators/wall_cycle.py`(WallCycleLM). 분류: ○(백본 표준)
- **Next-token prediction** (다음 토큰 예측) — 학습 목표. 각 위치에서 실제 다음 토큰 확률을 높이는 cross-entropy. 분류: ○
- **Constrained decoding** (제약 디코딩) — 생성 매 스텝에서 문법·구조 불변조건(섹션 순서·corner/room 참조 유효성·cycle≥3·문 on-wall·창 exterior)을 만족하는 토큰만 허용(-inf 마스킹). 무효 *도면 구조*를 생성 시점에 차단. 구현 `wall_cycle.make_constraint_mask`(ADR-0012 §3). 분류: ⚪(FMLM 차용 기법) + ✅(우리 g-0.4 불변규칙)
- **Mode collapse** (모드 붕괴) — 생성기가 다양성을 잃고 같은(또는 퇴화한) 출력을 반복하는 실패. 박스 회귀의 중앙붕괴·중복방 동일박스 겹침([[geom-g0-failure-modes]]). 측정=`uniq_rate`. 우리 wall-cycle은 붕괴 없음(uniq 0.89~1.0). 분류: ○(개념)

## 3. 추출 (Extraction)

- **Raster-to-Graph (R2G) parsing** (래스터→그래프 파싱) — 도면 이미지(+부분 라벨)에서 구조 그래프를 추출. **established task, novelty 아님**(Raster-to-Vector[Liu], Raster-to-Graph[Hu 2024 CGF], CubiCasa5K). 구현 `topology.py`·`geomgraph.py`(ADR-0009). 분류: ○
- **Vision-to-Vector (V2V) detection** (비전→벡터 검출) — YOLOv8-seg로 방(SPA)·구조(STR) 폴리곤 검출. 게이트 통과율로 최적화(mAP 아님), union-not-replace. 구현 `v2v_infer.py`. 분류: ○(표준 검출), ✅(게이트/union 운용)
- **Neuro-symbolic** (뉴로-심볼릭) — 신경(neural: 검출·생성) + 기호(symbolic: 룰·법규·검증·추론)의 결합. 본 시스템 전반의 패러다임. 분류: ○(패러다임)

## 4. 룰·법규 (Rules & Code-Compliance) + 소버린

- **Integrity/convention rule engine** (무결성·관행 룰엔진) — 그래프가 *유효한 도면 구조*인지 검사(고립·문없는방·현관 도달성·필수공간·역할). 그래프 **변환 시점**에 참조. 구현 `rules.py`(R1~R5)·`geomgraph.validate`·`enhance_roles_g`·`KR_CONVENTIONS`. 분류: ✅(한국 관행 룰)
- **Code-compliance (legal) engine** (법규 엔진) — 한국 건축법 정량 준수 검사(채광 §17① 창면적≥바닥 1/10·환기 §17② 1/20·피난·최소면적). **생성 후 verify→repair** 시점에 참조. 구현 `rules_legal.py`·`law_api.py`(국가법령정보센터 175조문). 분류: ✅
- **SWRL + HermiT reasoning** — 파이썬이 수치·위상을 grounding → boolean 단언 → 산술 없는 논리(SWRL)+추론엔진(HermiT)이 위반 클래스 추론. 구현 `rules_swrl.py`·`ontology/floorplan.owl`. 분류: ⚪(도구 표준) + ✅(한국 도면 온톨로지)
- **Sovereign generation** (소버린 생성) — 한국 주권이 **3층**(데이터+룰+법규)에 박힌 생성. ① 데이터(한국 AI-Hub) ② 룰(한국 건축 관행·고유 공간: 전실·드레스룸·알파룸·실외기실) ③ 법규(한국 건축법). RPLAN/CubiCasa는 데이터만, 룰·법규 없음 → 결정적 차별. 분류: ✅(핵심 포지셔닝, ADR-0006/0014)
- **Self-correction (verify→repair→rerank)** (자기교정) — 생성 후 검증→교정→재순위 루프. 무결성·법규·관행 검사 후 스냅·수정, 여러 샘플 중 최선 선택. 구현 `gen_loop.py`·`cadrender.autocorrect`(R1~R8)(ADR-0012 §7). 분류: ✅(법규-인식 부품) ⚠️ wall-cycle 생성기엔 배선 follow-up

## 5. 데이터·조건 (Data & Conditioning)

- **Parsed / Corrected** (파서출력 / 인간보정) — 같은 R2G 파서를 공유하되, Corrected는 사람 검수·보정(HITL)을 더한 조건. 옛 "T/G"·"auto" 폐기(ADR-0009). 물리 분리: `graphs/`(Parsed, 읽기전용) + `edits/`(Corrected 오버레이). 분류: ✅(비교 설계)
- **Human-correction ablation (HITL)** (인간보정 절제비교) — automation vs automation+human. 비교 두 축 = ① **성능**(Parsed→Corrected 같은 데이터=보정 품질) ② **데이터 증분**(보정이 살린 양). A/B/C ablation(ADR-0014 Amendment2). 분류: ✅
- **Domain-conditioned joint training** (도메인 조건 합동학습) — naive 전이(차원 불일치) 대신, union 어휘 + `country/housing_type/label_schema` 조건으로 다국가(CN/KR/EU)를 한 모델이 합동학습. 한국단독 vs 합동 비교. 구현 META 토큰(ADR-0013). 분류: ✅
- **plan_scope / units** (도면 종류 / 세대수) — 생성 단위 조건. `unit`(단위세대, 기본) / `floor`(층평면도=단위세대 조합). 자연어로 지정("3세대 층평면도"). unit=units 1, floor=1~n(ADR-0016). 분류: ✅
- **Generation-unit two levels** (생성 단위 2레벨) — 단위세대(기본 빌딩블록) / 층 조립(공용코어+배치). AI-Hub 원본=층평면도를 현관 기준 세대 분할(`iter_units`). 분류: ✅(ADR-0016)

## 6. 평가 (Evaluation)

- **CAD-quality metrics** (CAD 품질 지표) — 1차 평가지표(FID보다 우선). DXF open rate · closed wall-loop rate · room overlap rate · door-on-wall rate · entrance reachability · exterior-window validity · legal pass rate. "도면으로 열리는가"가 목표(ADR-0012 §4). 분류: ✅(평가 설계)
- **valid_rate / uniq_rate** (유효율 / 다양성) — 진단 지표. valid=생성 그래프가 방≥2·cycle 닫힘·문 on-wall 만족 비율. uniq=고유 시퀀스 비율(모드붕괴 검출). 구현 `train_wall_cycle.diagnose`. 분류: ✅
- **Roundtrip preservation** (라운드트립 보존) — graph→tokens→graph 왕복의 정보 보존(토큰 무손실·방/문/창·인접 jaccard·면적·겹침). 코덱 검증(ADR-0015). 분류: ✅
- **FID / KID** (Fréchet/Kernel Inception Distance) — 생성 분포 품질. DiffPlanner 베이스라인 FID 1.23과 비교. **2차 지표**(CAD 품질 다음). 분류: ○

---

## 7. 차용 부품 (Borrowed components — 출처 인용)

표준 백본·SOTA *기법*을 부품으로 인용한다(모델 통째 아님, ADR-0006/LITERATURE.md):

| 부품 | 출처 | 우리 적용 |
|---|---|---|
| 벡터 직접 diffusion 골격 · 경계조건 · FID 베이스라인(1.23) | **DiffPlanner** ('25) | 비교 베이스라인/헤지 |
| alignment loss + random self-supervision (코너/벽 정렬) | **GSDiff** (AAAI'25) | 벽-그래프 정렬 개념 |
| 이산+연속 denoising (직각·평행·코너공유) | **HouseDiffusion** (CVPR'23) | 기하관계 |
| markup representation + constrained decoding | **FML/FMLM** (CVPR'26) | 토큰 직렬화·제약 디코딩 기법 |
| verifiable rewards (제약→보상) | **RLVR** (ACL'26) | (예정) 법규 reranking |

## 8. Novelty 요약 (우리 기여 — 정직)

1. **Wall-cycle + opening 토큰 표현** — 한국 g-0.4 도면을 위한 생성 가능 직렬화(겹침0·무손실). ✅
2. **다국가 도메인 조건 생성** (CN/KR/EU) — 플로어플랜 문헌상 희소(투고 전 서베이 확인). ✅
3. **한국 소버린 3층** (데이터+룰+법규) — 한국 건축 지식·법이 엔진에 내장. ✅
4. **한국 규제-인식 생성** — SOTA 법규 준수율 ≈0% → 한국만으로도 필드 최초. ✅
   ※ "다국가 규제-인식"은 미보유(법규엔진 현재 한국 전용, ADR-0014 Amendment). 정직 한정.
5. **HITL ablation 평가** (Parsed/Corrected, 성능+데이터 증분 두 축). ✅
6. **CAD-품질 우선 평가** (DXF·overlap·reachability > FID). ✅

**기여가 *아닌* 것(정직):** R2G 추출(established) · Transformer 백본(표준) · next-token 학습(표준).

## 9. 폐기 용어 (쓰지 말 것 — ADR README §5)

| 옛 표현 | 현재(정본) | 근거 |
|---|---|---|
| T-라인 / G-라인 | Parsed / Corrected | ADR-0009 |
| treemap (규칙기반 배치) | wall-cycle 생성 AI | ADR-0006/0012 |
| bbox 회귀 / 박스 | (폐기) | ADR-0006 |
| "GSDiff로 엔진 교체" | ONE 엔진 유지 + 생성 타깃이 wall-cycle | ADR-0012 |
| "Mask R-CNN" (사업계획서 옛 명칭) | YOLOv8-seg | ARCHITECTURE §3 |

---

## 부록 — 검증/측정값 (`[잠정]` = 학습 완료 후 확정)

| 항목 | 값 | 출처 |
|---|---|---|
| 코덱 라운드트립(전수 40,495) | 토큰무손실 100% · 방/문/창 100% · 인접 jaccard 1.0 · 면적 완벽 · 겹침 med 0 | ADR-0015 |
| 토큰 길이 | mean 419 · max 1067 (max_len 1024 커버) | `tokens_parsed_apt/manifest` |
| 미니셋 진단 valid (constrained) | T1 0.91 · T3 0.81 (모드붕괴 없음) | `train_wall_cycle` |
| 메인 학습(Parsed APT 29,641) | `[잠정]` ep15 valid 0.61 상승 중 (40ep 진행) | `/tmp/wc_train.log` |
| Phase1 데이터 | APT+현관1+방수≤25+통과 = 29,641 (train 26,646) | ADR-0011/0014 |

관련: [adr/README.md](adr/README.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [LITERATURE.md](LITERATURE.md) · [for_review/technical_brief.md](for_review/technical_brief.md)
