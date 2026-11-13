"""Phase1 Parsed 토큰 데이터셋 빌드 (ADR-0014 Amendment2 · 0015 코덱 · 0016 scope).

graphs/(자동변환=Parsed, edits/ 무시) 중 **APT + 현관1 + 방수≤25 + validation통과**를
wallcycle_codec로 토큰화 → train/val/test jsonl + vocab + manifest.

split = id 해시 기반 결정론(재현). test/val 각 5%, train 90%.
Corrected(인간보정)는 별도 빌드(--use-edits) — Phase2. 지금은 Parsed만.

사용(서버 115):
  PYTHONPATH=src python scripts/build_token_dataset.py \
    --dir data/staging/parsed/graphs --out data/staging/tokens_parsed_apt --grid 128 --max-rooms 25
"""
from __future__ import annotations

import argparse
import collections
import copy
import glob
import hashlib
import json
import os
import statistics
import sys

sys.path.insert(0, "src")
from plan2graph import wallcycle_codec as wc  # noqa: E402


# 단위세대에서 방이 아닌 것 — 기둥·샤프트처럼 다룬다(면적 회계에는 남는다).
STRUCTURE_ROLES = ("계단실", "엘리베이터", "엘리베이터홀", "구조물")


def drop_structure(g):
    """plan_scope=="unit" 이면 수직동선·구조물을 그래프에서 뺀다. (새 그래프, 뺀 수)

    층 평면도에서는 계단실이 **진짜 공용 계단실**이라 그대로 둔다.
    실측(표본 3,000장): 계단실 노드의 91%가 연결 차수 0 — 이미 그래프에서 떠 있다.
    """
    if (g.get("meta") or {}).get("plan_scope") == "floor":
        return g, 0
    rooms = g.get("rooms") or {}
    drop = {str(rid) for rid, r in rooms.items()
            if (r.get("role") or r.get("base") or "") in STRUCTURE_ROLES}
    if not drop:
        return g, 0
    g = copy.deepcopy(g)
    for rid in list(g.get("rooms") or {}):
        if str(rid) in drop:
            g["rooms"].pop(rid, None)
    g["edges"] = [e for e in (g.get("edges") or [])
                  if str(e.get("from")) not in drop and str(e.get("to")) not in drop]
    g["doors"] = [d for d in (g.get("doors") or [])
                  if not ({str(x) for x in (d.get("connects") or [])} & drop)]
    g["windows"] = [w for w in (g.get("windows") or [])
                    if str(w.get("belongs_to")) not in drop]
    for w in (g.get("walls") or []):
        if w.get("rooms"):
            w["rooms"] = [x for x in w["rooms"] if str(x) not in drop]
    g["n_rooms"] = len(g.get("rooms") or {})
    g["n_edges"] = len(g.get("edges") or [])
    g["n_doors"] = len(g.get("doors") or [])
    g["n_windows"] = len(g.get("windows") or [])
    return g, len(drop)


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
    ap.add_argument("--snap", action="store_true",
                    help="벽두께 표현 정규화(축좌표 스냅+edge-split+문 재매칭). 한국(AI-Hub)용. RPLAN엔 불필요.")
    ap.add_argument("--snap-tol", type=float, default=2.5, help="스냅 tolerance(grid 단위). 안전권 2.5~3.0.")
    ap.add_argument("--ids", default="", help="클린 타깃 한정: {'ids':[...]} 또는 [...] JSON. gid 화이트리스트.")
    args = ap.parse_args()

    vocab = wc._vocab(args.grid)
    os.makedirs(args.out, exist_ok=True)
    files = sorted(glob.glob(os.path.join(args.dir, "APT_*.json")))   # APT만
    if args.ids:                                                      # 클린셋(1세대·APT·평면도) 한정
        _idblob = json.load(open(args.ids, encoding="utf-8"))
        idset = set(_idblob["ids"] if isinstance(_idblob, dict) else _idblob)
        files = [f for f in files if os.path.basename(f)[:-5] in idset]
        print(f"[ids] {len(idset)} 화이트리스트 → {len(files)} 파일 매칭")
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
        g, n_drop = drop_structure(g)          # 단위세대의 계단·EV 는 방이 아니다
        if n_drop:
            skip["structure_dropped"] += n_drop
            skip["plans_with_structure"] += 1
        nr = len(g.get("rooms") or {})
        if nr > args.max_rooms:
            skip["too_many_rooms"] += 1
            continue
        if not (g.get("validation") or {}).get("passed"):
            skip["not_passed"] += 1
            continue
        try:
            canon = wc.canonicalize(g, grid=args.grid)
            if args.snap:                                    # 한국 벽두께 → RPLAN식 공유벽 정규화
                canon = wc.snap_split(canon, tol=args.snap_tol)
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
