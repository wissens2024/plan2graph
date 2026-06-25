# KorPlan-AR 결과 종합 기록지

평가: `eval_ar_geom` n=200, **seed=42**, constrained+orthogonal, overlap=RLVR 표준정의(arXiv:2605.14117), selfint=OGC.
⚠️ 탐색 단계 수치(no-room-permutation, dim_ff=1408). 최종은 튜닝버전+seed 재학습.

| 모델 | decoded | **clean** | selfint=0 | overlap<.25 | single | rooms |
|---|---|---|---|---|---|---|
| RPLAN ep10 | 100% | **2%** | 2% | 30% | 54% | median 7 mean 7.3 |
| RPLAN ep20 | 100% | **6%** | 6% | 36% | 54% | median 7 mean 7.3 |
| RPLAN ep30 | 100% | **16%** | 16% | 50% | 56% | median 7 mean 7.1 |
| RPLAN ep40 | 100% | **28%** | 28% | 56% | 66% | median 7 mean 7.2 |
| RPLAN ep50 | 100% | **37%** | 37% | 58% | 68% | median 7 mean 7.0 |
| RPLAN ep60 | 100% | **40%** | 41% | 64% | 68% | median 7 mean 6.9 |
| RPLAN ep70 | 100% | **46%** | 46% | 74% | 73% | median 7 mean 7.1 |
| RPLAN ep80 | 100% | **42%** | 42% | 63% | 74% | median 7 mean 7.0 |
| 한국 target-only nosnap | 100% | **24%** | 26% | 70% | 8% | median 14 mean 14.7 |
| 한국 target-only snap | 100% | **14%** | 16% | 40% | 40% | median 15 mean 14.6 |
| 한국 RPLAN→FT nosnap | 100% | **32%** | 36% | 72% | 6% | median 15 mean 14.7 |
| 한국 RPLAN→FT snap | 100% | **20%** | 20% | 60% | 44% | median 15 mean 14.6 |

## 핵심 비교
- **snap 효과**: target-only/FT 각각 nosnap↔snap clean 비교
- **pretrain 효과**: target-only↔RPLAN→FT clean 비교
- **RPLAN 수렴**: ep10~80 clean 곡선 (평탄 여부)
