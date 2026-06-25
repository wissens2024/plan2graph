# RPLAN room-permutation+seed 결과 (재현가능)

eval: n=200, **seed=42**, 표준 overlap(RLVR), country=CN. 학습=`--room-perm --seed 42`.

| 모델 | decoded | **clean** | selfint=0 | overlap<.25 | single |
|---|---|---|---|---|---|
| room-perm+seed ep10 | 100% | **2%** | 4% | 36% | 48% |
| room-perm+seed ep20 | 100% | **10%** | 10% | 48% | 56% |
| room-perm+seed ep30 | 100% | **12%** | 14% | 45% | 59% |
| room-perm+seed ep40 | 100% | **18%** | 21% | 56% | 66% |
| room-perm+seed ep50 | 100% | **30%** | 32% | 66% | 74% |
| room-perm+seed ep60 | 100% | **44%** | 46% | 70% | 79% |
| room-perm+seed ep70 | 100% | **38%** | 42% | 65% | 73% |
| room-perm+seed ep80 | 100% | **46%** | 47% | 72% | 80% |
| [대조] no-perm v2 ep70 | 100% | **46%** | 46% | 74% | 73% |
| [대조] no-perm v2 ep80 | 100% | **42%** | 42% | 63% | 74% |

## 보는 법
- **room-perm 효능**: room-perm ep70/80 clean ↔ no-perm v2 ep70/80 clean 비교
- **수렴**: ep10~80 곡선이 no-perm보다 빠르거나 높은가
- **재현성**: seed=42 고정 → 다른 연구자가 같은 코드+시드로 동일 수치 재현
