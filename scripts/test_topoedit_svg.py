"""E2E — 영역 그리기(carve)+면적 → SVG → 결정적 위상추출 (AI-Hub linked_demo).

사용자 합의 구조 검증: 사람이 완전 기하(SVG) 만들고 → 위상은 SVG에서 결정적 추출.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph import topoedit as te  # noqa: E402


def main() -> None:
    d = config.DATA_DIR / "raw" / "linked_demo"
    rp = te.scan_dir(d)[0]
    dr, _ = te.load_plan(rp)
    units = te.segment_units(dr)
    room_ids = units[0]
    unit_id = f"{rp.plan_id}_u{min(room_ids)}"
    st = te.init_state(dr, unit_id, rp.house, room_ids)
    print(f"[init]    unit={unit_id} 방={len(st.nodes)} (폴리곤 보유)")

    # 큰 방 하나 골라 그 내부에 사각형을 '그려' 복도로 carve
    target = max((n for n in st.nodes.values() if n.polygon is not None),
                 key=lambda n: n.area_px)
    before_area = target.area_px
    cx, cy = target.cx, target.cy
    pts = [(cx - 50, cy - 50), (cx + 50, cy - 50), (cx + 50, cy + 50), (cx - 50, cy + 50)]
    conn = te.add_drawn_region(st, "복도", pts, dr)
    assert conn is not None, "영역 추가 실패"
    cnode = st.nodes[conn]
    print(f"[draw]    복도 노드 {conn} 면적={cnode.area_px:,.0f}px²  "
          f"(대상방 {target.id}: {before_area:,.0f}→{target.area_px:,.0f})")
    assert cnode.polygon is not None and cnode.area_px > 0, "복도 폴리곤/면적 없음"
    assert abs(cnode.area_px - 100 * 100) < 50, f"그린 면적 이상({cnode.area_px})"
    assert target.area_px < before_area - 9000, "carve 안 됨(방 면적 안 줄음)"
    inter = target.polygon.intersection(cnode.polygon).area
    assert inter < 1.0, f"carve 후에도 겹침({inter})"
    print(f"[carve]   방∩복도 겹침={inter:.2f}px² (차감 정상)")

    # 사람 표시 비-문 연결 1개(거실-복도 open 가정)
    te.add_edge(st, target.id, conn, "open")

    # SVG 라운드트립 + 결정적 위상 추출
    svg = te.to_svg(st, dr)
    assert te.SVG_SCHEMA in svg and "<polygon" in svg and "data-kind=\"door\"" in svg
    regions, doors, links = te.parse_svg(svg)
    print(f"[svg]     polygon={len(regions)} door={len(doors)} link={len(links)}")
    assert len(regions) == sum(1 for n in st.nodes.values() if n.polygon is not None)
    assert len(doors) == sum(1 for dd in dr.doors if dd.centroid)
    assert any(r["id"] == conn and r["base"] == "복도" and r["area_px"] > 0 for r in regions)

    G = te.extract_topology(regions, doors, links)
    print(f"[extract] 노드={G.number_of_nodes()} 엣지={G.number_of_edges()} "
          f"(door={sum(1 for *_ ,dd in G.edges(data='via') if dd=='door')})")
    assert G.number_of_nodes() == len(regions), "추출 노드수 불일치"
    assert conn in G.nodes and G.nodes[conn]["is_connector"], "복도 노드 미추출"
    assert G.nodes[conn]["area_px"] > 0, "복도 면적 미전달"
    assert G.has_edge(target.id, conn), "사람표시 open 연결 미반영"
    assert G.number_of_edges() >= 1, "문 엣지 0(추출 실패)"

    p = te.save_svg(st, dr, status="보정완료", curator="test", ts="2026-06-07 00:00:00")
    assert p.exists() and p.suffix == ".svg"
    re_regions, _, _ = te.parse_svg(p.read_text(encoding="utf-8"))
    assert len(re_regions) == len(regions), "저장 SVG 재파싱 불일치"
    print(f"[save]    {p.name} 재파싱 OK")

    print("\n✅ E2E PASS — 그리기·carve·면적·SVG·결정적 위상추출 전부 정상")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
