# 설계 정본 (Canon) — 헷갈리면 먼저 여기를 읽어라

> ADR이 12개로 늘며 일부가 옛 결정을 덮어써(supersede) 서로 모순돼 보인다.
> 이 문서가 **현재 살아있는 설계 하나**를 평이하게 묶는다. 충돌하면 이 문서 +
> 각 ADR의 최신본이 정본이다. **세션마다 재설계 금지.** (목표는 고정, 설계도 고정.)

---

## 1. 목표 (불변 — 절대 안 바뀜)
한국형 **소버린 생성형 도면 엔진**. 입력(방 구성·관계)으로 **완성 건축 도면**
— **벽(두께)·방·문·창·가구·치수** — 을 생성하고, **한국 법규를 준수**하며,
**이미지 + AutoCAD DXF**로 출력한다.
👉 박스+색칠이 아니라 *그려서 쓰는 도면*이 목표다.

> **목표 2단계 분리 (ADR-0014):**
> - **Phase 1 (지금 · SCI 논문)** = DiffPlanner/GSDiff/FMLM급 **벡터 레이아웃**(벽·방·문) + 한국형 + **다국가 도메인 조건**(CN/KR/EU, ADR-0013) + **국가별 규제-인식 생성**(법규엔진 — 우리 신규성). *완성형 상용 도면 아님.*
> - **Phase 2 (나중 · 상용)** = 가구·치수·완성형 as-built, **하이브리드(생성+파라메트릭 CAD 솔버)**, 알바 데이터 보정 후. ([[commercial-is-solver-not-generative]])
> - **Phase 1 먼저 채운다.** Phase 1 전제 = 한국 기하 품질이 DiffPlanner급 도달(40k/wall-cycle 진행 중).

### 1-A. Phase 1 대상·범위 (한눈에 — 흩어진 ADR 묶음, 정본)
> 지금 **Phase 1만** 진행. 아래가 흩어진 ADR(0011/0014/0016)을 한 곳에 모은 단일 기준이다.

| 항목 | 내용 | 근거 |
|---|---|---|
| **데이터 대상** | **APT × 현관1(단위세대) × 방수≤25 × Parsed × validation통과 = 29,641건** (train 26,646) | ADR-0011 Amend·0014 Amend2 |
| 생성 단위 | **단위세대**(현관 항상 1). 층평면도(현관 2+)는 Phase1 아님(후속) | ADR-0016 ① |
| "방수"의 의미 | **공간(노드) 수** — 거실·주방·현관·발코니·실외기실 등 포함, **침실 아님**. 상한 25=p97(26+ 병합·과검출 차단) | ADR-0011 Amend |
| 산출물 | DiffPlanner/GSDiff급 **벡터 레이아웃**(코너·벽·방 폴리곤·문) + 이미지/DXF. **완성형(가구·치수·as-built) 아님** | ADR-0014 |
| 신규성(기여) | ① 다국가 도메인 *조건* 생성(CN/KR/EU) ② **한국 규제-인식 생성**(SOTA≈0% → 필드 최초) ③ 한국 데이터셋 | ADR-0013·0014 |
| 엔진 | **KorPlan-Diff**(A·코너그래프 확산+정렬손실) / **KorPlan-AR**(B·wall-cycle 자기회귀) 비교 | korplan-model-naming |
| 평가 | CAD 품질(DXF open·wall-loop·overlap·reachability·door-on-wall) + **법규 준수율**(채광 50→98%, SCI 헤드라인) > FID | ADR-0012 |
| A/B/C ablation | A=Parsed 29,641 / B=Corrected 같은 29,641(순수 품질) / C=Corrected+증분(양 효과) | ADR-0014 Amend2 |
| **범위 밖 = Phase 2** | 가구·치수·완성형 as-built · 파라메트릭 CAD 솔버 하이브리드 · 상용화 | ADR-0014 |

> ⚠️ **혼동 주의**: GUI 🗂 데이터셋 도면의 "방 개수" 필터는 **침실(찐 방)** 기준이고, 위 Phase1 데이터 정의의 **"방수≤25"는 공간(노드) 수** 기준 — **다른 지표**다.

## 2. 현재 확정 파이프라인 (처음→끝, 평이하게)
```
데이터 (AI-Hub 한국 + RPLAN 중국/CubiCasa 유럽 글로벌)
  → R2G 파싱 → 통일 그래프 (geomgraph g-0.4) + 기하 복도 복원(ADR-0017)
  → 두 조건 비교: [Parsed=파서출력]  ∥  [Corrected=사람보정]
  → 생성 엔진 = KorPlan (소버린, 2 패러다임 A/B 비교): wall-cycle + opening 토큰   ※ bbox 폐기
        · KorPlan-Diff (엔진 A: 코너-그래프 확산 + 정렬손실)
        · KorPlan-AR   (엔진 B: wall-cycle 자기회귀)
        (사전학습 RPLAN 중국 → 파인튜닝 한국 APT; 체크포인트 save/init)
  → 완성층 (neuro-symbolic): 문·창 세부 · 가구 · 치수 · 축척
  → cadrender → 이미지 + DXF
  → 평가: CAD 품질 먼저  >  FID
```

## 3. 핵심 결정 7가지 (현재 정본, 한 줄씩)
1. **ONE 소버린 엔진** — 외부 엔진으로 갈아타지 않는다. **KorPlan-Diff/AR 2 패러다임은 *내부 A/B 비교*(둘 다 우리 것)**지 엔진 swap 아님. 바뀌는 건 *생성 타깃*뿐. (ADR-0006 · `korplan-model-naming`)
2. **표현** = corner + wall + room-cycle + role. 방-방 경계 = `wall|open|door`(벽 없으면 벽 안 그림). 복도 항상 노드(흡수된 복도는 **기하 복원**, ADR-0017). **bbox 폐기.** (ADR-0010, 0006/0012)
3. **생성 타깃** = wall-cycle + **opening 토큰**(GSDiff류 정렬손실). 문·창의 *존재·연결*은 생성, *세부*는 완성층. (ADR-0012)
4. **완성층** = 가구(검출 5종 + 역할추론 카탈로그) · 치수 · 축척. (ADR-0010, `fixture_catalog.py`)
5. **비교축** = Parsed vs Corrected (= 사람보정의 가치). (ADR-0008/0009) ※ 옛 "T/G"는 폐기
6. **데이터** = 자동 사용은 APT 정형 단일세대만, 나머지 사람 판단. 위상오류(흡수·오라벨) 정제 우선. (ADR-0011, 0012)
7. **평가** = CAD 품질(DXF open·wall-loop·overlap·door-on-wall·reachability·legal pass) > FID. 법규는 loss 아니라 **verifier→repair→reranking**. (ADR-0012)

## 4. ADR 지도 (현행 vs 폐기 — 어느 게 살아있나)
| # | 제목 | 상태 |
|---|---|---|
| 0001 | Generator 추상화(인터페이스·레지스트리) | ✅ Accepted (인프라) |
| 0002 | T-라인/G-라인 분리 | ⚠️ 라인분리 원칙만 잔존. **용어 T/G·treemap은 폐기**(→0008/0009/0012) |
| 0003 | G 단일 진실(staging 하나) | ✅ Accepted (지금 `staging/corrected` 하나) |
| 0004 | 렌더 2산출물(생성형+DXF) | ✅ Accepted. ⚠️ treemap 부분 폐기 |
| 0005 | 데이터 회계 = 구성×처분 | ✅ Accepted |
| 0006 | **ONE 소버린 엔진** | ✅ Accepted ★골격 |
| 0007 | 기하모델 A/B + 돌림방지 | ❌ **Superseded by 0008** (단 "돌림방지" 원칙은 유효) |
| 0008 | **Parsed/Corrected · 그래프JSON 편집 · SVG 폐기** | ✅ Accepted ★ |
| 0009 | **용어 Parsed/Corrected · R2G** | ✅ Accepted ★ |
| 0010 | **표현 g-0.4 · 완성 7레이어 · 가구 2계층** | ✅ Accepted (0012로 일부 refine) ★ |
| 0011 | 데이터 구분자 = APT 정형 단일세대만 | ✅ Accepted |
| 0012 | **생성 타깃 wall-cycle+opening · 평가 · 학습순서** | ✅ Accepted ★ |
| 0013 | 다국가 조건 메타(country/dataset/housing/label_schema) | ✅ Accepted |
| 0014 | 목표 2단계 분리(Phase1 SCI / Phase2 상용) — novelty 정직화 | ✅ Accepted |
| 0015 | **wall-cycle+opening 토큰 직렬화 코덱**(전수 40k 검증) | ✅ Accepted ★ |
| 0016 | **생성 단위 2레벨**(단위세대/층) · scope·세대수 조건 | ✅ Accepted |
| 0017 | **기하 기반 복도 복원**(비직사각형 거실→복도 분리, geomgraph.build 통합) | ✅ Accepted |
| 0018 | **다중 도메인 아키텍처**(프로파일 레지스트리 + 공용백본/개별어댑터, dataset별 분리) | ✅ Accepted |
| 0019 | **3-트랙 엔진 헤지**(A joint diff·B 닫힘AR·C raster) + 규제-피드백 공통 뒤단(벡터-first 단독 폐기) | ✅ Accepted ★ |
| 0020 | **한국 벽두께 정규화 snap_split**(축좌표 스냅+edge-split+문 재매칭) — 문·창·벽두께 처리, 내부벽 0.8→27.8·문부착 100%·방손실 1.5%(ADR-0015 union 27% 대체) | 🔶 Proposed (A/B 확정) |

★ = 지금 가장 중요한 정본 (0006·0008·0009·0010·0012).

## 5. 폐기된/혼동 용어 — 다시 쓰지 마라
| 옛 표현 (쓰지 마) | 현재 (정본) | 근거 |
|---|---|---|
| T-라인 / G-라인 | **Parsed / Corrected** | 0009 |
| treemap(규칙기반 배치) | **생성 AI(wall-cycle)** | 0002정정/0006/0012 |
| "GSDiff로 엔진 교체" | **ONE 엔진 유지 + 생성 타깃이 wall-cycle** | 0012 |
| bbox 회귀 / 박스 | **폐기** | 0006/0012 |
| "기하 낮춘 T vs 강한 G" | Parsed/Corrected 입력 품질 비교 | 0008 |

## 6. 안티-플립 규칙 (클로드 자신에게)
- **도면이 안 나오면 = 생성 부품(타깃·표현·학습)만 의심, 전체 재설계 금지.** (ADR-0007 "돌림방지" — 이 원칙만은 유효)
- 목표·파이프라인(§1·§2)은 고정. 새 SOTA(CE2EPlan/TLC/FMLM/RLVR…)는 *부품/비교군*일 뿐 **방향 전환 근거 아님**. (0012)
- 입장 변경은 **새 증거 있을 때만** + 이유를 ADR/메모리에 남긴다. 어려워졌다고 설계를 바꾸지 않는다.

## 7. 지금 작업 위치 = 검증된 현재 상태 (2026-06-17, 실측) ★단일 진실
> **세션마다 같은 걸 다시 검증하지 마라. 아래는 직접 렌더/측정으로 확인된 사실이다.**
> 깨끗한 벡터 기하(겹침 없는 직각 타일링 방+벽)를 만드는 게 모든 트랙의 관문.

### 7-A. 각 엔진/경로 상태 (2026-06-17 심야 갱신)
| 경로 | 상태 | 비고 |
|---|---|---|
| **A = KorPlan-Diff** (코너+엣지 확산) | 🔄 **GSDiff 방법으로 재구현·RPLAN 학습중**(GPU1, `korplan_diff_r_gsdiff`) | 옛 corner-only는 천장(폐기). 이번 세션 `align_proto.py`에 GSDiff 기법 이식: 다중스케일 정렬손실·Fourier좌표·시간가중·clip0.1·batch256·깊이12. 미구현=코너 semantics 조건. 1M step 규모 필요 |
| **B = KorPlan-AR** (wall-cycle 자기회귀) | ✅🔄 **현재 앞섬·주력후보**. AR-R ep150 깨끗한 직각 7~8방 평면. RPLAN 완성차 연장중(GPU0, `korplan_ar_r.pt`) | 빠름·소버린·닫힘 구조보장·자연어조건 적합. orthogonal 마스크 작동(축정렬 97.6%) |
| **C = raster→벡터** | ⏸ 보류(이미지 코헤런트, 벡터화 거침). 규제레이어 결합됨 | — |
| **GSDiff** (외부 GPL3) | ✅ RPLAN 깨끗 타일링+DXF **직접 검증**(`gsdiff_to_cadrender.py`). 청사진(=A 재구현 근거) | 코드 차용 금지(GPL3). 알고리즘만 재구현=A |
| **DiffPlanner** (외부) | ❌ 박스 산포(실패) | 재평가 말 것 |
| **규제 레이어** | ✅ `src/plan2graph/regulation.py` verify→repair→rerank. 깨끗 기하 입력 필요 | 재구현 말 것 |

### 7-B. ★잠긴 결정 (재논의/변경 금지)
1. **RPLAN 완벽 먼저(A·B), 그 다음 한국.** 판정=**사용자가 렌더 보고**(클로드 "완벽/부족" 선언 금지).
2. **학습 = FMLM 동일 사양(50 epoch). 완료까지, 중간 확인 없음.**
3. **품질>수량.** RPLAN 99% clean(필터 불요). 한국 Parsed 45%만 통과→정제.
4. **A·B·C 모두 한국=정제셋.** 단일기준 `data/staging/korean_clean_ids.json`(13,325 ID).
5. **정제 한국셋 보존**: `releases/korean_clean_parsed_v1/`(동결·읽기전용). 덮어쓰기/재생성 금지.
6. **freeze RPLAN → 정제 한국 파인튜닝.** vocab 통합(260)이라 AR 전이됨.

### 7-C. ★행동 경고 (이 세션 실패 = 반복금지)
**churn 금지**(매 턴 학습 죽이고/재시작/GPU전환 = "처음부터 다시"의 원인) · **flip-flop 금지**(판정 흔들기, 결과는 렌더로 보여주고 사용자 판정) · **조기진행 금지**(RPLAN 완벽 전 한국 X). [[dont-shift-technical-stance]]

### 7-D. 이미 끝낸 일 (다시 하지 마라)
GSDiff 구조분석·GSDiff방법 align_proto 재구현·옵티마이저 저장복원·한국 품질필터(`scripts/filter_korean_quality.py`)+동결·RPLAN 99%clean확인·AR-K삭제·canonical clean-ID·corner_edge_engine(edge먼저+ckpt-every).
