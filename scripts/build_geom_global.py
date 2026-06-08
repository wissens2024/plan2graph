"""글로벌(RPLAN+CubiCasa) 기하 데이터셋 — 사전학습용 g_global.

변환 그래프(layout.nodes: type·centroid_px·area_px2·n_windows + edges)를 geom 형식으로.
RPLAN/CubiCasa 노드엔 박스(w,h)가 없어 **centroid+area로 정사각 근사 폴리곤** 합성
(위치·크기 prior 학습용). 실측 박스인 g0로 파인튜닝해 비율 보정.
train_geom.load_units가 그대로 읽는 rooms{폴리곤·area_px·role·n_windows}+edges 형식.

사용: python scripts/build_geom_global.py --version g_global
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _poly(cx, cy, area):
    s = max(math.sqrt(max(area, 1.0)), 1.0) / 2          # 정사각 근사 반변
    return [[cx - s, cy - s], [cx + s, cy - s], [cx + s, cy + s], [cx - s, cy + s]]


def _convert(gfile):
    d = json.loads(Path(gfile).read_text(encoding="utf-8"))
    L = d.get("layout", d)
    nodes = L.get("nodes", [])
    if len(nodes) < 2:
        return None
    rooms = {}
    for n in nodes:
        c = n.get("centroid_px") or [0, 0]
        a = n.get("area_px2", 0) or 0
        rooms[str(n["id"])] = {"role": n.get("type", "기타"), "area_px": a,
                               "n_windows": n.get("n_windows", 0),
                               "polygon": _poly(c[0], c[1], a)}
    edges = [{"from": str(e["source"]), "to": str(e["target"]), "via": e.get("via")}
             for e in L.get("edges", [])]
    return {"plan_id": d.get("graph_id", Path(gfile).stem), "rooms": rooms,
            "edges": edges, "n_rooms": len(rooms), "n_edges": len(edges), "corrected": False}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="g_global")
    a = ap.parse_args()
    import config
    rel = config.DATA_DIR / "releases"
    srcs = [rel / "global_rplan" / "graphs", rel / "global_cubicasa" / "graphs"]
    out_dir = rel / a.version
    out_dir.mkdir(parents=True, exist_ok=True)
    outf = out_dir / "geom.jsonl"
    n = skip = 0
    t0 = time.time()
    with outf.open("w", encoding="utf-8") as f:
        for sd in srcs:
            gs = sorted(glob.glob(str(sd / "*.json")))
            print(f"[{sd.parent.name}] {len(gs):,} graphs")
            for g in gs:
                try:
                    rec = _convert(g)
                    if rec:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        n += 1
                    else:
                        skip += 1
                except Exception:  # noqa: BLE001
                    skip += 1
                if n % 10000 == 0 and n:
                    print(f"  ...{n:,} ({(time.time()-t0)/60:.1f}min)")
    manifest = {"version": a.version, "schema": "geometry", "auto": True,
                "houses": ["GLOBAL(RPLAN+CubiCasa)"], "n_plans": n, "n_units": n,
                "n_skip": skip, "note": "사전학습용 · centroid+area 정사각 근사"}
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{a.version}: units={n:,} skip={skip} ({(time.time()-t0)/60:.1f}min) → {outf}")


if __name__ == "__main__":
    main()
