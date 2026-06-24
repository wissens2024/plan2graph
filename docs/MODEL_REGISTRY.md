# KorPlan-AR 모델 버전 레지스트리

> 버전이 많아 혼동·실수 방지용 단일 기록. 모든 모델의 *축(axis)별 설정*을 명시. 수치는 `results_report.md`/`results_roomperm_rplan.md`.

## 튜닝 축 (무엇을 바꿔가며 비교하나)
| 축 | 값 | 의미 |
|---|---|---|
| **corpus** | RPLAN / 한국 | 사전학습 도메인 vs 타깃 |
| **pretrain** | RPLAN-only · target-only · RPLAN→FT | 사전학습 효과 |
| **snap** | nosnap / snap | 벽두께 정규화(표현) 효과 (ADR-0020) |
| **room-perm** | off / on(p=0.5) | 방순서 증강 효과 (FMLM Table6) |
| **seed** | 0(미설정) / 42(고정) | 재현성 |
| **dim_ff** | 1408 | (2048은 10GB OOM — 전 모델 1408 고정) |
| **grid** | 128 / 256 | 좌표 정밀도 (현재 전부 128, 256은 Phase2) |

## 현행 모델 (2026-06-24)

| 모델 | corpus | pretrain | snap | room-perm | seed | grid | dimff | ep | 용도/Phase |
|---|---|---|---|---|---|---|---|---|---|
| `korplan_ar_r_fmlm80m_pretrain_v2` | RPLAN | RPLAN-only | — | off | **0** | 128 | 1408 | 10~80 | P1 사전학습 베이스라인 (ep70수렴) |
| `korplan_ar_k_nosnap` | 한국 | target-only | nosnap | off | **0** | 128 | 1408 | 50 | P1 매트릭스 |
| `korplan_ar_k_snap` | 한국 | target-only | **snap** | off | **0** | 128 | 1408 | 50 | P1 매트릭스 |
| `korplan_ar_rk_nosnap` | 한국 | **RPLAN→FT** | nosnap | off | **0** | 128 | 1408 | 100 | P1 매트릭스 |
| `korplan_ar_rk_snap` | 한국 | **RPLAN→FT** | **snap** | off | **0** | 128 | 1408 | 100 | P1 매트릭스 |
| `korplan_ar_r_roomperm_seed42` | RPLAN | RPLAN-only | — | **on** | **42** | 128 | 1408 | 10~80 | room-perm 효능 + 재현성 |

## ★당신 질문 직답
- **GPU1 매트릭스(k_nosnap·k_snap·rk_nosnap·rk_snap)**: **seed=0, room-perm=off** (전부). = 순수 **snap × pretrain 2×2 ablation**.
  - 가로축 snap: nosnap ↔ snap → **snap 효과**
  - 세로축 pretrain: target-only(k) ↔ RPLAN→FT(rk) → **사전학습 효과**
  - ⚠️ **무시드라 "정확 재현"은 안 됨**(탐색용). 절대값보다 *상대 비교*(snap/pretrain 우열)가 목적.
- **seed+room-perm이 들어간 건 `korplan_ar_r_roomperm_seed42`(RPLAN) 하나뿐.**

## Phase 구분 (실수 방지 핵심)
- **Phase 1 (현재·탐색)**: seed 없음, room-perm 없음, grid 128. → "snap 도움되나? pretrain 도움되나? room-perm 효능? 수렴 ep?"의 *방향*을 잡는 용도. 절대 재현수치 아님.
- **Phase 2 (최종·1회)**: 탐색 결론 + **room-perm + seed42 + (snap결정) + grid256** 다 합쳐 **재학습** → 이게 논문 정본·재현 가능 수치.

## 비교로 답할 질문
| 비교 | 질문 |
|---|---|
| k_nosnap ↔ k_snap | snap이 target-only를 개선? |
| rk_nosnap ↔ rk_snap | snap이 FT를 개선? |
| k_* ↔ rk_* | pretrain(FT)이 도움? (논문 가설) |
| v2(no-perm) ↔ roomperm_seed42 | room-perm 효능? (ep70 44% 대비) |
| roomperm_seed42 재실행 | seed로 동일 재현? |

## 레거시 (참고·비교 제외)
- `korplan_ar_k_fmlm80m`(6/19 한국, 구 코덱) · `korplan_ar_korean_ftR`(6/20) · `korplan_ar_r_fmlm80m_pretrain`(깨진코덱기 재학습) — 소실/구버전. 현 비교엔 미사용.
