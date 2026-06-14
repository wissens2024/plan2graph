"""Corrected(기하 geometry-rich) 데이터셋 자동 빌드.

AI-Hub 코퍼스 → (검출 라벨) → geomgraph 자동추출 → releases/<version>/geom.jsonl.
사람 보정 없이 자동으로 g0 생성(인간조정 없는 버전이 생성모델로 감).
counts는 releases/<version>/manifest.json 에만 기록(GUI·문서가 여기서 읽음 = 단일 출처).

사용: python scripts/build_geom.py --version g0 --house APT --limit 20   (테스트)
      python scripts/build_geom.py --version g0                         (전량)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="g0")
    ap.add_argument("--house", default=None, help="APT/DEH/ROW, 미지정=전부")
    ap.add_argument("--limit", type=int, default=0, help="도면 수 제한(0=무제한, 테스트용)")
    a = ap.parse_args()

    import config
    from plan2graph import aihub_source as A, topoedit as T, geomgraph as G

    houses = [a.house] if a.house else ["APT", "DEH", "ROW"]
    out_dir = config.release_write_dir(a.version)
    out_dir.mkdir(parents=True, exist_ok=True)
    outf = out_dir / "geom.jsonl"

    n_plan = n_unit = n_skip = 0
    t0 = time.time()
    with outf.open("w", encoding="utf-8") as f:
        for h in houses:
            recs = A.scan(house=h)
            print(f"[{h}] 편집대상 {len(recs):,}건")
            for r in recs:
                if a.limit and n_plan >= a.limit:
                    break
                try:
                    dr, _ = A.load(r)
                    for u in T.segment_units(dr):
                        st = T.init_state(dr, r["plan_id"], r["house"], u)
                        g = G.build(st, dr)
                        g.update({"corrected": False, "version": a.version,
                                  "unit_id": f'{r["plan_id"]}_u{min(u)}'})
                        f.write(json.dumps(g, ensure_ascii=False) + "\n")
                        n_unit += 1
                    n_plan += 1
                    if n_plan % 200 == 0:
                        print(f"  ...plans={n_plan} units={n_unit} "
                              f"({(time.time()-t0)/60:.1f}min)")
                except Exception as e:  # noqa: BLE001
                    n_skip += 1
                    if n_skip <= 10:
                        print(f"  skip {r['plan_id']}: {e}")

    manifest = {"version": a.version, "schema": "geometry", "auto": True,
                "houses": houses, "n_plans": n_plan, "n_units": n_unit,
                "n_skip": n_skip, "limit": a.limit}
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{a.version}: plans={n_plan:,} units={n_unit:,} skip={n_skip} "
          f"({(time.time()-t0)/60:.1f}min) → {outf}")
    print("manifest:", manifest)


if __name__ == "__main__":
    main()
