"""RPLAN 토큰화 (grid 가변) — tokens_rplan 재현 + grid256 Phase2용.

build_token_dataset.py의 인코딩/split/manifest 로직을 그대로 따르되:
  - glob = RPLAN_*.json (APT 아님)
  - 현관(_n_entrance) 필터 제거 (RPLAN엔 현관 role 없음)
나머지(canonicalize→encode, 결정론 split, validation/max_rooms 필터)는 동일.

사용:
  PYTHONPATH=src python scripts/_tokenize_rplan_grid.py \
    --dir data/releases/parsed/global_rplan/graphs \
    --out data/staging/tokens_rplan_grid256 --grid 256 --max-rooms 25
"""
from __future__ import annotations
import argparse, collections, glob, hashlib, json, os, statistics, sys
sys.path.insert(0, "src")
from plan2graph import wallcycle_codec as wc  # noqa: E402


def _bucket(gid, mod=20, test=1, val=1):
    h = int(hashlib.md5(gid.encode("utf-8")).hexdigest(), 16) % mod
    if h < test:
        return "test"
    if h < test + val:
        return "val"
    return "train"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--grid", type=int, default=256)
    ap.add_argument("--max-rooms", type=int, default=25)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    vocab = wc._vocab(args.grid)
    os.makedirs(args.out, exist_ok=True)
    files = sorted(glob.glob(os.path.join(args.dir, "RPLAN_*.json")))
    if args.limit:
        files = files[: args.limit]
    print("[rplan] %d graphs, grid=%d, vocab=%d" % (len(files), args.grid, vocab["size"]), flush=True)

    fhs = {s: open(os.path.join(args.out, "%s.jsonl" % s), "w", encoding="utf-8")
           for s in ("train", "val", "test")}
    cnt = collections.Counter()
    skip = collections.Counter()
    toklens, roomns = [], []

    for i, f in enumerate(files):
        if i % 10000 == 0:
            print("  ...%d/%d (train=%d skip=%d)" % (i, len(files), cnt["train"], sum(skip.values())), flush=True)
        try:
            g = json.load(open(f, encoding="utf-8"))
        except Exception:
            skip["read_err"] += 1
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
        except Exception as e:
            skip["encode_err"] += 1
            if skip["encode_err"] <= 5:
                print("  ENC ERR %s: %s: %s" % (os.path.basename(f), type(e).__name__, e))
            continue
        gid = g.get("plan_id") or os.path.basename(f)[:-5]
        rec = {
            "id": gid, "n_tokens": len(toks), "n_rooms": len(canon.rooms),
            "n_corners": len(canon.corners), "scope": canon.meta.get("scope"),
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
        "line": "rplan_pretrain", "filter": "RPLAN + 방수<=%d + validation통과" % args.max_rooms,
        "grid": args.grid, "max_rooms": args.max_rooms, "vocab_size": vocab["size"],
        "counts": dict(cnt), "total": sum(cnt.values()),
        "skip": dict(skip), "files_scanned": len(files), "source": "rplan_network_mat",
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
    print("[rplan] done -> %s | train=%d val=%d test=%d skip=%s"
          % (args.out, cnt["train"], cnt["val"], cnt["test"], dict(skip)), flush=True)


if __name__ == "__main__":
    main()
