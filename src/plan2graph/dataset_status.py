"""출처별 검수 현황 집계 — 총/정상/격리 + 격리 사유 분포 (DATASET_DESIGN §2③).

각 그래프 레코드의 meta.status(success|quarantine)·meta.reason(위반 규칙)을 모아
"총 N개 중 정상 X · 격리 Y, 사유별 분포"를 만든다. GUI가 이를 보여주고, 사유를
눌러 해당 격리 도면을 원본∥그래프로 검수 → "왜 못 했는지" 파악 → 보정 판단으로 잇는다.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

# 무결성 위반 규칙 → 사람이 읽는 사유(rules.py R1~R5).
RULE_KO = {
    "R1_isolated_component": "분리된 덩어리(여러 세대/조각)",
    "R2_doorless_room": "문 없는 고립 방",
    "R3_unreachable_from_entrance": "현관에서 도달 불가",
    "R4_no_entrance": "현관(진입점) 없음",
    "R5_unresolved_doors": "미해소 문",
    "duplicate": "중복(동일 평면도 사본)",
    "untyped_rooms": "방 타입 미정(Undefined만)",
    "": "사유 미기록",
}


def reason_label(rule: str) -> str:
    return RULE_KO.get(rule, rule or "사유 미기록")


def scan_status(graphs_dir: Path) -> dict:
    """graphs_dir/*.json 메타 집계.
    반환: {total, success, quarantine, reasons:{rule:count}, by_id:{stem:(status,reason)}}.
    by_id 키는 파일 stem(=graph_id, 예 'RPLAN_42007')."""
    total = success = 0
    reasons: Counter = Counter()
    by_id: dict[str, tuple[str, str]] = {}
    if graphs_dir.is_dir():
        for f in graphs_dir.glob("*.json"):
            try:
                m = json.loads(f.read_text(encoding="utf-8")).get("meta", {})
            except Exception:  # noqa: BLE001
                continue
            total += 1
            stt = m.get("status", "success")
            rsn = (m.get("reason") or "").strip()
            by_id[f.stem] = (stt, rsn)
            if stt == "success":
                success += 1
            else:   # 레코드당 사유는 중복제거(같은 규칙 다회 위반=1로 카운트)
                for r in (set(rsn.split(",")) if rsn else {""}):
                    reasons[r.strip()] += 1
    return {"total": total, "success": success, "quarantine": total - success,
            "reasons": dict(reasons.most_common()), "by_id": by_id}


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.path[:0] = [str(Path(__file__).resolve().parents[2]),
                    str(Path(__file__).resolve().parents[2] / "src")]
    from plan2graph import sources
    sid = sys.argv[1] if len(sys.argv) > 1 else "rplan"
    s = scan_status(sources.graphs_dir(sid))
    print("%s: 총 %d · 정상 %d · 격리 %d" % (sid, s["total"], s["success"], s["quarantine"]))
    for r, c in s["reasons"].items():
        print("   격리사유 %-32s %6d  (%s)" % (r, c, reason_label(r)))
