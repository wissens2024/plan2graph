"""보정(위상편집 SVG) → geometry-rich 그래프 → base(g0)에 덮어써 새 G 버전 빌드.
사람이 위상편집으로 보정한 SVG를 실제 '사용 데이터셋'으로 흘려보내는 루프의 마지막 조각.
  base(g0, 자동) 레코드 + 보정 레코드(같은 unit_id는 보정이 덮어씀, corrected=True)
  → releases/gline/<version>/geom.jsonl (+manifest). 📦 데이터셋·⚖️ 비교에 등장.
사용: python scripts/build_geom_corrected.py --base g0 --version g1
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
    ap.add_argument("--base", default="g0", help="기반 자동 데이터셋(보정으로 덮어씀)")
    ap.add_argument("--version", default="g1", help="출력 G 버전")
    a = ap.parse_args()

    import config
    from plan2graph import topoedit as T, aihub_source as A, geomgraph as G

    t0 = time.time()
    recs: dict = {}                                   # unit_id -> record
    base_f = config.release_dir(a.base) / "geom.jsonl"
    if base_f.exists():
        for ln in base_f.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                r = json.loads(ln)
                recs[r.get("unit_id") or r.get("plan_id")] = r
    n_base = len(recs)
    svgs = sorted(T.REC_DIR.glob("*.svg"))
    print(f"base {a.base}: {n_base:,}세대 · 보정 SVG: {len(svgs)}", flush=True)

    scan_cache: dict = {}
    n_corr = n_skip = 0
    for sp in svgs:
        uid = sp.stem
        house = uid.split("_")[0]
        plan_id = uid.rsplit("_u", 1)[0]
        if house not in scan_cache:
            print(f"  AI-Hub 인덱스 스캔({house})...", flush=True)
            scan_cache[house] = {r["plan_id"]: r for r in A.scan(house=house)}
        rec = scan_cache[house].get(plan_id)
        if not rec:
            print(f"  skip(원천 없음): {uid}"); n_skip += 1; continue
        try:
            dr, _png = A.load(rec)
            state = T.state_from_svg(sp.read_text(encoding="utf-8"), dr, uid, house)
            g = G.build(state, dr)
            g["unit_id"] = uid
            g["plan_id"] = plan_id
            g["house"] = house
            g["corrected"] = True
            recs[uid] = g                              # 보정이 자동을 덮어씀
            n_corr += 1
        except Exception as e:                         # noqa: BLE001
            print(f"  ERR {uid}: {e}"); n_skip += 1

    houses = sorted({r.get("house") for r in recs.values() if r.get("house")})
    out_dir = config.release_write_dir(a.version)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "geom.jsonl").open("w", encoding="utf-8") as f:
        for r in recs.values():
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    manifest = {"version": a.version, "schema": "geometry", "auto": False,
                "houses": houses, "n_plans": len(recs), "n_units": len(recs),
                "n_corrected": n_corr, "base": a.base, "n_skip": n_skip,
                "note": f"{a.base}(자동) + 사람 보정 {n_corr} (보정이 덮어씀)"}
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{a.version}: 세대 {len(recs):,} (보정 {n_corr}·스킵 {n_skip}) "
          f"({(time.time()-t0)/60:.1f}min) → {out_dir}", flush=True)


if __name__ == "__main__":
    main()
