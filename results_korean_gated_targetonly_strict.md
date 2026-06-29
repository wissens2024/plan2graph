# 한국 gated target-only (미사전학습) — strict 곡선

eval n200 seed42 country0. production(gated RPLAN→FT)과 동일 데이터/레시피, --resume만 없음.
→ FT(사전학습) 효과 분리: 이 곡선 vs results_korean_gated_strict.md(=RPLAN→FT) 비교.

| ep | clean(strict) | clean(loose) | single | selfint=0 | rooms |
|---|---|---|---|---|---|
| ep10 | **3%** | 4% | 55% | 4% | median 13 mean 13.1 |
| ep20 | **8%** | 12% | 58% | 12% | median 14 mean 13.9 |
| ep30 | **11%** | 15% | 54% | 16% | median 14 mean 14.0 |
| ep40 | **12%** | 16% | 66% | 17% | median 14 mean 14.2 |
| ep50 | **16%** | 20% | 64% | 22% | median 14 mean 14.3 |
| ep60 | **24%** | 30% | 70% | 31% | median 14 mean 14.2 |
| ep70 | **28%** | 34% | 68% | 36% | median 14 mean 14.3 |
| ep80 | **33%** | 38% | 68% | 39% | median 14 mean 13.8 |
| ep90 | **22%** | 28% | 65% | 28% | median 14 mean 14.1 |
| ep100 | **32%** | 38% | 65% | 42% | median 14 mean 14.2 |
