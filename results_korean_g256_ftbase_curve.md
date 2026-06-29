# 한국 gated grid256 FT — 베이스 ep90 vs ep110 ablation

eval n200 seed42 country0. 데이터=tokens_korean_gated_g256(GT clean 99%%). 비교기준: grid128 gated FT 피크 65%%.

## 베이스 grid256 ep90 (RPLAN clean 56%)

| korean ep | clean | selfint=0 | overlap<.25 | single | rooms |
|---|---|---|---|---|---|
| ep10 | **18%** | 20% | 43% | 60% | median 14 mean 13.9 |
| ep20 | **32%** | 34% | 62% | 75% | median 15 mean 14.4 |
| ep30 | **38%** | 39% | 72% | 76% | median 14 mean 14.2 |
| ep40 | **48%** | 48% | 73% | 79% | median 14 mean 14.2 |
| ep50 | **53%** | 56% | 78% | 80% | median 14 mean 14.4 |
| ep60 | **48%** | 52% | 81% | 79% | median 14 mean 13.8 |
| ep70 | **55%** | 58% | 82% | 80% | median 14 mean 14.7 |
| ep80 | **63%** | 65% | 84% | 84% | median 14 mean 14.1 |

## 베이스 grid256 ep110 (RPLAN clean 66%)

| korean ep | clean | selfint=0 | overlap<.25 | single | rooms |
|---|---|---|---|---|---|
| ep10 | **22%** | 23% | 44% | 56% | median 14 mean 14.1 |
| ep20 | **42%** | 42% | 62% | 72% | median 14 mean 14.4 |
| ep30 | **41%** | 43% | 70% | 74% | median 14 mean 14.2 |
| ep40 | **46%** | 50% | 76% | 79% | median 14 mean 14.2 |
| ep50 | **46%** | 50% | 76% | 76% | median 14 mean 14.1 |
| ep60 | **54%** | 56% | 84% | 82% | median 14 mean 14.2 |
| ep70 | **55%** | 56% | 74% | 84% | median 14 mean 14.4 |
| ep80 | **57%** | 59% | 82% | 82% | median 14 mean 13.9 |

