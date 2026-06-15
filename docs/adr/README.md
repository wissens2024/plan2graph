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

## 2. 현재 확정 파이프라인 (처음→끝, 평이하게)
```
데이터 (AI-Hub 한국 + RPLAN/CubiCasa 글로벌)
  → R2G 파싱 → 통일 그래프 (geomgraph g-0.4)
  → 두 조건 비교: [Parsed=파서출력]  ∥  [Corrected=사람보정]
  → 생성 엔진 (ONE): wall-cycle + opening 토큰 생성   ※ bbox 폐기
        (사전학습 RPLAN/CubiCasa → 파인튜닝 한국 APT)
  → 완성층 (neuro-symbolic): 문·창 세부 · 가구 · 치수 · 축척
  → cadrender → 이미지 + DXF
  → 평가: CAD 품질 먼저  >  FID
```

## 3. 핵심 결정 7가지 (현재 정본, 한 줄씩)
1. **ONE 엔진** — 엔진을 갈아타지 않는다. 바뀌는 건 *생성 타깃*뿐. (ADR-0006)
2. **표현** = corner + wall + room-cycle + role. 방-방 경계 = `wall|open|door`(벽 없으면 벽 안 그림). 복도 항상 노드. **bbox 폐기.** (ADR-0010, 0006/0012)
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

## 7. 지금 작업 위치 (2026-06-14)
- ✅ g-0.4 빌더(벽두께·경계태그·open벽·Tier A 가구) 구현·검증
- ✅ ADR-0010/0011/0012 확정, 본 정본 작성
- ▶ 다음(ADR-0012 순서): **네이티브 생성기를 wall-cycle+opening 타깃으로 재작성 → 500~2k 진단 미니셋(Tier1~4)에서 collapse 진단 → 장기학습.** (cadrender g-0.4 렌더는 병행 가능)
