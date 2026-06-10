# V2V(Vision-to-Vector) 성능 원장

> **2026-06-05 서버(115) 실측.** 단일라벨 도면(SPA만/STR만)에 빠진 종류를 YOLOv8-seg로 예측 → 위상 게이트 통과분만 그래프로 채택(`use`).
> 목적: 모델·추론 설정을 바꿀 때마다 **fix→use 전환수**를 기록해, 어느 레버가 실제로 `use`를 늘리는지로 의사결정한다.
> 관련: [DATA.md](DATA.md)(처분 회계) · [ARCHITECTURE.md](ARCHITECTURE.md)§3(검출 요약) · [OPERATIONS.md](OPERATIONS.md)(V2V 튜닝) · [ROADMAP.md](ROADMAP.md)

---

## 0. 제1원칙 — mAP가 아니라 게이트 통과율로 최적화하라

V2V의 목표지표는 mAP가 아니라 **fix→use 전환수(=빠진 라벨을 채워 위상 게이트를 통과한 도면 수)** 다. 둘은 정렬돼 있지 않다.

**증거(2026-06-05).** STR 모델을 mask mAP50 0.565 → 0.68로 올린 모델로 **교체**했더니 spa_only 방향 `use`가 **오히려 −122** 였다. mAP는 "평균 마스크 겹침"을 재지만, 게이트는 "현관문 존재·필수실 연결" 같은 위상 충족만 본다. box mAP 0.90인데 mask mAP 0.68인 격차가 그 원인 — 검출은 되나 마스크 외곽이 흔들려 일부 도면은 좋아지고 일부는 망가진다.

→ **새 모델은 "교체"가 아니라 "추가 후보"로 쓴다(유니온).** 둘 다 추론해 하나라도 게이트를 통과하면 채택하면, 기존 통과분을 절대 잃지 않아 회귀가 구조적으로 0이 된다.

---

## 1. 모델 원장

| 모델 | weights | imgsz | epochs | box mAP50 | mask mAP50 | mask mAP50-95 | 비고 |
|---|---|---|---|---|---|---|---|
| SPA | `runs/segment/v2v_runs/spa_e5` | 768 | 5 | 0.92 | **0.90** | 0.59 | 미수렴(5ep), 추론 default 1024와 해상도 불일치 |
| SPA | `spa_yolov8n-seg_768_e100` | 768 | ≤100(patience30) | — | — | — | 🔄 학습중(2026-06-05) — str_only_pending 겨냥 |
| STR | `str_e50` | 768 | 50 | 0.86 | 0.565 | 0.23 | 현 predicted/ 생성에 쓰인 모델 |
| STR | `str_yolov8n-seg_1024_e50` | 1024 | 50 | **0.90** | **0.68** | 0.30 | mAP는 최고지만 교체 시 게이트 −122(§0) |

**학습곡선 교훈(results.csv 실측):**
- STR 1024n: mask mAP50이 ep39에 0.68로 **평탄**(val loss도 바닥). → 에폭은 병목 아님. 레버는 **모델 용량(n→m/l)·해상도·데이터**.
- SPA 768: ep5에 mask mAP50 0.90이고 **아직 상승 중**(val loss 하강). → **undertrained, 더 학습하면 개선**.

---

## 2. 실험 원장 — fix→use 측정

격리 A/B: `predicted/`를 백업 후, 변환은 `build_dataset.py --predicted --out /tmp/...`로 임시 출력. releases/v2·manifest 무손상. spa_only 방향만 STR 모델 영향(SPA 예측 불변이면 str_only 방향은 동일).

**EXP-1 (2026-06-05) — STR 모델 교체 vs 유니온** (split=Training, spa_only 지문 4,919)

| STR 운용 | spa_only `use` | vs 구모델 |
|---|---|---|
| 구 STR(str_e50, mask 0.565) | 4,425 | 기준 |
| 신 STR(str_1024, mask 0.68) **교체** | 4,303 | **−122** ❌ |
| **두 모델 유니온** | **4,527** | **+102, 회귀 0** ✅ |

- 구만 통과 224 · 신만 통과 102 · 둘다 4,201 · **어느쪽도 실패(진짜 잔여) 392**
- 게이트 격리 사유: `no_unit_with_entrance`(현관문 못 찾음), `missing_essential`(필수실) — **문 검출이 병목**임을 시사.

> 참고: manifest 정식 회계(crc8 dedup)의 spa_only_pending는 995건. 위 build_predicted(Training split) 수치는 재도출이라 절대값이 다르지만, A/B 비교는 자체 정합.

**EXP-2 (2026-06-05) — SPA 수렴학습(5ep→100ep)** (split=Training, str_only 지문 4,290)

| SPA 모델 | str_only `use` | vs 구모델 |
|---|---|---|
| 구 SPA(spa_e5, 5ep, mask 0.902) | 3,631 | 기준 |
| 신 SPA(768_e100, ep39 best, mask **0.964**) **교체** | 3,799 | **+168** ✅ |
| **두 모델 유니온** | **3,904** | **+273** (구만 105 + 신만 273) |

- 잔여(어느쪽도 실패) 386. SPA 학습곡선: 5ep 0.902 → ep39 0.964(+0.062), patience30 수렴 후 조기종료.
- **STR(EXP-1)과 정반대**: STR은 고mAP 모델 교체가 −122(게이트 비정렬)였으나, **SPA는 undertrained 해소라 교체로도 순증(+168)**. "에폭을 늘리면 좋아지나?"의 답이 모델별로 갈림 — SPA는 Yes, STR은 No(용량·해상도가 레버).
- **두 방향 유니온 합산 잠재이득**: spa_only +102(EXP-1) + str_only +273(EXP-2) = **+375 fix→use**.

---

## 3. 최적화 레버 (우선순위)

1. **모델 유니온** — 신·구 STR(및 향후 m/l) 모두 추론, 하나라도 게이트 통과 시 채택. 회귀 0, EXP-1 기준 +102. **1순위.**
2. **문(door) recall** — 게이트 실패가 현관문 누락. 문 클래스 conf threshold↓(0.25→0.1), 클래스별 val(door/window/wall)로 병목 확정.
3. **STR 용량↑(n→m-seg)** — nano 0.68 용량포화 돌파. 단 mAP가 아니라 유니온 후보로 추가해 게이트로 검증.
4. **해상도↑** — 768→1024가 0.565→0.68을 만듦. 원본이 1024 초과면 재export 후 1280 시도.
5. **SPA 수렴학습** — 5ep→100ep(patience30). str_only_pending 1,051 겨냥. 🔄 진행중.

**범위 밖(이 루프로 안 줄어듦):** `convert_failed`(dual 도면 — V2V가 skip, 재병합/복구 별도 경로 필요), `excl`(사본·비FP·OBJ/OCR — 설계상 고정), 원본정보 미완성분(영구 floor).

---

## 4. 재현 커맨드 (서버 115, env p2g)

```bash
PY=/home/ju/.local/share/mamba/envs/p2g/bin/python
# 학습 (CUDA_VISIBLE_DEVICES=1 = 프로젝트 지정 학습 GPU)
CUDA_VISIBLE_DEVICES=1 $PY scripts/yolo_train.py spa 100 768 16   # <spa|str> ep imgsz batch

# 추론 (단일라벨 도면에 빠진 종류 예측 → data/v2v/predicted/)
CUDA_VISIBLE_DEVICES=1 $PY src/plan2graph/v2v_infer.py run \
  --spa-weights runs/segment/v2v_runs/spa_e5/weights/best.pt \
  --str-weights runs/segment/v2v_runs/str_yolov8n-seg_1024_e50/weights/best.pt \
  --only SPA --split Training --device 0      # --only SPA: SPA보유→STR예측 방향만

# 변환·게이트 (predicted + 실라벨 → 위상 → 채택그래프)
$PY src/plan2graph/build_dataset.py --predicted --split Training --jobs 8 --out /tmp/exp

# fix→use 집계: /tmp/exp/accepted.csv 의 sheet_id(=house_FP_<crc8>_<size>) 지문집합을
# baseline과 diff. spa_only/str_only 방향은 unpack.fingerprint_label_map로 분리.
```

**주의:** `predicted/`는 추론이 덮어쓴다 → A/B 전 `cp -r data/v2v/predicted data/v2v/predicted.baseline`. `data/v2v/coco_*/data.yaml`의 `path:`는 Windows 경로라 서버 학습 시 서버 절대경로로 교정 필요.
