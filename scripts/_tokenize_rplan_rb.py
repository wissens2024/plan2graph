"""RPLAN rBoundary(실폴리곤) 토큰화 — grid 가변. tokens_rplan 재현 + grid256 비교용.
.mat의 rBoundary(방별 실제 폴리곤, L자 포함)를 소스로 사용(box 근사 아님).
역할: masterroom→안방 등 위계 보존(원본 토큰 분포와 일치). canonicalize(grid)→encode.

사용: PYTHONPATH=src python scripts/_tokenize_rplan_rb.py --grid 256 --out data/staging/tokens_rplan_rb256
"""
import sys, json, os, argparse, collections, hashlib, statistics
sys.path.insert(0, "src")
from plan2graph import wallcycle_codec as wc
from plan2graph.adapters import rplan_vector as rv
import numpy as np

# RPLAN 카테고리 → 코덱 ROLES (안방/침실/다목적/드레스룸 위계 보존)
RPLAN_ROLE = {0: "거실", 1: "안방", 2: "주방", 3: "화장실", 4: "주방",
              5: "침실", 6: "다목적공간", 7: "침실", 8: "침실", 9: "발코니",
              10: "현관", 11: "드레스룸", 12: "드레스룸"}


def _bucket(gid, mod=20):
    h = int(hashlib.md5(gid.encode()).hexdigest(), 16) % mod
    return "test" if h < 1 else ("val" if h < 2 else "train")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", type=int, default=256)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mat", default="data/raw/rplan/Network/data.mat")
    ap.add_argument("--max-rooms", type=int, default=25)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    vocab = wc._vocab(args.grid)
    os.makedirs(args.out, exist_ok=True)
    fhs = {s: open(os.path.join(args.out, "%s.jsonl" % s), "w", encoding="utf-8")
           for s in ("train", "val", "test")}
    cnt, skip = collections.Counter(), collections.Counter()
    toklens, roomns = [], []
    n = 0
    for nm, s in rv.iter_structs(args.mat):
        n += 1
        if args.limit and n > args.limit:
            break
        if n % 10000 == 0:
            print("  ...%d  train=%d skip=%d" % (n, cnt["train"], sum(skip.values())), flush=True)
        rb = getattr(s, "rBoundary", None)
        rtype = getattr(s, "rType", None)
        if rb is None or rtype is None:
            skip["no_rb"] += 1
            continue
        rb = np.asarray(rb, dtype=object)
        rtype = np.asarray(rtype).ravel()
        rooms = {}
        for i in range(min(len(rb), len(rtype))):
            t = int(rtype[i])
            if t not in RPLAN_ROLE:
                continue
            try:
                pts = np.asarray(rb[i])
                if pts.ndim != 2 or len(pts) < 3:
                    continue
                poly = [[int(x), int(y)] for x, y in pts]
            except Exception:
                continue
            rooms[str(i)] = {"polygon": poly, "role": RPLAN_ROLE[t]}
        if len(rooms) < 2:
            skip["too_few_rooms"] += 1
            continue
        if len(rooms) > args.max_rooms:
            skip["too_many_rooms"] += 1
            continue
        g = {"house": "APT", "rooms": rooms, "validation": {"passed": True},
             "meta": {"country": "CN"}}
        try:
            canon = wc.canonicalize(g, grid=args.grid)
            toks = wc.encode(canon, vocab)
        except Exception as e:
            skip["encode_err"] += 1
            if skip["encode_err"] <= 3:
                print("  ENC ERR %s: %s" % (nm, e))
            continue
        gid = "RPLAN_" + str(nm)
        rec = {"id": gid, "n_tokens": len(toks), "n_rooms": len(canon.rooms),
               "n_corners": len(canon.corners), "tokens": toks}
        b = _bucket(gid)
        fhs[b].write(json.dumps(rec, ensure_ascii=False) + "\n")
        cnt[b] += 1
        toklens.append(len(toks))
        roomns.append(rec["n_rooms"])
    for fh in fhs.values():
        fh.close()
    json.dump(vocab, open(os.path.join(args.out, "vocab.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    manifest = {"line": "rplan_rboundary", "grid": args.grid, "vocab_size": vocab["size"],
                "counts": dict(cnt), "total": sum(cnt.values()), "skip": dict(skip),
                "source": "rplan_network_mat_rBoundary",
                "token_len": {"mean": round(statistics.mean(toklens), 1) if toklens else 0,
                              "max": max(toklens) if toklens else 0,
                              "p99": sorted(toklens)[len(toklens) * 99 // 100] if toklens else 0},
                "rooms": {"median": statistics.median(roomns) if roomns else 0}}
    json.dump(manifest, open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("done -> %s | train=%d val=%d test=%d | tok_max=%d skip=%s"
          % (args.out, cnt["train"], cnt["val"], cnt["test"],
             max(toklens) if toklens else 0, dict(skip)), flush=True)


if __name__ == "__main__":
    main()
