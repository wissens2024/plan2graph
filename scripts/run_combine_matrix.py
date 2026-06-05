"""데이터셋 조합 단일학습 매트릭스 v1~v7 × 5시드 — 전이학습 아님, **합쳐서 한 번 학습**.

개념: 데이터셋을 합쳐 하나를 학습 → 어떤 조합이 동결 균형 test에서 최고인지 비교(소버린 ablation).
각 (버전, 시드)마다: train_gen.train(combine) → eval_gen.evaluate_combine(동결 균형 test).
GPU1 사용. 백그라운드: nohup ... &  (115에서 다일 학습).

버전 정의(2026-06-05 확정, 사용자):
  v1 = AI-Hub 20,828
  v2 = AI-Hub 20,828 + CubiCasa 3,028
  v3 = AI-Hub 20,828 + RPLAN 80,371
  v4 = AI-Hub 20,828 + CubiCasa 3,028 + RPLAN 80,371
  v5 = RPLAN 80,371                  (글로벌만)
  v6 = CubiCasa 3,028                (글로벌만)
  v7 = RPLAN 80,371 + CubiCasa 3,028 (글로벌만)
  (v0 = AI-Hub 7,101 dual 클린 = 기준선, 기존 그대로)
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from plan2graph import train_combine  # noqa: E402

# (release, source) — meta.source로 필터해 합침. AI-Hub 20,828=v2의 aihub분, CubiCasa 3,028=v2의 cubicasa분.
SPEC = {
    "v1": [("v2", "aihub")],
    "v2": [("v2", "aihub"), ("v2", "cubicasa5k")],
    "v3": [("v2", "aihub"), ("global_rplan", "rplan")],
    "v4": [("v2", "aihub"), ("v2", "cubicasa5k"), ("global_rplan", "rplan")],
    "v5": [("global_rplan", "rplan")],
    "v6": [("v2", "cubicasa5k")],
    "v7": [("global_rplan", "rplan"), ("v2", "cubicasa5k")],
}
ORDER = ["v6", "v1", "v2", "v5", "v7", "v3", "v4"]   # 작은 것부터(빠른 피드백)
SEEDS = [42, 1, 2, 3, 4]


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    for v in ORDER:
        for s in SEEDS:
            print(f"[{time.strftime('%H:%M:%S')}] combine {v} seed={s}  {SPEC[v]}", flush=True)
            train_combine.train(SPEC[v], v, epochs=100, seed=s)
            train_combine.evaluate(v, SPEC[v], s)
    print(f"[{time.strftime('%H:%M:%S')}] ALL DONE (combine matrix v1~v7)", flush=True)


if __name__ == "__main__":
    main()
