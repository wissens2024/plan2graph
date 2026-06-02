# RUNBOOK — 115 서버에서 전체 파이프라인 실행

> 개발=노트북(git push) → 실행=115 서버(2×RTX3080, git pull). 결과=서버 콘솔을 브라우저로.
> ✅=구현됨(지금 실행 가능) / 🔧=코드 작성 필요(명령 형태만 제시).

## 0. 서버 셋업 (1회)
```bash
git clone <repo>  plan2graph && cd plan2graph        # 또는 노트북에서 push한 remote pull
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-server.txt
#   torch는 서버 CUDA에 맞춰: pip install torch torchvision --index-url .../cu121

# 데이터 경로(환경변수) — 코드 동일, 경로만 서버용
export PLAN2GRAPH_RAW=/data/aihub/01-1.정식개방데이터     # AI-Hub 원본 ZIP 위치
export PLAN2GRAPH_DATA=/data/plan2graph/data              # 산출물 위치
export LAW_API_OC=juchul_law_engine
nvidia-smi   # GPU 2개 확인
```
AI-Hub ZIP(38GB)·글로벌셋을 서버에 전송해 `PLAN2GRAPH_RAW`에 둔다.

## 1. P1 — 건축지식 구조화 (CPU)
```bash
✅ python src/plan2graph/unpack.py inventory          # 인벤토리
✅ python src/plan2graph/unpack.py link --split Training     # SPA+STR 지문 연결
✅ python src/plan2graph/unpack.py link --split Validation
✅ python src/plan2graph/legal_harvest.py             # 법령 수집(API) → legal/catalog.json
✅ python src/plan2graph/ontology.py                  # OWL 스키마
🔧 면적·비율 법규(채광1/10·침실최소·피난거리) 추가 — rules_legal 확장
```

## 2. P2 — 한국형 데이터셋 (CPU)
### v0 (라벨 clean) ✅
```bash
✅ python src/plan2graph/build_dataset.py --split all --jobs 8   # 통합 빌드
✅ python src/plan2graph/scale_ocr.py pass 8 && python src/plan2graph/scale_ocr.py apply
✅ python -c "from plan2graph.rules_legal import annotate_all; annotate_all()"
✅ python src/plan2graph/release.py v0                # v0 동결(test 고정)
```
### 정확도 게이트 ✅
```bash
✅ python src/plan2graph/gate.py build --n 20         # 검증 오버레이+채점표
#   사람이 notebooks/gate/score_template.csv 채점 → 
✅ python src/plan2graph/gate.py score
```
### v1 (보정) — 콘솔 ✅ / 일괄 🔧
```bash
✅ streamlit run admin.py --server.address 0.0.0.0    # 격리 교정·scale 보정·검수
🔧 일괄교정(bulk) CLI: 사유별 알고리즘 일괄 적용 → 재채택
✅ python src/plan2graph/release.py v1                # v1 동결(test 공유)
```
### v2 (V2V로 41,556 확장) — 🔧 GPU
```bash
🔧 python src/plan2graph/v2v_export.py        # 라벨→COCO 학습셋(train/val) export
🔧 yolo segment train data=floorplan.yaml model=yolov8m-seg.pt epochs=100 device=0,1   # 2-GPU
🔧 python src/plan2graph/v2v_infer.py         # 무라벨 도면에 빠진 라벨 예측(COCO)
🔧 python src/plan2graph/build_dataset.py --split predicted --jobs 8   # 예측라벨→위상→채택(품질게이트)
🔧 python src/plan2graph/release.py v2
   # 검증: held-out 라벨로 mask AP, 신뢰도 임계
```

## 3. P2 글로벌 사전학습셋 — 🔧
```bash
🔧 python src/plan2graph/adapters/rplan.py      # RPLAN → 공통 스키마
🔧 python src/plan2graph/adapters/cubicasa.py   # CubiCasa5k(SVG) → 공통 스키마
🔧 python src/plan2graph/release.py global      # 글로벌 동결(출처 태그)
```

## 4. P3 — 생성 AI (GPU)
```bash
✅ python src/plan2graph/model_baseline.py v0     # 통계 baseline + 평가(A/B 기준)
🔧 python src/plan2graph/text2graph.py            # 자연어→제약그래프
🔧 python src/plan2graph/train_gen.py --pretrain global --finetune v2   # 신경망(GNN/diffusion)
🔧 python src/plan2graph/gen_loop.py --reg-correct # Neuro-Symbolic: 생성→rules_swrl검증→교정(Self-Correction)
🔧 python src/plan2graph/eval_gen.py --versions v0,v1,v2  # A/B 종합
```

## 5. 결과 보기 (서버 호스팅)
```bash
✅ streamlit run admin.py --server.address 0.0.0.0 --server.port 8501 --server.headless true
#   노트북 브라우저: http://<115서버IP>:8501  또는  ssh -L 8501:localhost:8501 user@115
#   📊 결과 대시보드 = 데이터셋·모델·법규·A/B 한 화면 (캡처→PPT)
```

## 구현 인벤토리 (현재) — 전 모듈 코드 완결
| 모듈 | 코드 | 비고 |
|---|---|---|
| P1: unpack·coco·geometry·topology·rules·rules_legal·rules_swrl·ontology·law_api·legal_harvest | ✅ | 면적·비율 법규는 확장 여지 |
| P2: build_dataset(+predicted)·scale_ocr·split·release·gate·v2v_export·v2v_infer·adapters(rplan·cubicasa) | ✅ | V2V·글로벌은 서버 실데이터·GPU 필요 |
| P3: text2graph·model_baseline·train_gen·gen_loop·eval_gen | ✅ | train_gen 학습은 서버 GPU |
| 콘솔: admin(격리·채택·scale·법령DB·대시보드)·review·visualize | ✅ | |
| 남은 실행: V2V 학습·글로벌 실데이터·신경망 학습·v1 일괄교정 | ⏳ | 코드는 있음, 서버서 데이터/GPU로 실행 |

> 순서(합의): **1(게이트) → 3(v2, 서버) ∥ 2(v1, 노트북 병행) → 글로벌 → P3 생성·규제루프 → A/B**
> 코드는 전부 노트북서 작성·검증(합성/baseline) 완료. 이제 서버서 실데이터·GPU로 채우면 됨.
