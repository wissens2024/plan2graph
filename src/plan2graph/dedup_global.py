"""글로벌 출처 구조 중복 제거 — 그룹당 1장만 정상, 나머지는 '중복' 격리.

같은 평면도가 여러 번 들어온 경우(CubiCasa 세트간 중복, RPLAN 동일레이아웃) 구조
지문으로 묶어, 그룹에서 **한 장만 정상으로 유지**하고 나머지 사본은
status=quarantine·reason='duplicate'(dup_of=원본)로 표시한다.

지문 = 방(type+centroid_px+area_px2) + 엣지(source,target,via)의 정렬 해시(기하 동일).
구조가 같으면 무결성 검증도 같으므로 '통과한 것 하나 keep'이 자연스럽다.

⚠️ 변환(어댑터) 이후 실행하는 후처리. 재변환 시 다시 돌려야 함(convert→dedup→status).
기본 dry-run, --apply로 기록. 사용: python src/plan2graph/dedup_global.py <source...> [--apply]
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph import sources  # noqa: E402


def fingerprint(rec: dict) -> str:
    """기하 포함 구조 지문(동일 평면도 → 동일 해시)."""
    L = rec["layout"]
    nodes = sorted((n.get("type"), tuple(n.get("centroid_px") or []),
                    round(n.get("area_px2") or 0, 1))
                   for n in L["nodes"] if isinstance(n.get("id"), int))
    edges = sorted((str(e["source"]), str(e["target"]), e.get("via")) for e in L["edges"])
    return hashlib.md5(repr((nodes, edges)).encode()).hexdigest()


def dedup(source_id: str, apply: bool) -> dict:
    gdir = sources.graphs_dir(source_id)
    groups: dict[str, list] = defaultdict(list)
    total = 0
    if gdir.is_dir():
        for f in gdir.glob("*.json"):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            groups[fingerprint(rec)].append((f, rec))
            total += 1
    marked = 0
    dgroups = 0
    for members in groups.values():
        if len(members) <= 1:
            continue
        dgroups += 1
        # keep 1: 정상 우선, 그다음 이름순(결정적)
        members.sort(key=lambda fr: (fr[1]["meta"].get("status") != "success", fr[0].name))
        keep = members[0][0].stem
        for f, rec in members[1:]:
            m = rec["meta"]
            m["status"] = "quarantine"
            m["reason"] = "duplicate"
            m["dup_of"] = keep
            marked += 1
            if apply:
                f.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    return {"source": source_id, "total": total, "unique": len(groups),
            "dup_groups": dgroups, "marked": marked}


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply = "--apply" in sys.argv
    srcs = args or ["cubicasa5k", "rplan"]
    print(f"[{'APPLY' if apply else 'DRY-RUN'}] 글로벌 중복 제거")
    for sid in srcs:
        r = dedup(sid, apply)
        print(f"  {r['source']:11s} 총 {r['total']:,} · 고유 {r['unique']:,} · "
              f"중복그룹 {r['dup_groups']:,} · 중복격리 {r['marked']:,}")
    if not apply:
        print("→ 실제 적용: --apply")
