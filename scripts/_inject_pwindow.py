"""기존 gen_<ver>.pt 체크포인트에 p_window(타입별 창 보유 확률)만 주입.
재학습 없이 생성-방식 개선(샘플링+창)을 동일 가중치에서 A/B 측정하기 위함.
사용: python scripts/_inject_pwindow.py v0
"""
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import torch  # noqa: E402
from plan2graph import model_baseline as mb  # noqa: E402

ver = sys.argv[1] if len(sys.argv) > 1 else "v0"
recs = mb._load_split(ver, "train")
wt, wh = Counter(), Counter()
for r in recs:
    for nd in r["layout"]["nodes"]:
        if isinstance(nd["id"], int):
            wt[nd["type"]] += 1
            if nd.get("n_windows", 0) >= 1:
                wh[nd["type"]] += 1
pw = {t: round(wh[t] / wt[t], 4) for t in wt if wt[t]}

ckpt_path = ROOT / "models" / f"gen_{ver}.pt"
ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
ckpt["p_window"] = pw
torch.save(ckpt, ckpt_path)
print(f"주입 완료: {ckpt_path}")
print("p_window:", {k: pw[k] for k in sorted(pw, key=lambda x: -pw[x])})
