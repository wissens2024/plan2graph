"""모델 용량 ablation — 같은 v0 데이터(AI-Hub 클린)에 **2배 모델** 학습 → 작은 v0와 비교.

작은(기본): embedding 48 · layers 2 · heads 4 · FFN 96   → 기존 v0 (gen-v0-neural-...)
큰(2배):   embedding 96 · layers 4 · heads 8 · FFN 192  → label=v0cap2x
파라미터를 키우면 동결 균형 test 성능이 나아지는가? (소량 데이터라 통상 무익~과적합 예상, 결과로 확인)
사용: CUDA_VISIBLE_DEVICES=1 PYTHONPATH=src nohup python scripts/run_capacity.py > logs/capacity.log 2>&1 &
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)
from plan2graph import train_combine  # noqa: E402

SPEC = [("v0", "aihub")]   # v0와 동일 데이터(AI-Hub 클린) — 모델 크기만 다르게
BIG = {"emb": 96, "hid": 192, "layers": 4, "heads": 8}
SEEDS = [42, 1, 2, 3, 4]


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    for s in SEEDS:
        print(f"[{time.strftime('%H:%M:%S')}] v0cap2x (2x model) seed={s} {BIG}", flush=True)
        train_combine.train(SPEC, "v0cap2x", epochs=100, seed=s, **BIG)
        train_combine.evaluate("v0cap2x", SPEC, s)
    print(f"[{time.strftime('%H:%M:%S')}] ALL DONE (capacity v0cap2x)", flush=True)


if __name__ == "__main__":
    main()
