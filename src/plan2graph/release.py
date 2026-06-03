"""데이터셋 버전 동결 — v0, v1, ... 스냅샷 + 고정 test split.

방법론: 깨끗한 baseline(v0)로 끝까지 → 보정·증강(v1)로 끝까지 → 동일 test로 비교.
- 각 버전은 그 시점의 '완전한 스냅샷'(diff 아님). v1 ⊇ v0 + 교정.
- ★불변식: test 셋은 v0에서 한 번 동결 → 모든 버전이 공유(누수 방지·비교 타당).
  새 데이터는 train/val로만 간다.
- '정상 처리' 기준: 단일 세대(현관 정확히 1개). 멀티세대 병합(현관≥2)은 제외.

산출: data/releases/<version>/ (graphs·splits·dataset_card·manifest)
      data/releases/_frozen_test.json (v0에서 정의된 test 시트 — 이후 버전 공유)
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402

RELEASES = config.DATA_DIR / "releases"
FROZEN_TEST = RELEASES / "_frozen_test.json"


def _sheet_key(graph_id: str) -> str:
    return graph_id.rsplit("_u", 1)[0]


def _bucket(sheet_key: str) -> str:
    h = hashlib.md5(f"{config.SPLIT_SEED}:{sheet_key}".encode()).hexdigest()
    x = int(h[:8], 16) / 0xFFFFFFFF
    r = config.SPLIT_RATIOS
    return "train" if x < r["train"] else ("val" if x < r["train"] + r["val"] else "test")


def _is_clean(rec: dict) -> tuple[bool, str]:
    """'정상 처리된 단일 세대' 기준: 현관 정확히 1개(멀티세대 병합 제외)."""
    prog = rec["constraints"]["program"]
    ent = prog.get("현관", 0)
    if ent == 0:
        return False, "no_entrance"
    if ent >= 2:
        return False, f"multi_household(현관{ent})"
    if prog.get("거실", 0) >= 2:
        return False, f"multi_household(거실{prog['거실']})"
    return True, ""


def freeze(version: str, src_graphs: Path = None) -> dict:
    """현재 채택 그래프에서 '정상' 세대만 골라 버전 스냅샷 동결."""
    from plan2graph import sources
    src = src_graphs or sources.graphs_dir("aihub")  # staging/aihub 우선·없으면 processed
    out = RELEASES / version
    if out.exists():
        shutil.rmtree(out)
    (out / "graphs").mkdir(parents=True)
    (out / "splits").mkdir()

    files = sorted(src.glob("*.json"))
    kept, excluded = [], Counter()
    for f in files:
        rec = json.loads(f.read_text(encoding="utf-8"))
        ok, reason = _is_clean(rec)
        if not ok:
            excluded[reason.split("(")[0]] += 1
            continue
        kept.append(rec)
        shutil.copy2(f, out / "graphs" / f.name)

    # 고정 test 셋: v0에서 정의 → 저장. 이후 버전은 그 test를 재사용.
    frozen = json.loads(FROZEN_TEST.read_text(encoding="utf-8")) if FROZEN_TEST.exists() else None
    test_sheets = set(frozen["test_sheets"]) if frozen else None

    splits = {"train": [], "val": [], "test": []}
    new_test = set()
    for rec in kept:
        sk = _sheet_key(rec["graph_id"])
        if test_sheets is not None:
            s = "test" if sk in test_sheets else (
                "val" if _bucket(sk) == "val" else "train")  # 신규는 train/val만
        else:
            s = _bucket(sk)
            if s == "test":
                new_test.add(sk)
        splits[s].append(rec["graph_id"])

    if test_sheets is None:   # v0: test 동결 저장
        FROZEN_TEST.parent.mkdir(parents=True, exist_ok=True)
        FROZEN_TEST.write_text(json.dumps(
            {"defined_by": version, "test_sheets": sorted(new_test)},
            ensure_ascii=False, indent=2), encoding="utf-8")

    for s, ids in splits.items():
        (out / "splits" / f"{s}.txt").write_text("\n".join(sorted(ids)) + "\n",
                                                 encoding="utf-8")

    n_scaled = sum(1 for r in kept if r["meta"].get("scale_confidence") == "ok")
    manifest = {
        "version": version,
        "frozen_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n_graphs": len(kept),
        "n_sheets": len({_sheet_key(r["graph_id"]) for r in kept}),
        "splits": {s: len(v) for s, v in splits.items()},
        "test_frozen_by": (frozen or {}).get("defined_by", version),
        "clean_filter": "현관==1 (단일세대), 멀티세대 병합 제외",
        "excluded": dict(excluded),
        "n_scaled_m2": n_scaled,
        "source": "AI-Hub 71465 (Training+Validation 통합, 지문중복 제거)",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    return manifest


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ver = sys.argv[1] if len(sys.argv) > 1 else "v0"
    m = freeze(ver)
    print(json.dumps(m, ensure_ascii=False, indent=2))
