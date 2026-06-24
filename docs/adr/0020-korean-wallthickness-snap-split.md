# ADR-0020: 한국 벽두께 표현 정규화 — snap_split (문·창·벽두께 처리)

Status: Proposed (A/B 비교로 확정 예정 — Ablation B)
Date: 2026-06-24
Deciders: wissens2024, Claude

## Relates
- **ADR-0015 §3**(gap-closing union 폐기)을 *다른 메커니즘으로 revisit*. 당시 union은 작은 방 27% 붕괴로 폐기됐으나, 본 ADR의 축좌표 스냅+edge-split는 **1.5% 손실**로 같은 목표(벽두께 간격 닫기)를 달성.
- ADR-0010(g-0.4 경계 `wall|open|door`)·ADR-0012(생성 타깃 wall-cycle)·ADR-0006(소버린 엔진).
- 메모리: korean-codec-rplan-optimized-wallthickness.

## Context — 무엇이 문제였나 (증거 기반)
KorPlan-AR이 RPLAN에선 clean ~34%인데 한국(AI-Hub)에선 clean 0%·footprint 파편화. 진단(2026-06-24):

- **코덱이 RPLAN식(벽두께 0·인접 방이 코너 공유)에 최적화돼 있었다.** canon_to_graph는 내부벽을 *두 방이 공유하는 cycle 엣지*에서 유도한다.
- **한국 도면은 인접 방이 벽두께만큼 떨어져 코너를 공유하지 않는다**(실측: 인접 코너쌍 d=1~3 grid에 집중, 도면당 ~26쌍). → 공유엣지 0 → **15방 아파트인데 내부벽 0~2개**(실측 평균 0.8), 나머지 전부 exterior.
- 결과: ① 방들이 *독립 박스*로 렌더(공유벽 없음) ② **문은 두 방 공유벽에 붙는데 내부벽이 없어 부착 실패**(한국 ~9문/도면) ③ 생성기는 RPLAN식 공유-코너 앵커 없이 15개 박스를 sub-px 간격으로 배치 → 파편화.
- ∴ 한국 도면 병목 = **표현(코덱)** 이지 모델·용량·파라미터 아님. (max_corners=256·max_len=1152 한계엔 안 걸림: 한국 max 138코너·701토큰.)

ADR-0015 §3은 이 "벽두께로 코너 공유 안 됨"을 이미 인지하고 **gap-closing union**(원본 walls.interior로 경계 junction 병합)을 시도했으나 **작은 방 27% 붕괴**로 폐기, 대신 방 분리 유지 + `open` 토큰을 택했다. 그러나 그 선택은 *내부벽·문 부착 구조*를 포기한 것이라 생성 도면 품질의 근본 한계로 남았다.

## Decision — snap_split (문·창·벽두께를 이렇게 처리한다)
토큰화 시 `canonicalize → **snap_split** → encode` 로 한국 벽두께 표현을 RPLAN식 공유벽 구조로 정규화. (`wallcycle_codec.snap_split`, `build_token_dataset.py --snap`. RPLAN엔 미적용.)

### ① 벽두께 — 축좌표 1D 클러스터 스냅으로 간격을 "닫는다"
- 한국 평면도는 축정렬(rectilinear) → 평행·근접 벽은 *한 축 좌표만 벽두께만큼 다름*. **X좌표끼리·Y좌표끼리 1D 클러스터링(tol 2.5 grid)** 해 같은 선으로 스냅 → 인접 방이 벽선을 공유.
- ADR-0015 union(주석 기반 junction 병합)과 다른 메커니즘 → **방 손실 1.5%**(n=500, vs union 27%). 18배 개선.
- ★**벽두께는 생성에선 0으로 정규화**(모델은 RPLAN식 쉬운 공유-코너 배치만 학습). **그리는 두께는 렌더에서 재부여**(내벽 120·외벽 200mm 결정론, canon_to_graph). 원본 per-wall 두께는 현재 미보존(필요 시 스냅 전 측정값 저장으로 확장 — follow-up).

### ② 내부벽 — edge-split(T자 접합)으로 정식 벽 그래프 복원
- 좌표 스냅만으론 부족(끝점 정렬된 벽만 공유). **벽 엣지 위에 놓인 (끝점 아닌) 코너를 삽입해 엣지 분할** → 한 방 벽 중간에 닿는 이웃 방 벽(T자)도 동일 sub-edge 공유 → canon_to_graph가 내부벽 정식 유도.
- 실측: 내부벽 **0.8 → 27.8/도면**(15방 아파트의 현실적 공유벽 수).

### ③ 문·창 — split sub-edge에 위치 보존 재매칭
- opening(문/창)은 corner-pair 엣지 + 위치비율(pos/16)로 인코딩. 스냅·split로 코너가 리맵·분할되므로, **문 점(pa+pos·(pb−pa))을 포함하는 sub-edge를 찾아 재부착**하고 pos 재계산.
- 실측(라운드트립): **문 부착 100%**(9/9~13/13). 내부벽이 생겨 방-방 문이 공유벽에 정상 부착.

## 검증 (재학습 0 — 표현 레벨, 라운드트립 decode→snap_split→encode→decode)
| 지표 | 원본(비스냅) | snap_split |
|---|---|---|
| 내부벽/도면 | 0.8 | **27.8** |
| 문 부착률 | 사실상 0(내부벽 부재) | **100%** |
| 방 손실(n=500) | — | **1.5%** (ADR-0015 union 27% 대비) |
| 토큰 길이 | ~402 | ~391 (유사, <<1152) |
| 렌더 | 떠다니는 독립 박스 | **공유벽·문·창 갖춘 완성 아파트 평면** |

## Open — A/B로 확정 (Status=Proposed인 이유)
표현 레벨은 검증됐으나 **생성 레벨은 미검증**: 스냅 토큰 학습 모델이 비스냅보다 잘 생성하는가? → **Ablation B**(스냅 有/無 두 토큰화로 각각 학습→동일 프로토콜 생성 비교)로 확정. 기대: 한국 clean 0% → RPLAN급(~34%) 근접.
- 부수 실험 **Ablation A**(문/창 有/無): 개구부가 배치를 방해하는지 격리(2차).
- 확정되면 Status → Accepted, ADR-0015 §3(open-only) 부분 supersede.

## 구현
- `src/plan2graph/wallcycle_codec.py`: `snap_split(canon, tol=2.5)`(_cluster_1d·_on_seg·edge-split·opening 재매칭). 커밋 891f3e255.
- `scripts/build_token_dataset.py --snap [--snap-tol 2.5]`: 한국 빌드 시 적용.
- 한국 소스: `data/staging/corrected/graphs`(39,117 APT). 비스냅=기존 `tokens_korean_clean`, 스냅=재토큰화 예정.
