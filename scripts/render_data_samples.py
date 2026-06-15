"""학습 데이터 그래프(원본 g-0.4) 샘플 → PNG + DXF 렌더. 데이터 정상 확인용.

생성물이 아니라 *학습 입력 데이터*를 그대로 렌더 — 데이터가 직각·정상인지 눈으로 확인.
APT+현관1+방수≤25+validation통과(Phase1 학습 대상)에서 샘플.

사용:
  PYTHONPATH=src python scripts/render_data_samples.py \
    --dir data/staging/corrected/graphs --out /tmp/data_samples --n 6
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, "src")
from plan2graph import cadrender  # noqa: E402


def _n_entrance(g):
    return sum(1 for r in (g.get("rooms") or {}).values()
               if (r.get("role") or r.get("base")) == "현관")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", default="/tmp/data_samples")
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--max-rooms", type=int, default=25)
    ap.add_argument("--autocorrect", action="store_true",
                    help="자기교정 적용(기본=원본 그대로)")
    ap.add_argument("--via-codec", action="store_true",
                    help="토큰화→복원 경로(canonicalize→canon_to_graph) 거쳐 렌더 — 토큰화가 직각 깨는지 확인")
    ap.add_argument("--fixtures", action="store_true",
                    help="Tier B 가구(역할추론) 배치 — RPLAN 없는 완성 정보 데모")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.dir, "APT_*.json")))
    # Phase1 대상 필터 후 균등 stride 샘플
    pool = []
    for f in files:
        try:
            g = json.load(open(f, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if _n_entrance(g) != 1:
            continue
        if len(g.get("rooms") or {}) > args.max_rooms:
            continue
        if not (g.get("validation") or {}).get("passed"):
            continue
        pool.append((f, g))
    if not pool:
        print("샘플 없음"); return
    step = max(1, len(pool) // args.n)
    picks = pool[::step][: args.n]

    os.makedirs(args.out, exist_ok=True)
    for i, (f, g) in enumerate(picks):
        try:
            if args.via_codec:
                from plan2graph import wallcycle_codec as wc
                g = wc.canon_to_graph(wc.canonicalize(g))
            geom = cadrender.from_geomgraph(g)
            if args.fixtures:                          # Tier B 가구 배치(neuro-symbolic 완성층)
                from plan2graph import semantic_fill
                sc = semantic_fill.infer_scale(g) or 5.0
                for rg in geom.rooms:
                    rd = (g.get("rooms") or {}).get(str(rg.id)) or {}
                    more = semantic_fill.place_fixtures_for_room(rd, g, sc)
                    rg.fixtures = list(getattr(rg, "fixtures", None) or []) + more
            if args.autocorrect:
                geom = cadrender.autocorrect(geom)
            png = cadrender.render_png(geom)
            open(os.path.join(args.out, f"data_{i}.png"), "wb").write(png)
            dxf = cadrender.render_dxf(geom)
            open(os.path.join(args.out, f"data_{i}.dxf"), "wb").write(dxf)
            print(f"data_{i}: {os.path.basename(f)} rooms={len(g.get('rooms') or {})} "
                  f"doors={g.get('n_doors')} windows={g.get('n_windows')}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"data_{i} err: {type(e).__name__}: {e}", flush=True)
    print(f"→ {args.out} (PNG+DXF {len(picks)}쌍)", flush=True)


if __name__ == "__main__":
    main()
