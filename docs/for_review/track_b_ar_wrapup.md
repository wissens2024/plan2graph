# Track B (KorPlan-AR) 논문 랩업 — 결과·개선·향후과제 [2026-06-20]

> 범위: **Track B(AR)가 생성한 데이터만**으로 논문 작성. 실측 근거는 `docs/EXPERIMENTS.md §KorPlan-AR 80M`.
> 핵심 주장: *"SOTA(FMLM) 엔진을 가져온 게 아니라 방법을 충실히 재현 → RPLAN에서 SOTA급 작동 → 그 위에 한국 데이터 + 규제-인식 + 한국특성 조치가 기여."* (FMLM을 이긴다는 주장 아님.)

---

## 1. 최종 결과 (AR 생성 데이터만)

### 1-A. 엔진 = SOTA 방법 충실 재현 (코드 레벨, 차용 0줄)
| FMLM 요소 | KorPlan-AR | 일치 |
|---|---|---|
| decoder-only AR + LLaMA-3(RoPE/RMSNorm/SwiGLU/SDPA) | 동일 | ✅ |
| 방 토큰 내림차순 | 면적 내림차순 | ✅ |
| constrained decoding | 동일(닫힘·on-wall·cycle≥4) | ✅ |
| 80M·50ep·batch32·lr1e-4·RPLAN | 동일(77.4M) | ✅ |
| permutation 증강 | 미구현 | ❌ (유일 갭) |

### 1-B. RPLAN = SOTA급 baseline (깨끗한 GT)
- valid **100%** · full_clean **40%** · footprint 단일 **90%** · 실겹침 median **0** · 대각선 **0**.
- 실제 RPLAN GT: footprint 단일 100% → 엔진이 깨끗한 데이터를 깨끗하게 재현함을 입증.

### 1-C. 한국 = pretrain(RPLAN ep50) → finetune
| 모델 | valid | selfint=0 | overlap<.25 | footprint단일 | 개구부 |
|---|---|---|---|---|---|
| Korean-alone (pretrain無) | 0.47 | 5% | 78% | 8% | 96% |
| **finetune-1e-4** | **0.688** | **25%** | **92%** | 0% | 98% |
| finetune-5e-5 | 0.656 | 22% | 88% | 2% | 92% |
- **RPLAN pretrain의 신뢰 이득 = valid 0.47→0.69**(학습 held-out 지표). ⚠️ 표의 selfint/overlap 등 *생성 기하*는 **n=40 노이즈**라 pretrain vs 단독 차이 단정 불가(§1-D 재측정). 앞서 "selfint 5×"는 단발 노이즈 — **철회**.
- lr 1e-4 vs 5e-5: 차이 미미.

### 1-D. ★핵심 발견 — footprint 지표 미스매치 (측정으로 확정)
- **간격 허용 재측정 (GT, n=300)**: 한국 GT 단일% = 0%(strict) → 47%(1%) → **99%(2%)**. RPLAN = 100% 전 구간.
- ★**한국 GT는 ~2% 간격(벽두께) 허용 시 99% 단일 건물.** = 방을 벽두께만큼 띄워 그리는 **현실적 표현**이지 파편화 아님. ∴ strict footprint 지표(RPLAN식 변-맞닿음)는 한국엔 **부적합** — "한국 0%"는 **지표 버그**, 데이터·엔진 정상.
- **생성물도 벽2%서 70~78% 단일**(단독 78%·ft 70%) → 엔진이 응집된 벽-분리 아파트 생성.
- ★**진짜 병목 = selfint(방 폴리곤 자기교차) ~8~12%** (연결성·개구부 아님) → clean(벽인식) ~8~12%.

---

## 2. 현재 버전에서 더 할 수 있는 것 (Python 후처리 — 재학습 없이)

### 2-A. 벽두께-인식 연결성 ✅검증완료 + selfint 직교수리 (진짜 레버)
- ✅**연결성 = 문제 아님**(측정): GT 99%·생성 70~78% (벽2%, §1-D). 벽두께-인식 지표로 평가하면 한국 연결성 정상 — Python만으로 즉시 정정(재학습 0).
- ★**진짜 Python 레버 = selfint 직교수리.** 실측: `buffer(0)`는 약함(selfint 8→20%·clean 5→8%, n=40). selfint가 수리後도 20%로 지배 → **다음 = axis-aligned 직사각화 수리**(buffer(0)보다 강함)로 selfint 근본 제거 → clean 급등 기대. 이게 현재버전 한국 clean율 핵심.
- (선택) 인접 방 코너 벽두께 내 스냅(gap close)으로 시각적 벽 1급 도면화.

### 2-B. ★반복 재생성/보정 알고리즘 (사용자 아이디어 — 강력)
> 관찰: AR 생성은 **독립·고분산** → 한 번엔 자주 틀리나, *분포에 좋은 표본이 있음*(RPLAN clean 40% = 약 2.5번에 1개).
- **L1 rejection sampling (즉시 가능)**: 생성 → 품질 게이트(겹침0·연결·세장) 통과까지 재생성. = "잘 나올 때까지 그려 달라". 이미 `render_geomclean`의 clean 필터가 토대. *AR은 생성이 싸서 매우 효과적.*
- **L2 진단-피드백 보정**: 한 도면의 **구체적 문제 진단**(겹친 방 X·끊긴 방 Y·법규 위반 Z) → (a) 기하 수리(스냅/재배치) 또는 (b) 그 부분만 타깃 재생성. = ADR-0012/0019 **verify→repair→rerank** 루프.
- 논문 가치: "단발 생성 품질"이 아니라 **"보정 루프 후 품질"**로 평가 = 우리 신규성(규제·검증을 생성기에 결합).

### 2-C. 규제/법규 DB 연결 (= §2-B의 게이트에 법규 포함)
- 생성 도면 → geomgraph → `rules_legal.check_legal`(채광/환기 창·면적·대피, KR 스코프) → 위반 시 repair 또는 reject → rerank.
- **한국은 개구부 96% 보유 → 법규 적용 가능**(RPLAN은 N/A). 이게 신규성의 무대.
- 기존 자산: `regulation.py`(verify→repair→rerank)·`rules_legal.py`·법령DB(`legal/`, 175조문, [[legal_db_handoff]]). Phase1서 채광 준수율 repair로 raw 50→98% 실측됨.
- → **규제-인식 rejection sampling**: "법규 통과할 때까지 그려 달라" = 사용자 아이디어 + 규제 = 핵심 기여.

---

## 3. Corrected 버전(향후 데이터)에서 챙길 것
- **Parsed→Corrected 보정**: 흡수복도 복원·방 파편화 정리·**벽 공유 표현 정규화**(벽두께 일관 인코딩으로 footprint 정상화).
- **HITL 에디터 활용**(이미 구축: `topoedit`·`edit_server`) — 사람이 위상·기하 보정.
- **라벨 정확도**(OCR 라벨 조인 — 파우더룸 등 오라벨 교정).
- 측정: Corrected가 Parsed 대비 selfint·연결성·법규 준수율을 얼마나 올리나 (A/B/C ablation).

---

## 4. 향후 과제 (모델/방법)
- **permutation 증강** 구현 (FMLM의 유일한 미구현 요소, 작은 한국 데이터에 특히 유효).
- 모델/컴퓨트 확대(80M→더 큼), 더 긴 학습(plateau까지).
- Track A(diff)·C(raster) 비교(3-트랙 헤지, ADR-0019).
- 완성층: 가구·치수·DXF (Phase2, ADR-0014).

---

## 부록 — 산출물 위치
- 결과 수치: `docs/EXPERIMENTS.md §KorPlan-AR 80M`.
- 도면: `docs/runs/korplan_ar_{rplan,korean}_ep50_montage.png` · `Downloads/ar_{r_ep50,k_ep50,korean_ft}/`.
- 모델: `ckpts/korplan_ar_r_fmlm80m_pretrain.pt`(RPLAN 사전학습 동결) · `korplan_ar_korean_ftR.pt`(1e-4) · `korplan_ar_korean_ftR_lr5e5.pt`(5e-5) · `korplan_ar_k_fmlm80m.pt`(단독).
