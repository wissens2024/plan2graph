# 운영 — 115 서버 배포·실행·디버그 (OPERATIONS)

> DEPLOY + RUNBOOK 통합본.

---

# DEPLOY — 115 서버 배포·실행·디버그 운영 계획

> 로컬(노트북)=개발·세션제어, 115 서버(2×RTX3080)=실행. 코드는 GitHub 경유.
> 핵심 과제: **로그 회신 → 로컬 수정 → 재배포 반복을 최소화**(커밋·푸쉬 churn 줄이기).
> repo: https://github.com/wissens2024/plan2graph.git

---

## 1. 서버 1회 셋업
```bash
git clone https://github.com/wissens2024/plan2graph.git && cd plan2graph
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-server.txt
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121   # 서버 CUDA에 맞게
# 데이터·OC 환경변수(.bashrc 또는 .env)
export PLAN2GRAPH_RAW=/data/aihub/01-1.정식개방데이터
export PLAN2GRAPH_DATA=/data/plan2graph/data
export LAW_API_OC=juchul_law_engine
python doctor.py        # ★ preflight: deps·torch·CUDA·GPU·Java·데이터경로 한 번에 점검
```
→ `doctor.py`가 ✅/❌로 누락을 먼저 잡는다(실행 중 발견 방지).

## 2. 반복 워크플로 — 커밋·푸쉬 churn 최소화 (핵심)

### 원칙: "디버그는 rsync, 커밋은 안정될 때만"
실행 중 오류가 나면 매번 commit→push→pull 하지 말 것. 대신:

**(A) 디버그 중 = rsync 직결 동기화 (커밋 0)**
```bash
# 로컬에서 소스만 즉시 서버로(커밋 없이). 데이터/모델 제외.
rsync -az --delete --exclude data/ --exclude models/ --exclude .git/ \
  ./ user@115:/path/plan2graph/
```
→ 로컬에서 고치고 rsync 한 줄 → 서버서 즉시 재실행. **여러 번 고쳐도 커밋 0.**
대안: VS Code Remote-SSH 또는 sshfs 마운트(서버 코드를 로컬 편집기로 직접).

**(B) 안정되면 = 1회 커밋**
검증된 묶음만 `git commit`(여러 디버그 수정을 하나로). main 히스토리 깨끗.
```bash
git push origin main        # 서버는 git pull (또는 계속 rsync)
```

**(C) 브랜치 전략**
- `main` = 안정. `dev` = 디버그(force-push 허용). dev에서 빠르게 돌리고 검증되면 main에 **squash-merge** → 잔 커밋이 main에 안 남음.

### 원칙: "튜닝은 env, 코드수정 아님" (커밋 0)
임계값은 `P2G_*` 환경변수로 오버라이드(config._envf). **튜닝에 커밋·재배포 불필요:**
```bash
export P2G_OPEN_MIN_RATIO=0.40       # 개방통로 과연결 줄이기
export P2G_MIN_ETC_AREA_PX=15000     # 기타 노이즈 더 제거
export P2G_LEGAL_BEDROOM_MIN_M2=5.0  # 침실 최소면적 조정
python src/plan2graph/build_dataset.py --split all --jobs 8   # 재빌드만
```
V2V/생성 튜닝(conf·imgsz·model·epochs·thresh)은 전부 **CLI 인자** → 커밋 0.

## 3. 에러 대응 — 진단을 코드에 내장 (왕복 최소화)
- **단계적 검증**: 모든 무거운 작업에 `--limit`/`--self-test`. 풀런 전 소량으로 먼저(서버서 즉시 실패 발견).
- **graceful 실패**: 항목별 try/except → 한 도면 실패가 전체를 안 죽임. 실패는 `quarantine.csv`/`failures`에 사유 기록 → 로그만 봐도 원인 파악(코드수정 불요한 경우 多).
- **구조화 로그**: 긴 작업은 `nohup ... > logs/<job>.log 2>&1 &`. 끝에 요약(처리/채택/격리/오류 사유별). 이 로그 한 파일만 회신하면 진단 가능.
- **run manifest**: release/eval은 manifest.json(파라미터·counts·날짜) 남김 → 무엇으로 돌렸는지 재현·비교.

## 4. Mask R-CNN(V2V) 오류·튜닝 대응 — 가장 리스크 큰 단계
**단계적 게이트(각 단계서 멈춰 확인 → 큰 실패 조기 차단):**
```bash
# 1) 학습셋 export 소량 + 좌표정렬 육안확인(노트북서 이미 검증한 방식)
python src/plan2graph/v2v_export.py --label SPA --limit 50
#    → artifacts 검증 렌더로 폴리곤 정렬 확인. RPLAN/STR 채널·클래스 점검.
# 2) 짧은 학습(파이프라인 확인): epochs 5, 작은 모델
yolo segment train data=.../data.yaml model=yolov8n-seg.pt epochs=5 imgsz=1024 device=0
# 3) 검증: held-out 라벨로 mask AP. AP 낮으면 튜닝(아래)
# 4) 풀 학습: yolov8m/l, epochs 100, device=0,1
# 5) 추론 소량 → 예측 COCO 육안/품질게이트 확인 → 풀 추론
python src/plan2graph/v2v_infer.py run --spa-weights ... --str-weights ... --limit 50
```
**예상 실패 모드 → 대응(대부분 커밋 불요):**
| 증상 | 원인 | 대응 |
|---|---|---|
| import/CUDA 오류 | torch-CUDA 불일치 | doctor.py + 올바른 cu1xx 휠 |
| OOM | imgsz·batch 큼 | `imgsz` 낮춤·batch 인자(CLI) |
| mask AP 낮음 | 데이터·에폭·모델크기 | epochs↑·yolov8l·augment(CLI) |
| 폴리곤 어긋남 | 좌표 역변환 | self-test 통과(0px). RPLAN 채널 index 점검(rplan.py 상단 상수) |
| RPLAN 파싱 0건 | 채널 레이아웃 상이 | rplan.CAT_CH/INST_CH/DOOR_CATS 조정(소스 1줄) |
| 예측 노이즈 과다 | conf 낮음 | `--conf 0.4`(CLI)·품질게이트가 자동 격리 |
- 예측 라벨은 무결성·규제 게이트가 자동 거름 → 노이즈 대응은 주로 **conf 인자(커밋0)**.

## 5. 결과 확인 (서버 호스팅)
```bash
streamlit run admin.py --server.address 0.0.0.0 --server.port 8501 --server.headless true
# 노트북: ssh -L 8501:localhost:8501 user@115  → http://localhost:8501
```
데이터·모델·지표·대시보드 전부 서버에 있으므로 **서버 결과를 브라우저로** 본다.

## 6. 실행 순서(서버) — RUNBOOK 참조
preflight → (P1 인벤토리·링크·법령) → v0 빌드·동결 → 게이트 → [v2: export→학습→추론→예측통합→v2 동결] ∥ [글로벌 어댑터] → 신경망 학습 → A/B(eval_gen) → 대시보드.

## 7. 요약 — churn 줄이는 4중 장치
1. **rsync 직결**(디버그 중 커밋 0) → 안정 시 1회 커밋(squash)
2. **P2G_* env 튜닝**(임계 조정에 커밋·재배포 불요)
3. **doctor.py preflight**(환경오류 사전 차단 → 실행중 발견 왕복 제거)
4. **단계적 검증(--limit/--self-test) + graceful 실패 + 구조화 로그**(로그 1파일로 진단)


---

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
#   사람이 artifacts/gate/score_template.csv 채점 → 
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
