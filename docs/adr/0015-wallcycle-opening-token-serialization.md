# ADR-0015: wall-cycle + opening 토큰 직렬화 스펙 (g-0.4 ↔ 토큰 코덱)

Status: Accepted
Date: 2026-06-15
Deciders: wissens2024, Claude

## Relates
- ADR-0012(생성 타깃 = corner+wall+room-cycle+role+opening)의 **follow-up① 구현·확정**. LITERATURE §5(토큰 직렬화 스펙) 닫음.
- ADR-0010(g-0.4 표현·경계 `wall|open|door` 태그)을 *생성 가능한 토큰 문법*으로 구체화.
- ADR-0013(다국가 조건 메타)을 META 토큰으로 표현.

## Context
ADR-0012가 생성 타깃을 wall-cycle로 정했으나 *어떻게 직렬화하나*는 미정이었다. g-0.4 그래프는 (a) 벽 끝점이 전역 junction으로 통합 안 됨, (b) 폴리곤이 jagged(벽두께 노이즈로 거실 100+ 꼭짓점), (c) 절대 픽셀 좌표 — 그대로 토큰화하면 시퀀스 폭발·학습 불가. **증거 기반으로**(추측 ADR 금지) 코덱을 먼저 구현(`wallcycle_codec.py`)하고 한국 g-0.4 **전수 40,495** 라운드트립으로 검증한 뒤 본 ADR로 확정한다.

## Decision

### 1. 표현 — 벽은 1급 토큰이 아니라 파생
- **corner 집합**: 모든 방 폴리곤 꼭짓점을 격자 양자화·dedup한 전역 junction.
- **room-cycle**: 방 = corner index 순환 + role(13+ 한국 역할).
- **opening 3종**: 문(`door`)·창(`window`) = corner-pair 엣지 + 위치비율(+방), **open**(문 없는 공유 경계, ADR-0010) = 방쌍 명시.
- **벽은 디코드 때 room-cycle 공유엣지로 유도**(2방 공유=interior/단독=exterior, 두께 200/120 결정론). 같은 corner를 공유하므로 **겹침이 구조적으로 불가능** — 이 핵심 가설이 전수에서 입증(med 겹침 0).

### 2. 정제 = 격자 양자화 + 폴리곤 직교 단순화
- **양자화**(bbox 정규화 → grid 격자): 어긋난 벽 끝점이 같은 셀로 모여 junction 자동 통합 + 좌표 이산화.
- **양자화 *전* simplify**(shapely, preserve_topology): jagged 노이즈 제거로 토큰 격감(max 4649→1726). `tol = simplify_frac · √(w·h)`.
- **권장 하이퍼파라미터: grid=128, simplify_frac=0.01, nbins=16, maxrooms=64.** vocab_size=249.

### 3. open 경계는 명시 토큰 (gap-closing union 폐기)
- 문 없는 `open` 인접을 corner 공유로 암묵 표현하면 손실됨(원본 폴리곤이 벽두께만큼 떨어져 양자화해도 공유 junction 안 생김).
- 시도한 **gap-closing union**(원본 `walls.interior` 신뢰해 두 방 경계 junction 병합)은 **작은 방을 27% 죽임**(병합이 cycle<3 붕괴) → **폐기**.
- 대신 ADR-0010 `boundary=open` 그대로 **open을 1급 경계 토큰(방쌍 ordinal)** 으로. union 없이 방 100% + 인접 1.0.

### 4. 토큰 문법(flat int 시퀀스)
```
[BOS] [META c h s] [SEC_CORNERS] (qx qy)*N [SEC_ROOMS] (role c* ROOM_END)*R
      [SEC_OPEN] (DOOR ca cb pos | WINDOW ca cb pos | OPEN oa ob)* [EOS]
```
어휘 구간(grid128): special 0–8, meta@9, coord@18(0..128), role@147, pos@168(0..16), room@185(ordinal 0..63). **constrained-decoding 마스크**(ADR-0012 §3 불변): room cycle 닫힘·door는 두 방 공유 corner-pair·window는 exterior 엣지·corner 참조는 기선언분만 — 디코딩에서 무효 토큰 차단(생성기에서 적용, follow-up).

## 검증 (한국 g-0.4 전수 40,495, grid128/simplify0.01/union off)
| 지표 | 결과 |
|---|---|
| 토큰 무손실(canon↔tokens) | 100% (40495/40495) |
| 방/문/창 수 보존 | 100% / 100% / 100% (방 40492/40495) |
| 인접 jaccard(원본 edges 대비) | mean 1.000, p10 1.0, 최악 0.814 |
| 면적 보존(\|ratio−1\|) | mean 0.000, p90 0.001 |
| 토큰 길이 | mean 426, p90 588, max 1726 (max_len 2048 커버) |
| 겹침 area frac | med 0.0006, **p90 0.11 / max 0.92(꼬리)** |

## Amendment (2026-06-15, ADR-0016) — META에 scope/units 추가
ADR-0016(생성 단위 2레벨)로 META 토큰에 `plan_scope`(unit|floor)·`units`(1..max_units=8) 추가. 토큰 문법 META = `[c h s scope units]`. **vocab_size 249→260**(grid128). 옛 meta(plan_scope 없음)는 기본 `unit/1`로 처리(하위호환). 검증: 토큰 무손실·방/인접/면적 보존 유지, floor/N 주입 라운드트립 OK.

## Considered Alternatives
1. **벽을 1급 토큰으로 명시 생성** — 기각: room-cycle 공유엣지로 결정론 유도 가능(토큰↓), 겹침0은 corner 공유가 보장. 벽 명시는 중복.
2. **gap-closing union(walls.interior 신뢰)** — 기각: 인접 +0.12 대가로 작은 방 27% 붕괴. open 명시 토큰이 무손실 대체.
3. **simplify 없이 양자화만** — 기각: jagged로 토큰 max 4649(학습 한계 초과). simplify가 방 보존하며 max 1726로.
4. **simplify를 방 죽인다고 기각**(앞선 판단) — 번복: 그건 union 켜진 오염 상태였음. union off에선 방 100% 유지.
5. **FML 어휘 그대로 차용** — 기각(LITERATURE): g-0.4 스키마를 토큰화(우리 13역할·open·다국가 META 포함). FML은 마크업·constrained-decoding *기법*만 참조.

## Consequences
- Positive: 생성 타깃 표현 확정·구현·전수 검증 완료. 겹침0·방/문/창/인접/면적 무손실 보장. 토큰 길이 학습 가능. cadrender 호환 디코드(`canon_to_graph`). 작업2(데이터셋)·작업3(생성기)의 입력 계약 고정.
- Negative: 겹침 꼬리(p90 0.11)는 코덱 아닌 **원본 데이터 폴리곤 겹침**(R2G 추출 오류) — ADR-0012 §5 위상오류 정제 과제. simplify가 면적 미세 손실(p90 0.001, 무시 가능).
- Follow-up: ① constrained-decoding 마스크를 생성기에 구현(ADR-0012 §3) ② 겹침 꼬리 도면 = 데이터 정제 큐(위상오류) ③ 작업2: 코덱으로 미니셋 4티어 토큰 데이터셋 ④ 작업3: 토큰 타깃 생성기 재작성.

## Assumptions
- `[검증]` grid128/simplify0.01에서 한국 APT g-0.4 전수가 무손실 직렬화됨(40,495).
- `[추론]` 같은 코덱이 RPLAN/CubiCasa(다국가)에도 적용 가능(META country로 분리) — 어댑터 후 측정.
- `[추론]` 토큰 max 1726이 생성 학습에 수용 가능(max_len 2048). 깨지면 simplify_frac 상향 재검토.
