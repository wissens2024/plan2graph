# ADR-0003: G-라인 단일 진실(single source) — staging/gline 하나로 통합, 구 자료(g0·g1·topo_human 분리) 폐기

Status: Accepted
Date: 2026-06-10
Deciders: wissens2024

## Context
G-라인 데이터가 **반복 작업 중 켜켜이 쌓여 3~4개로 산만하게 분리**됐고, 각 화면·학습이 서로 다른 걸 읽어 혼선을 낳았다. 사용자 지적: "왜 산만하게 나뉘어 있나, 이게 나중에 쓰레기로 인식돼 오작동·실수의 원인이 된다."

분리된 실태:
- `releases/gline/g0` — **옛 지름길 자동빌드**(init_state, 스키마 미달 — ADR-0002가 "미달"이라 한 것). **구 자료.**
- `releases/gline/g1` — 옛 g0 위에 corrected 덮어쓴 보정본. g0 파생이라 동반 폐기. **구 자료.**
- `staging/topo_human` — 사람 SVG 편집 폴더(현재 내용은 e2e 테스트 7줄·SVG 3개, 실작업 없음).
- `staging/gline` — **신규 자동 베이스라인**(schema g-0.3, 위상=T-라인 topology 재사용, 8700세대, 보정 회계). 위 g0을 사실상 대체.

`🧩 AI-Hub 검수 (G)` 화면은 `topoedit.GRAPHS_DIR`(=topo_human, 사람=0)와 `g0 manifest`만 읽어 **미검수만 최대**로 보이고, 진짜 자동 진행도(gline 사용 37%)는 안 보였다. = [[no-separate-goldset-one-dataset]]·[[staging-is-current]]·[[topoedit-human-builder]]("구release=시소 쓰레기 재사용 금지")가 이미 경고한 패턴.

## Decision
**G-라인 데이터는 `staging/gline` 하나가 단일 진실이다. 다른 G 데이터 폴더를 만들지 않는다.**

1. **자동 베이스라인 = `staging/gline/graphs`** (corrected=false). `scripts/build_gline_auto.py`가 생성.
2. **사람 보정완료 = 같은 폴더의 corrected=true** — 별도 폴더(topo_human) 두지 않는다. GUI 저장 경로(`topoedit.OUT_DIR`)를 `staging/gline`으로 통합 → SVG·ledger·graphs가 한곳. 한 unit의 자동 레코드를 사람이 보정완료하면 그 자리서 corrected=true로 갱신(증량).
3. **회계 = 보정 2축**([[gline-correction-not-verification]]): 자동 분류(사용/보정필요/제외) + 사람(보정완료). 화면은 gline의 분류를 읽는다. corrected=true만 "보정완료(사용 확정)"로 센다(자동 corrected=false를 검증완료로 오인 금지).
4. **학습 입력 = staging/gline을 freeze한 release**(staging→release recipe). 옛 g0/g1 release는 삭제.
5. **구 자료 삭제**: `releases/gline/{g0,g1}` 삭제 완료(2026-06-10, 각 219M). `g_global`(기하 사전학습 CubiCasa/RPLAN)은 *자동빌드가 아니라 별 용도*라 보존(별도 판단).

### 실행 항목(소유·진행)
| # | 항목 | 파일 | 소유 | 상태 |
|---|---|---|---|---|
| 1 | g0·g1 삭제 | (data) | 클로드 | ✅완료 |
| 2 | 본 ADR | docs | 클로드 | ✅완료 |
| 3 | GUI 저장경로 통합 `OUT_DIR→staging/gline` | `topoedit.py` | **오른쪽(위상편집)** | 미완(합의) |
| 4 | 🧩 화면 = gline 분류 읽기(corrected만 사용) | `admin.py` | **오른쪽** | 미완(합의) |
| 5 | train_geom 입력 = frozen gline | `train_geom.py` | 클로드 | gline freeze 후 |
| 6 | topo_human 잔여(테스트) 정리 | (data) | 클로드 | OUT_DIR 통합 시 |

③④는 GUI(눈 필요)라 위상편집 세션 소관 — 클로드는 스펙만 제공(blind 편집 금지).

## Considered Alternatives
1. **topo_human(사람)·gline(자동) 폴더 분리 유지** — 기각: 분리 이유가 없다(historical accretion). 한 데이터셋의 corrected 플래그면 충분. 분리는 화면·학습 혼선·쓰레기화의 원인.
2. **자동 그래프를 topo_human/graphs에 직접 써서 화면이 보게** — 기각: 그 폴더는 "검증완료(사람)"로 집계돼 자동을 사람검증으로 오인. 의미 붕괴.
3. **g_global도 삭제** — 보류: 기하 사전학습(별 용도)이라 G 자동빌드 구 자료와 다름. 별도 판단.

## Consequences
- Positive: G 데이터 단일 진실 → 화면·학습이 한 곳을 읽어 혼선·쓰레기화 제거. 진짜 진행도(자동 사용/보정필요/제외 + 사람 보정완료)가 한 화면에. 미래 세션이 죽은 g0을 현재로 오인하는 실수 차단.
- Negative: GUI 저장경로·화면 로직 변경(③④) 1회 필요(오른쪽 합의). 기존 topo_human 테스트 데이터 폐기.

관련: [[ADR-0002]](tline-gline 분리) · [[gline-correction-not-verification]] · [[staging-is-current]] · [[no-separate-goldset-one-dataset]] · [ARCHITECTURE.md](../ARCHITECTURE.md)(G-라인 스키마)
