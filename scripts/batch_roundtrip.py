"""wall-cycle 코덱 배치 라운드트립 검증 (ADR-0012 작업1).

한국 g-0.4 그래프 다수에 roundtrip_metrics를 돌려 코덱 견고성 분포를 본다.
한 샘플은 일화적 → 수백~수천 샘플 통계로 ADR(토큰 문법) 확정 근거를 만든다.

사용(서버 115):
  PYTHONPATH=src python scripts/batch_roundtrip.py --dir <corrected/graphs> --grid 128 --limit 2000
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys

sys.path.insert(0, "src")
from plan2graph import wallcycle_codec as wc  # noqa: E402


def _q(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    i = max(0, min(len(s) - 1, int(round(p * (len(s) - 1)))))
    return round(s[i], 3)


def _is_g04(g):
    """g-0.4 기하 그래프(rooms.polygon 보유)인지 — Parsed(layout.nodes)는 제외."""
    rooms = g.get("rooms")
    if not isinstance(rooms, dict) or not rooms:
        return False
    for r in rooms.values():
        if isinstance(r, dict) and r.get("polygon"):
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="graphs 디렉토리")
    ap.add_argument("--grid", type=int, default=128)
    ap.add_argument("--simplify-frac", type=float, default=0.01)
    ap.add_argument("--wall-snap", action="store_true",
                    help="gap-closing union 켜기(폐기됨 — open토큰이 대체, 진단용만)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--glob", default="*.json")
    ap.add_argument("--apt-only", action="store_true", help="house=APT만(ADR-0011)")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.dir, "**", args.glob), recursive=True))
    if not files:
        files = sorted(glob.glob(os.path.join(args.dir, args.glob)))
    if args.limit:
        files = files[: args.limit]

    n_total = len(files)
    n_skip_schema = n_err = n_ok = 0
    n_apt_skip = 0
    jac, area_dev, overlap, tokens, ncorners = [], [], [], [], []
    tok_ok = 0
    room_keep = door_keep = win_keep = 0
    worst = []   # (jaccard, plan_id)

    for f in files:
        try:
            g = json.load(open(f, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            n_err += 1
            continue
        if not _is_g04(g):
            n_skip_schema += 1
            continue
        if args.apt_only and (g.get("house") or (g.get("meta") or {}).get("house_type")) != "APT":
            n_apt_skip += 1
            continue
        try:
            m = wc.roundtrip_metrics(g, grid=args.grid, simplify_frac=args.simplify_frac,
                                     use_wall_snap=args.wall_snap)
        except Exception as e:  # noqa: BLE001
            n_err += 1
            if n_err <= 5:
                print(f"  ERR {os.path.basename(f)}: {type(e).__name__}: {e}")
            continue
        n_ok += 1
        if m["token_roundtrip_ok"]:
            tok_ok += 1
        if m["n_rooms_in"] == m["n_rooms_out"]:
            room_keep += 1
        if m["n_doors_in"] == m["n_doors_out"]:
            door_keep += 1
        if m["n_windows_in"] == m["n_windows_out"]:
            win_keep += 1
        if m["adj_jaccard"] is not None:
            jac.append(m["adj_jaccard"])
            worst.append((m["adj_jaccard"], g.get("plan_id") or os.path.basename(f)))
        if m["area_ratio_med"] is not None:
            area_dev.append(abs(m["area_ratio_med"] - 1.0))
        if m["overlap_area_frac"] is not None:
            overlap.append(m["overlap_area_frac"])
        tokens.append(m["n_tokens"])
        ncorners.append(m["n_corners"])

    print("=" * 64)
    print(f"파일 {n_total} | g-0.4 검증 {n_ok} | 스키마skip {n_skip_schema} "
          f"| APTskip {n_apt_skip} | 에러 {n_err}  (grid={args.grid} simplify={args.simplify_frac})")
    if not n_ok:
        return
    print("-" * 64)
    print(f"토큰 무손실      : {tok_ok}/{n_ok} ({100*tok_ok/n_ok:.1f}%)")
    print(f"방 수 보존       : {room_keep}/{n_ok} ({100*room_keep/n_ok:.1f}%)")
    print(f"문 수 보존       : {door_keep}/{n_ok} ({100*door_keep/n_ok:.1f}%)")
    print(f"창 수 보존       : {win_keep}/{n_ok} ({100*win_keep/n_ok:.1f}%)")
    print("-" * 64)
    print(f"인접 jaccard     : mean {statistics.mean(jac):.3f}  "
          f"med {statistics.median(jac):.3f}  p10 {_q(jac,0.1)}  p25 {_q(jac,0.25)}  p90 {_q(jac,0.9)}")
    print(f"면적 |ratio-1|   : mean {statistics.mean(area_dev):.3f}  "
          f"med {statistics.median(area_dev):.3f}  p90 {_q(area_dev,0.9)}")
    print(f"겹침 area frac   : mean {statistics.mean(overlap):.4f}  "
          f"med {statistics.median(overlap):.4f}  p90 {_q(overlap,0.9)}  max {max(overlap):.4f}")
    print(f"토큰 길이        : mean {statistics.mean(tokens):.0f}  "
          f"med {statistics.median(tokens):.0f}  p90 {_q(tokens,0.9)}  max {max(tokens)}")
    print(f"corner 수        : mean {statistics.mean(ncorners):.0f}  "
          f"med {statistics.median(ncorners):.0f}  max {max(ncorners)}")
    print("-" * 64)
    worst.sort()
    print("최저 인접 보존 10:")
    for j, pid in worst[:10]:
        print(f"  jaccard {j:.3f}  {pid}")


if __name__ == "__main__":
    main()
