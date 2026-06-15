"""진단 미니셋 4티어 (ADR-0012 §6) — 생성기 collapse 진단 실험실.

graphs(Parsed, APT+현관1+방수≤25)에서 역할로 난이도 분류 → 티어별 샘플 → 토큰화.
  T1_simple    : 복도/전실 없음, 침실≤2, 방수≤11 (직교 단순)
  T2_canonical : 한국 정준(복도/전실 경유, 발코니) — T1/T3 아닌 정상
  T3_master    : 안방 복합(안방 + 전실/드레스룸/파우더룸, split 필요)
  T4_noise     : validation 미통과(흡수/오라벨) — parser noise robustness
판독(ADR-0012 §6): T1 붕괴=모델/표현 · T1 OK·T3 실패=한국 위상/데이터 · T4 실패=parser noise.

사용(서버 115):
  PYTHONPATH=src python scripts/build_miniset.py --dir data/staging/corrected/graphs \
    --out data/staging/miniset --per-tier 400 --grid 128
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import sys

sys.path.insert(0, "src")
from plan2graph import wallcycle_codec as wc  # noqa: E402

MASTER_AUX = {"전실", "드레스룸", "파우더룸"}
CONNECTOR = {"복도", "전실"}


def _roles(g):
    return [(r.get("role") or r.get("base")) for r in (g.get("rooms") or {}).values()]


def _n_entrance(g):
    return sum(1 for x in _roles(g) if x == "현관")


def _tier(g, passed):
    if not passed:
        return "T4_noise"
    roles = _roles(g)
    rset = set(roles)
    nbed = roles.count("침실") + roles.count("안방")
    nr = len(roles)
    if "안방" in rset and (rset & MASTER_AUX):
        return "T3_master"
    if not (rset & CONNECTOR) and nbed <= 2 and nr <= 11:
        return "T1_simple"
    return "T2_canonical"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--per-tier", type=int, default=400)
    ap.add_argument("--grid", type=int, default=128)
    ap.add_argument("--max-rooms", type=int, default=25)
    args = ap.parse_args()

    vocab = wc._vocab(args.grid)
    os.makedirs(args.out, exist_ok=True)
    files = sorted(glob.glob(os.path.join(args.dir, "APT_*.json")))

    # 1) 티어별 후보 수집(필터: APT+현관1+방수≤25)
    pools = collections.defaultdict(list)
    for f in files:
        try:
            g = json.load(open(f, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if _n_entrance(g) != 1:
            continue
        if len(g.get("rooms") or {}) > args.max_rooms:
            continue
        passed = bool((g.get("validation") or {}).get("passed"))
        pools[_tier(g, passed)].append((f, g))

    # 2) 티어별 결정론 샘플(균등 stride) → 토큰화
    counts, full = {}, {}
    for tier, pool in sorted(pools.items()):
        full[tier] = len(pool)
        if not pool:
            continue
        k = min(args.per_tier, len(pool))
        step = len(pool) / k
        picks = [pool[int(i * step)] for i in range(k)]
        out = open(os.path.join(args.out, f"{tier}.jsonl"), "w", encoding="utf-8")
        n = 0
        for f, g in picks:
            try:
                canon = wc.canonicalize(g, grid=args.grid)
                toks = wc.encode(canon, vocab)
            except Exception:  # noqa: BLE001
                continue
            gid = g.get("plan_id") or os.path.basename(f)[:-5]
            out.write(json.dumps({
                "id": gid, "tier": tier, "n_tokens": len(toks),
                "n_rooms": len(canon.rooms), "n_corners": len(canon.corners),
                "tokens": toks,
            }, ensure_ascii=False) + "\n")
            n += 1
        out.close()
        counts[tier] = n

    json.dump(vocab, open(os.path.join(args.out, "vocab.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    manifest = {"per_tier_target": args.per_tier, "grid": args.grid,
                "vocab_size": vocab["size"],
                "tier_pool_full": full, "tier_sampled": counts,
                "total_sampled": sum(counts.values())}
    json.dump(manifest, open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
