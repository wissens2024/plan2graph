# 한국 grid256 FT 베이스 ablation — strict 곡선

eval n200 seed42. clean(strict)=도면답게(+대각선0+꼭짓점>=4+외곽 1덩어리·채움·볼록). loose=옛 정의(selfint·overlap·span만).

## 베이스 grid256 ep90 (플래토)

| korean ep | clean(strict/도면답게) | clean(loose/옛) | single | rooms |
|---|---|---|---|---|
| ep10 | **14%** | 18% | 60% | median 14 mean 13.9 |
| ep20 | **28%** | 32% | 75% | median 15 mean 14.4 |
| ep30 | **33%** | 38% | 76% | median 14 mean 14.2 |
| ep40 | **40%** | 48% | 79% | median 14 mean 14.2 |
| ep50 | **48%** | 53% | 80% | median 14 mean 14.4 |
| ep60 | **42%** | 48% | 79% | median 14 mean 13.8 |
| ep70 | **46%** | 55% | 80% | median 14 mean 14.7 |
| ep80 | **56%** | 63% | 84% | median 14 mean 14.1 |

## 베이스 grid256 ep110 (피크)

| korean ep | clean(strict/도면답게) | clean(loose/옛) | single | rooms |
|---|---|---|---|---|
| ep10 | **19%** | 22% | 56% | median 14 mean 14.1 |
| ep20 | **37%** | 42% | 72% | median 14 mean 14.4 |
| ep30 | **34%** | 41% | 74% | median 14 mean 14.2 |
| ep40 | **42%** | 46% | 79% | median 14 mean 14.2 |
| ep50 | **43%** | 46% | 76% | median 14 mean 14.1 |
| ep60 | **50%** | 54% | 82% | median 14 mean 14.2 |
| ep70 | **44%** | 55% | 84% | median 14 mean 14.4 |
| ep80 | **50%** | 57% | 82% | median 14 mean 13.9 |

