"""semantic_fill (데모) — 단일 한국 아파트 그래프 1개를 골라 의미추론·렌더.

⚠️ 재사용 로직은 전부 패키지 단일소스 `plan2graph.semantic_fill`로 이동했다.
이 스크립트는 그 함수들로 **그래프 1개를 골라 PNG+DXF를 뽑는 데모**일 뿐이다.

실행:  PYTHONPATH=src python scripts/semantic_fill.py [--plan PLAN_ID]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from plan2graph import cadrender
from plan2graph.semantic_fill import (  # noqa: F401  단일소스
    HINGED_DOOR_MM, infer_scale, room_area_m2, place_fixtures_for_room, FIX_LABEL,
)

DEFAULT_PLAN = "APT_FP_f81f810a_17446927_u3"
GRAPHS_DIR = Path("/home/ju/plan2graph/data/staging/parsed/graphs")


def build(plan_id: str):
    g = json.load(open(GRAPHS_DIR / f"{plan_id}.json", encoding="utf-8"))
    sc = infer_scale(g)
    scale = sc["scale_mm_per_px"]
    for r in g.get("rooms", {}).values():
        r["fixtures"] = []                 # 좌표없는 탐지태그 비우고 우리가 채움
    geom = cadrender.from_geomgraph(g)
    geom.scale_mm_per_px = scale
    rooms_by_id = {r.id: r for r in geom.rooms}
    for rid, r in g["rooms"].items():
        rg = rooms_by_id.get(int(rid))
        if rg is None:
            continue
        rg.area_m2 = room_area_m2(r["area_px"], scale)
        rg.fixtures = place_fixtures_for_room(r, g, scale)
    return g, geom, sc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default=DEFAULT_PLAN)
    ap.add_argument("--out", default="/tmp/semantic_fill")
    args = ap.parse_args()

    g, geom, sc = build(args.plan)
    cadrender.FIX_KO.update(FIX_LABEL)
    print(f"PLAN {geom.plan_id}: 척도 {sc['scale_mm_per_px']:.3f} mm/px "
          f"(여닫이문 중앙값 {sc['median_hinged_px']:.1f}px@{HINGED_DOOR_MM:.0f}mm)")
    geom = cadrender.autocorrect(geom)
    nfix = sum(len(r.fixtures) for r in geom.rooms)
    print(f"기구 {nfix}개 · 자기교정 잔여 {len(geom.issues)}건")

    fig = cadrender.render_fig(geom)
    fig.savefig(f"{args.out}.png", dpi=160, bbox_inches="tight")
    print(f"[PNG] {args.out}.png")
    try:
        with open(f"{args.out}.dxf", "wb") as fh:
            fh.write(cadrender.render_dxf(geom))
        print(f"[DXF] {args.out}.dxf")
    except RuntimeError as e:
        print(f"[DXF] 실패: {e}")


if __name__ == "__main__":
    main()
