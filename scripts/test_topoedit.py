"""E2E 테스트 — 신규 위상편집 코어를 로컬 linked_demo 실데이터로 검증.

검증: 스캔 → 로드 → 초기상태(엣지 0=자동추론 없음) → 편집(엣지·연결공간·역할)
      → 렌더 → 저장 → 재로드 라운드트립. 하나라도 깨지면 비정상 종료.
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
    plans = te.scan_dir(d)
    assert plans, f"도면 0 ({d})"
    rp = plans[0]
    print(f"[scan]   {len(plans)} plan(s); plan_id={rp.plan_id} labels={list(rp.label_paths)}")

    dr, png = te.load_plan(rp)
    print(f"[load]   rooms={len(dr.rooms)} doors={len(dr.doors)} "
          f"windows={len(dr.windows)} objects={len(dr.objects)} png={len(png)}B")
    assert dr.rooms, "방 0개로 로드됨"
    assert png, "png 비어있음"

    units = te.segment_units(dr)
    print(f"[segment] 세대 {len(units)}개, 방수={[len(u) for u in units]}")
    assert units, "세대 분리 0"
    assert all(len(u) >= 3 for u in units), "min_rooms 미준수"
    assert sum(len(u) for u in units) <= len(dr.rooms), "방 중복 배정"
    # 시트(89방)가 한 덩어리가 아니라 여러 세대로 갈렸는지(타일 분리 검증)
    assert len(units) >= 2, "다세대 시트가 1덩어리로 안 갈림(근접군집 실패)"

    room_ids = units[0]
    unit_id = f"{rp.plan_id}_u{min(room_ids)}"
    st = te.init_state(dr, unit_id, rp.house, room_ids)
    assert len(st.nodes) == len(room_ids), f"노드({len(st.nodes)})≠세대방({len(room_ids)})"
    assert all(e.get("source") == "auto" for e in st.edges), "init 엣지=문 자동연결이어야"
    print(f"[init]   unit={unit_id} nodes={len(st.nodes)} edges={len(st.edges)} (문 기본연결, 편집 출발점)")

    ids = list(st.nodes)
    assert te.add_edge(st, ids[0], ids[1], "door")
    assert not te.add_edge(st, ids[0], ids[0], "door"), "자기루프 허용됨"
    conn = te.add_connector(st, "복도", ids[:3])
    te.set_role(st, ids[0], "거실")
    assert any({e["a"], e["b"]} == {ids[0], ids[1]} for e in st.edges), "엣지 추가 실패"
    assert st.nodes[conn].base == "복도" and st.nodes[conn].source == "human"
    conn_edges = [e for e in st.edges if conn in (e["a"], e["b"])]
    assert len(conn_edges) == 3, f"연결공간 엣지 {len(conn_edges)}≠3"
    assert st.nodes[ids[0]].role == "거실"
    print(f"[edit]   +door엣지, +복도(연결 3), 역할지정 → nodes={len(st.nodes)} edges={len(st.edges)}")

    te.remove_edge(st, conn, ids[0])
    assert len([e for e in st.edges if conn in (e["a"], e["b"])]) == 2, "엣지 삭제 실패"
    print("[edit]   remove_edge OK")

    fig = te.render_figure(dr, png, st, highlight=ids[0])
    out_png = ROOT / "artifacts" / "topoedit_test.png"
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=90)
    assert out_png.exists() and out_png.stat().st_size > 0, "렌더 PNG 비어있음"
    print(f"[render] saved {out_png} ({out_png.stat().st_size}B)")

    rec = te.to_record(st, status="검증완료", curator="test", notes="e2e", ts="2026-06-07 00:00:00")
    p = te.save_record(rec)
    rec2 = te.load_record(unit_id)
    assert rec2, "재로드 None"
    assert len(rec2["nodes"]) == len(st.nodes), "노드 수 불일치"
    assert len(rec2["edges"]) == len(st.edges), "엣지 수 불일치"
    st2 = te.state_from_record(rec2, dr)
    assert len(st2.nodes) == len(st.nodes) and len(st2.edges) == len(st.edges)
    assert st2._next_conn > conn, "_next_conn 복원 실패(중복 id 위험)"
    # 라벨 방 polygon 재부착 확인
    label_nodes = [n for n in st2.nodes.values() if n.source == "label"]
    assert any(n.polygon is not None for n in label_nodes), "polygon 재부착 실패"
    print(f"[persist] saved {p.name}; reload nodes={len(rec2['nodes'])} "
          f"edges={len(rec2['edges'])}; state 복원 OK")

    led = te.load_ledger()
    assert led.get(unit_id, {}).get("status") == "검증완료", "ledger 미기록"
    print(f"[ledger] {unit_id} → {led[unit_id]['status']}")

    print("\n✅ E2E PASS — 스캔·로드·편집·렌더·영속 전부 정상")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
