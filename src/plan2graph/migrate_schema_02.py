"""스키마 0.1 → 0.2 백필 (DATASET_DESIGN §9).

기존 그래프 레코드에 신규 meta 필드(status/reason/role/tier)를 가산 채움.
- role  : sources.role_of(meta.source)
- tier  : provenance=='v2_pred' → 2, 그 외 sources.tier_of(meta.source)
- status: 기본 'success' (정상/격리 정교화는 큐 연동 시 별도)
- reason: 기본 ''
기본 dry-run. --apply 로 실제 기록. 무손실(있는 값은 보존).

사용: python src/plan2graph/migrate_schema_02.py [경로...] [--apply]
경로 미지정 시 기본: processed/graphs, releases/global_cubicasa/graphs,
                     releases/global_rplan/graphs (+ staging/* 있으면 포함).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph import sources  # noqa: E402


def _default_dirs() -> list[Path]:
    cand = [
        config.PROCESSED_DIR / "graphs",
        config.DATA_DIR / "releases" / "global_cubicasa" / "graphs",
        config.DATA_DIR / "releases" / "global_rplan" / "graphs",
    ]
    staging = config.DATA_DIR / "staging"
    if staging.is_dir():
        cand += sorted(staging.glob("*/graphs"))
    return [d for d in cand if d.is_dir()]


def migrate_record(rec: dict) -> bool:
    """레코드 1건에 0.2 필드 가산. 변경 있으면 True."""
    meta = rec.setdefault("meta", {})
    src = meta.get("source")
    changed = False
    if "role" not in meta or meta.get("role") is None:
        meta["role"] = sources.role_of(src)
        changed = True
    if "tier" not in meta or meta.get("tier") is None:
        meta["tier"] = 2 if meta.get("provenance") == "v2_pred" else sources.tier_of(src)
        changed = True
    if "status" not in meta:
        meta["status"] = "success"
        changed = True
    if "reason" not in meta:
        meta["reason"] = ""
        changed = True
    if rec.get("schema_version") != "0.2":
        rec["schema_version"] = "0.2"
        changed = True
    return changed


def run(dirs: list[Path], apply: bool) -> dict:
    stat = {"scanned": 0, "changed": 0, "dirs": []}
    for d in dirs:
        files = sorted(d.glob("*.json"))
        dchanged = 0
        for f in files:
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            stat["scanned"] += 1
            if migrate_record(rec):
                stat["changed"] += 1
                dchanged += 1
                if apply:
                    f.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        stat["dirs"].append({"dir": str(d), "files": len(files), "changed": dchanged})
    return stat


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = [a for a in sys.argv[1:] if a != "--apply"]
    apply = "--apply" in sys.argv
    dirs = [Path(a) for a in args] if args else _default_dirs()
    if not dirs:
        print("대상 그래프 디렉터리 없음.")
        sys.exit(0)
    stat = run(dirs, apply)
    mode = "APPLY(기록함)" if apply else "DRY-RUN(미기록)"
    print(f"[{mode}] 스캔 {stat['scanned']} · 변경대상 {stat['changed']}")
    for d in stat["dirs"]:
        print(f"  {d['changed']:>6}/{d['files']:<6} {d['dir']}")
    if not apply and stat["changed"]:
        print("→ 실제 적용: 같은 명령에 --apply 추가")
