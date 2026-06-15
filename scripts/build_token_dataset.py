"""Phase1 Parsed 토큰 데이터셋 빌드 (ADR-0014 Amendment2 · 0015 코덱 · 0016 scope).

graphs/(자동변환=Parsed, edits/ 무시) 중 **APT + 현관1 + 방수≤25 + validation통과**를
wallcycle_codec로 토큰화 → train/val/test jsonl + vocab + manifest.

split = id 해시 기반 결정론(재현). test/val 각 5%, train 90%.
Corrected(인간보정)는 별도 빌드(--use-edits) — Phase2. 지금은 Parsed만.

사용(서버 115):
  PYTHONPATH=src python scripts/build_token_dataset.py \
    --dir data/staging/corrected/graphs --out data/staging/tokens_parsed_apt --grid 128 --max-rooms 25
"""
from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import os
import statistics
import sys

sys.path.insert(0, "src")
from plan2graph import wallcycle_codec as wc  # noqa: E402


def _n_entrance(g):
    return sum(1 for r in (g.get("rooms") or {}).values()
               if (r.get("role") or r.get("base")) == "현관")


def _bucket(gid, mod=20, test=1, val=1):
    """id 해시 결정론 split — 같은 id는 항상 같은 split(재현·누수 방지)."""
    h = int(hashlib.md5(gid.encode("utf-8")).hexdigest(), 16) % mod
    if h < test:
        return "test"
    if h < test + val:
        return "val"
    return "train"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="graphs 디렉토리(Parsed 자동변환)")
    ap.add_argument("--out", required=True, help="출력 디렉토리")
    ap.add_argument("--grid", type=int, default=128)
    ap.add_argument("--max-rooms", type=int, default=25)   # ADR-0011 Amendment
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    vocab = wc._vocab(args.grid)
    os.makedirs(args.out, exist_ok=True)
    files = sorted(glob.glob(os.path.join(args.dir, "APT_*.json")))   # APT만
    if args.limit:
        files = files[: args.limit]

    fhs = {s: open(os.path.join(args.out, f"{s}.jsonl"), "w", encoding="utf-8")
           for s in ("train", "val", "test")}
    cnt = collections.Counter()
    skip = collections.Counter()
    toklens, roomns = [], []

    for f in files:
        try:
            g = json.load(open(f, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            skip["read_err"] += 1
            continue
        if _n_entrance(g) != 1:
            skip["not_single_entrance"] += 1
            continue
        nr = len(g.get("rooms") or {})
        if nr > args.max_rooms:
            skip["too_many_rooms"] += 1
            continue
        if not (g.get("validation") or {}).get("passed"):
            skip["not_passed"] += 1
            continue
        try:
            canon = wc.canonicalize(g, grid=args.grid)
            toks = wc.encode(canon, vocab)
        except Exception as e:  # noqa: BLE001
            skip["encode_err"] += 1
            if skip["encode_err"] <= 5:
                print(f"  ENC ERR {os.path.basename(f)}: {type(e).__name__}: {e}")
            continue
        gid = g.get("plan_id") or os.path.basename(f)[:-5]
        rec = {
            "id": gid,
            "n_tokens": len(toks),
            "n_rooms": len(canon.rooms),
            "n_corners": len(canon.corners),
            "scope": canon.meta.get("scope"),
            "units": canon.meta.get("units"),
            "house": g.get("house") or (g.get("meta") or {}).get("house_type"),
            "tokens": toks,
        }
        b = _bucket(gid)
        fhs[b].write(json.dumps(rec, ensure_ascii=False) + "\n")
        cnt[b] += 1
        toklens.append(len(toks))
        roomns.append(rec["n_rooms"])

    for fh in fhs.values():
        fh.close()

    json.dump(vocab, open(os.path.join(args.out, "vocab.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    manifest = {
        "line": "parsed", "filter": "APT + 현관1 + 방수<=%d + validation통과" % args.max_rooms,
        "grid": args.grid, "max_rooms": args.max_rooms, "vocab_size": vocab["size"],
        "counts": dict(cnt), "total": sum(cnt.values()),
        "skip": dict(skip), "files_scanned": len(files),
        "token_len": {
            "mean": round(statistics.mean(toklens), 1) if toklens else 0,
            "median": statistics.median(toklens) if toklens else 0,
            "max": max(toklens) if toklens else 0,
            "p99": sorted(toklens)[len(toklens) * 99 // 100] if toklens else 0,
        },
        "rooms": {"median": statistics.median(roomns) if roomns else 0,
                  "max": max(roomns) if roomns else 0},
    }
    json.dump(manifest, open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
