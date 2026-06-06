"""모델 용량 ablation 확장 — v1·v4·v7에 2배 모델(작은 모델 대비). v0cap2x(§9)에서 용량↑ 이득 확인 후 확장.

2배: embedding 96 · layers 4 · heads 8 · FFN 192 (작은: 48·2·4·96).
v1=AI-Hub 20,828(더 큰 한국) · v4=AI-Hub+CubiCasa+RPLAN(전부) · v7=RPLAN+CubiCasa(글로벌만).
→ 더 큰/다양한 데이터에서도 용량이 도움되는지(용량-데이터 매칭) 확인.
사용: CUDA_VISIBLE_DEVICES=1 PYTHONPATH=src nohup python scripts/run_capacity_more.py > logs/capacity_more.log 2>&1 &
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)
from plan2graph import train_combine  # noqa: E402

SPEC = {
    "v1cap2x": [("v2", "aihub")],
    "v4cap2x": [("v2", "aihub"), ("v2", "cubicasa5k"), ("global_rplan", "rplan")],
    "v7cap2x": [("global_rplan", "rplan"), ("v2", "cubicasa5k")],
}
ORDER = ["v1cap2x", "v7cap2x", "v4cap2x"]   # 작은 것부터(빠른 피드백)
BIG = {"emb": 96, "hid": 192, "layers": 4, "heads": 8}
SEEDS = [42, 1, 2, 3, 4]


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    for v in ORDER:
        for s in SEEDS:
            print(f"[{time.strftime('%H:%M:%S')}] {v} (2x) seed={s} {SPEC[v]}", flush=True)
            train_combine.train(SPEC[v], v, epochs=100, seed=s, **BIG)
            train_combine.evaluate(v, SPEC[v], s)
    print(f"[{time.strftime('%H:%M:%S')}] ALL DONE (capacity_more v1/v4/v7)", flush=True)


if __name__ == "__main__":
    main()
