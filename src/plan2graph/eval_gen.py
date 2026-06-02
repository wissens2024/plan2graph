"""P3-d A/B 종합 평가 — 데이터버전 × 생성기 × 규제루프 정량 비교.

사업계획서 가설 검증:
- 데이터: v0 vs v1 vs v2(양 vs 질) / 글로벌 사전학습 효과
- 생성기: baseline(통계) vs 신경망(train_gen)
- 규제루프: off vs on (Self-Correction 효과)
지표(고정 test, 버전 공유): 무결성·법규통과·인접사실성(L1)·다양성·신규성.

결과 → data/releases/eval_ab.json (대시보드·보고용). torch 없으면 신경망 config 자동 제외.
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph import model_baseline as mb  # noqa: E402
from plan2graph import gen_loop  # noqa: E402
from plan2graph.topology import EXTERIOR  # noqa: E402

CONNECT = mb.CONNECT_VIAS


def _metrics(gen_fn, test: list[dict], train_sigs: set, use_loop: bool,
             adj_score=None) -> dict:
    n_valid = n_legal = 0
    gen_pairs, real_pairs, gen_sigs = Counter(), Counter(), []
    for i, rec in enumerate(test):
        program = dict(Counter(n["type"] for n in rec["layout"]["nodes"]
                               if isinstance(n["id"], int)))
        if use_loop:
            G, _ = gen_loop.generate_compliant(gen_fn, program, max_tries=5,
                                               repair=True, seed=i, adj_score=adj_score)
        else:
            G = gen_fn(program, random.Random(i))
        v = gen_loop.verify(G)
        n_valid += int(v["integrity_ok"]); n_legal += int(v["legal_ok"])
        gn = {x: d["type"] for x, d in G.nodes(data=True) if x != EXTERIOR}
        sp = []
        for a, b, d in G.edges(data=True):
            if d.get("via") in CONNECT and a != EXTERIOR and b != EXTERIOR:
                pr = tuple(sorted((gn[a], gn[b]))); gen_pairs[pr] += 1; sp.append(pr)
        gen_sigs.append(mb._signature(program, set(sp)))
        rn = {n["id"]: n["type"] for n in rec["layout"]["nodes"] if isinstance(n["id"], int)}
        for e in rec["layout"]["edges"]:
            if e["via"] in CONNECT and isinstance(e["source"], int) and isinstance(e["target"], int):
                real_pairs[tuple(sorted((rn[e["source"]], rn[e["target"]])))] += 1
    gt = sum(gen_pairs.values()) or 1; rt = sum(real_pairs.values()) or 1
    l1 = sum(abs(gen_pairs[k] / gt - real_pairs[k] / rt)
             for k in set(gen_pairs) | set(real_pairs))
    n = len(test)
    novel = sum(1 for s in gen_sigs if s not in train_sigs)
    return {"integrity": round(n_valid / n, 3), "legal": round(n_legal / n, 3),
            "adj_L1": round(l1, 3), "diversity": round(len(set(gen_sigs)) / n, 3),
            "novelty": round(novel / n, 3)}


def evaluate_version(version: str, n_test: int | None = None) -> list[dict]:
    train = mb._load_split(version, "train")
    test = mb._load_split(version, "test")
    if n_test:
        test = test[:n_test]
    if not train or not test:
        return []
    rows = []
    # baseline 생성기
    model = mb.fit(train)
    gen_fn, adj = gen_loop.baseline_gen_fn(model)
    tsigs = model.get("train_sigs", set())
    for loop in (False, True):
        m = _metrics(gen_fn, test, tsigs, loop, adj)
        rows.append({"version": version, "generator": "baseline",
                     "reg_loop": "on" if loop else "off", **m})
    # 신경망(체크포인트 있으면)
    ckpt = ROOT / "models" / f"gen_{version}.pt"
    if ckpt.exists():
        try:
            from plan2graph.train_gen import NeuralGenerator
            ng = NeuralGenerator(str(ckpt))
            for loop in (False, True):
                m = _metrics(ng.generate, test, tsigs, loop, None)
                rows.append({"version": version, "generator": "neural",
                             "reg_loop": "on" if loop else "off", **m})
        except Exception as e:  # noqa: BLE001
            print(f"  [신경망 평가 건너뜀: {str(e)[:60]}]")
    return rows


def run(versions: list[str], n_test: int | None = None) -> Path:
    rows = []
    for v in versions:
        if not (config.DATA_DIR / "releases" / v).exists():
            print(f"  [없음] {v}")
            continue
        print(f"평가: {v}")
        rows += evaluate_version(v, n_test)
    out = config.DATA_DIR / "releases" / "eval_ab.json"
    out.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    # 표 출력
    print(f"\n{'version':10} {'gen':9} {'loop':5} {'무결성':>7} {'법규':>6} "
          f"{'인접L1':>7} {'다양성':>7} {'신규성':>7}")
    for r in rows:
        print(f"{r['version']:10} {r['generator']:9} {r['reg_loop']:5} "
              f"{r['integrity']:>7} {r['legal']:>6} {r['adj_L1']:>7} "
              f"{r['diversity']:>7} {r['novelty']:>7}")
    print(f"\n저장: {out}")
    return out


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--versions", default="v0,v1,v2,global_rplan,global_cubicasa")
    ap.add_argument("--n-test", type=int, default=None)
    a = ap.parse_args()
    run([v.strip() for v in a.versions.split(",") if v.strip()], a.n_test)
