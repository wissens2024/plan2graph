"""레거시 그래프 위치 → staging/<source>/ 이동 (DATASET_DESIGN §9, P2).

expand 패턴: sources.graphs_dir()가 staging 우선·레거시 폴백이라, 이동 전후 모두
코드가 동작한다. 이 스크립트는 '이동(contract)'만 담당.

이번 범위(글로벌만):
  releases/global_cubicasa/graphs → staging/cubicasa5k/graphs
  releases/global_rplan/graphs    → staging/rplan/graphs
⚠️ AI-Hub(processed/graphs)는 review.py/admin.py가 아직 processed/를 직접 읽으므로
   제외(그 리더 연결 후 별도 이동). graphs_dir('aihub')는 그동안 processed로 폴백.

기본 dry-run. --apply 로 실제 이동. 이동 후 schema 백필 권장:
  python src/plan2graph/migrate_schema_02.py --apply
사용: python src/plan2graph/migrate_to_staging.py [--apply]
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402

# (source_id, 레거시 graphs 경로)
MOVES = [
    ("cubicasa5k", config.DATA_DIR / "releases" / "global_cubicasa" / "graphs"),
    ("rplan",      config.DATA_DIR / "releases" / "global_rplan" / "graphs"),
]


def run(apply: bool) -> list[dict]:
    out = []
    for sid, legacy in MOVES:
        dst = config.DATA_DIR / "staging" / sid / "graphs"
        n = len(list(legacy.glob("*.json"))) if legacy.is_dir() else 0
        info = {"source": sid, "legacy": str(legacy), "dst": str(dst),
                "n_files": n, "action": "skip"}
        if not legacy.is_dir():
            info["action"] = "없음(레거시 부재)"
        elif dst.exists():
            info["action"] = "이미 staging 존재 → 건너뜀"
        else:
            info["action"] = "이동" if apply else "이동예정(dry-run)"
            if apply:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(legacy), str(dst))
        out.append(info)
    return out


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    apply = "--apply" in sys.argv
    rows = run(apply)
    print(f"[{'APPLY' if apply else 'DRY-RUN'}] staging 이동(글로벌)")
    for r in rows:
        print(f"  {r['source']:11s} {r['n_files']:>6} files · {r['action']}")
        print(f"      {r['legacy']}\n        → {r['dst']}")
    if not apply:
        print("→ 실제 이동: --apply · 이후 migrate_schema_02.py --apply 로 0.2 백필")
