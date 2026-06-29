# 산출물 인벤토리 (정본 keep-list) — 2026-06-29

**목적**: 트랙×라인별 **최종 산출물(정본 모델 + 학습 데이터/release)** 만 여기에 기록.
**삭제 규칙**: 여기 KEEP에 **없는** 모델·release·파생데이터 = **일괄 삭제 대상**(나중에).
**불변(항상 유지)**: `data/raw/`, `data/staging/parsed/`, `data/staging/corrected/`, 사전학습 코퍼스.
⚠️ **모델 삭제는 단일 파일 확인 후**(과거 FT모델 실수삭제 사고).

목표 매트릭스 — A·B = parsed↔corrected 비교 / C = corrected 전용 / corrected는 아직 모델 0개:

| 엔진 | parsed | corrected |
|---|---|---|
| A (Diff·그래프) | 예정 | 예정 |
| B (AR·토큰) | ✅ 성공 | 예정 |
| C (상업·신규) | — | 예정 |

---

## ★ 현재 확정 KEEP 모델 (이외 ckpt는 삭제후보)
- `ckpts/korplan_ar_rk_gated_seed42_roomperm_ep150.pt` — **B·parsed 정본**
- `ckpts/korplan_ar_r_roomperm_seed42_ep80.pt` — 위의 **사전학습 base**(RPLAN, room_perm, seed42)

## 0. 공유 자원 (항상 KEEP)
| 종류 | 경로 | 비고 |
|---|---|---|
| 원본 | `data/raw/` | 불변 |
| 소스(자동) | `data/staging/parsed/graphs` (+manifest) | A·B 소스 · 41,409 g-0.4 |
| 소스(보정) | `data/staging/corrected/edits` (+graphs) | A·B·C 소스 · 현재 15 |
| 사전학습(AR) | `staging/tokens_rplan`, `tokens_rplan_rb256` | RPLAN 사전학습 토큰 |
| 사전학습(Diff) | `releases/parsed/global_rplan`, `global_cubicasa` | Diff 사전학습 코퍼스 |
| 검수용 | `staging/aihub/{graphs,manifest.jsonl}` | admin 검수(0.1) — 유지 검토 |

## 1. Track B (AR·토큰)
| 라인 | 상태 | 정본 모델 ckpt(+peak ep) | 학습 데이터 |
|---|---|---|---|
| **parsed** | ✅ **정본 확정** | **`ckpts/korplan_ar_rk_gated_seed42_roomperm_ep150.pt`** — strict clean **46%**(peak·n200·seed42) | FT: `tokens_korean_gated`(parsed AI-Hub gated, grid128) · 사전학습 base: `ckpts/korplan_ar_r_roomperm_seed42_ep80.pt`(`tokens_rplan`) |
| corrected | 예정 | — | tokens_korean_*(corrected, 미생성) |

## 2. Track A (Diff·그래프) — 예정
| 라인 | 상태 | 정본 모델 | 학습 데이터 |
|---|---|---|---|
| parsed | 예정 | — | `derived/parsed/geom` + RPLAN/CubiCasa |
| corrected | 예정 | — | `derived/corrected/geom` |

## 3. Track C (상업·신규) — 예정
| 라인 | 상태 | 정본 모델 | 학습 데이터 |
|---|---|---|---|
| corrected | 예정 | — | corrected (기법 미구체) |

## 4. 삭제 후보 (KEEP 미기록 = 삭제 대상 · 나중 일괄 · 정본 확정 후)
- 옛 Diff `gen-v0~v7*` (`models/`·`runs/`) — 신규 Track A로 대체 예정
- 옛 AR 실험·중간 에폭 (`ckpts/` 대부분) — 정본 외. 예: `rk_gated_seed42_roomperm`은 **ep150만 KEEP**, ep90~140·160~180 = 삭제후보. (`r_roomperm_seed42_ep80` base는 KEEP)
- 미사용 release: `parsed/{v0, v2, global_all}` · `corrected/{g0, g1, g_global}` (corrected 모델 0개)
- (단 `v0·v2`는 현재 `aihub/graphs` 소스 → admin 검수 유지 여부와 함께 결정)
- ⚠️ 모델 삭제는 단일 파일 확인

---
_갱신 규칙: 새 정본 모델이 나오면 해당 칸에 ckpt 경로+peak ep+데이터를 적는다. 세션은 이 문서를 먼저 읽는다._
