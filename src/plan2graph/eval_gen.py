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
from plan2graph import experiments as exp  # noqa: E402
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


def _record(run_id: str, version: str, generator: str, pretrain, rows: list):
    """조건별 runs/<run_id>/metrics.json 보존 + 원장(index.jsonl) append.
    덮어쓰지 않으므로 '글로벌 무/유' 등 모든 조건이 나란히 누적된다."""
    run_dir = exp.start_run(run_id)
    exp.write_metrics(run_dir, rows)
    sha = exp.git_commit()
    for r in rows:
        exp.append_index({"kind": "eval", "run_id": run_id, "generator": generator,
                          "version": version, "pretrain": pretrain,
                          "git_commit": sha, **r})


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
    brows = []
    for loop in (False, True):
        m = _metrics(gen_fn, test, tsigs, loop, adj)
        row = {"version": version, "generator": "baseline",
               "reg_loop": "on" if loop else "off", **m}
        rows.append(row); brows.append(row)
    _record(exp.make_run_id("baseline", version, None, 0), version, "baseline", None, brows)
    # 신경망(체크포인트 있으면) — 프로비넌스(run_id·pretrain)는 체크포인트에서 읽음
    ckpt = ROOT / "models" / f"gen_{version}.pt"
    if ckpt.exists():
        try:
            from plan2graph.train_gen import NeuralGenerator
            ng = NeuralGenerator(str(ckpt))
            nrun = ng.run_id or exp.make_run_id("neural", version, None, 42)
            npre = (ng.condition or {}).get("pretrain")
            nrows = []
            for loop in (False, True):
                m = _metrics(ng.generate, test, tsigs, loop, None)
                row = {"version": version, "generator": "neural",
                       "reg_loop": "on" if loop else "off", **m}
                rows.append(row); nrows.append(row)
            _record(nrun, version, "neural", npre, nrows)
        except Exception as e:  # noqa: BLE001
            print(f"  [신경망 평가 건너뜀: {str(e)[:60]}]")
    return rows


def _neural_gen(ng, temperature: float):
    """gen_loop/_metrics가 기대하는 (program, rng) 시그니처로 온도 주입."""
    return lambda program, rng: ng.generate(program, rng, temperature=temperature)


def sweep_temperature(version: str, temps: list, select_split: str = "val") -> None:
    """재학습 없이 생성 온도 T 스윕 — val에서 선택 → test 1회 측정(누수 방지).
    val 스윕은 runs/<base>/temp_sweep_val.json 보존, test 최종은 원장에 -T 태그로 기록."""
    from plan2graph.train_gen import NeuralGenerator
    train = mb._load_split(version, "train")
    sel = mb._load_split(version, select_split)
    test = mb._load_split(version, "test")
    if not train or not sel or not test:
        print(f"  [데이터 없음] {version}"); return
    tsigs = mb.fit(train).get("train_sigs", set())
    ckpt = ROOT / "models" / f"gen_{version}.pt"
    if not ckpt.exists():
        print(f"  [체크포인트 없음] {ckpt}"); return
    ng = NeuralGenerator(str(ckpt))
    base = ng.run_id or exp.make_run_id("neural", version, None, 42)

    print(f"온도 스윕 (선택셋={select_split}, n={len(sel)}) — adj_L1 최소 T 탐색")
    print(f"{'T':>5} {'무결성':>7} {'법규':>6} {'인접L1':>7} {'다양성':>7} {'신규성':>7}")
    sweep = []
    for T in temps:
        m = _metrics(_neural_gen(ng, T), sel, tsigs, False, None)
        sweep.append({"T": T, **m})
        print(f"{T:>5} {m['integrity']:>7} {m['legal']:>6} {m['adj_L1']:>7} "
              f"{m['diversity']:>7} {m['novelty']:>7}")
    # 선택: 다양성 0.5 이상 가드 하에서 adj_L1 최소
    cand = [s for s in sweep if s["diversity"] >= 0.5] or sweep
    best = min(cand, key=lambda s: s["adj_L1"])
    bT = best["T"]
    print(f"\n▶ 선택(val): T={bT}  adj_L1={best['adj_L1']}  다양성={best['diversity']}")
    run_dir = exp.start_run(base)
    (run_dir / "temp_sweep_val.json").write_text(
        json.dumps({"select_split": select_split, "sweep": sweep, "best_T": bT},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    # 최종: test에서 best T 1회 → 원장(-T 태그)
    rid = f"{base}-T{bT}"
    rows = []
    for loop in (False, True):
        m = _metrics(_neural_gen(ng, bT), test, tsigs, loop, None)
        rows.append({"version": version, "generator": "neural", "temperature": bT,
                     "reg_loop": "on" if loop else "off", **m})
    _record(rid, version, "neural", (ng.condition or {}).get("pretrain"), rows)
    print(f"\n=== test 최종(T={bT}) → 원장: {rid} ===")
    print(f"{'loop':5} {'무결성':>7} {'법규':>6} {'인접L1':>7} {'다양성':>7} {'신규성':>7}")
    for r in rows:
        print(f"{r['reg_loop']:5} {r['integrity']:>7} {r['legal']:>6} {r['adj_L1']:>7} "
              f"{r['diversity']:>7} {r['novelty']:>7}")


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
    ap.add_argument("--sweep-temp", default=None,
                    help="생성 온도 스윕(쉼표 T목록). 예: 0.5,0.7,0.85,1.0,1.2,1.5,2.0")
    ap.add_argument("--version", default="v0", help="스윕 대상 버전")
    a = ap.parse_args()
    if a.sweep_temp:
        sweep_temperature(a.version, [float(x) for x in a.sweep_temp.split(",") if x.strip()])
    else:
        run([v.strip() for v in a.versions.split(",") if v.strip()], a.n_test)
