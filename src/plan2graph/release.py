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
FROZEN_TEST = RELEASES / "_frozen_test.json"   # benchmark(AI-Hub) test 동결, v0에서 정의
RECIPES = RELEASES / "recipes"                 # releases/recipes/<version>.json (안 지워짐)


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


def _default_recipe(version: str) -> dict:
    """레시피 미지정 시 = 기존 v0 동작(AI-Hub 정상분만, 단일세대 필터)."""
    return {"version": version,
            "include": [{"source": "aihub", "status": "success"}],
            "test_from": ["aihub"],
            "clean_filter": "현관==1"}


def load_recipe(version: str, recipe: dict | None = None) -> dict:
    """레시피 결정: 명시 인자 > releases/recipes/<version>.json > 기본(aihub)."""
    if recipe is not None:
        return recipe
    rp = RECIPES / f"{version}.json"
    if rp.exists():
        return json.loads(rp.read_text(encoding="utf-8"))
    return _default_recipe(version)


def _collect(source_id: str, status: str | None,
             provenance: set[str] | None = None) -> list[tuple[Path, dict]]:
    """staging/<source>/graphs(없으면 레거시)에서 status 일치 레코드 수집.

    provenance: 지정 시 manifest.reason 이 이 집합에 든 그래프만(버전 선언적 구분, §5).
                예: v0 = {dual, dual_dedup_merge}(V2V 제외). None이면 필터 없음.
    """
    from plan2graph import sources
    gdir = sources.graphs_dir(source_id)
    prov = sources.provenance_map(source_id) if provenance else {}
    out = []
    if gdir.is_dir():
        for f in sorted(gdir.glob("*.json")):
            rec = json.loads(f.read_text(encoding="utf-8"))
            if status and rec.get("meta", {}).get("status", "success") != status:
                continue
            if provenance is not None and prov.get(rec["graph_id"]) not in provenance:
                continue
            out.append((f, rec))
    return out


def freeze(version: str, recipe: dict | None = None) -> dict:
    """레시피(출처×상태×역할) 조합으로 버전 스냅샷 동결 (DATASET_DESIGN §5).
    benchmark 출처 = 단일세대 필터 + train/val/test(동결 test 공유),
    pretrain 출처 = status 통과분 전체 + train/val만(test 누수 방지)."""
    from plan2graph import sources
    recipe = load_recipe(version, recipe)
    test_from = set(recipe.get("test_from", []))

    out = config.release_write_dir(version)
    if out.exists():
        shutil.rmtree(out)
    (out / "graphs").mkdir(parents=True)
    (out / "splits").mkdir()

    frozen = json.loads(FROZEN_TEST.read_text(encoding="utf-8")) if FROZEN_TEST.exists() else None
    test_sheets = set(frozen["test_sheets"]) if frozen else None

    splits = {"train": [], "val": [], "test": []}
    new_test, excluded, per_source = set(), Counter(), {}
    per_source_sheets = {}   # 출처별 도면(시트) 수 — 세대그래프(per_source)와 구분(단위 혼동 방지)

    for entry in recipe["include"]:
        sid = entry["source"]
        status = entry.get("status", "success")
        provenance = set(entry["provenance"]) if entry.get("provenance") else None
        src = sources.resolve(sid)
        role = entry.get("role") or (src.role if src else "pretrain")
        is_bench = (sid in test_from) or (role == "benchmark")
        cnt = 0
        sheets = set()
        for f, rec in _collect(sid, status, provenance):
            if is_bench:                              # 단일세대 필터(현관==1)
                ok, reason = _is_clean(rec)
                if not ok:
                    excluded[f"{sid}:{reason.split('(')[0]}"] += 1
                    continue
            sk = _sheet_key(rec["graph_id"])
            if is_bench:
                if test_sheets is not None:
                    s = "test" if sk in test_sheets else ("val" if _bucket(sk) == "val" else "train")
                else:
                    s = _bucket(sk)
                    if s == "test":
                        new_test.add(sk)
            else:                                     # pretrain: test 금지(train/val만)
                s = "val" if _bucket(sk) == "val" else "train"
            splits[s].append(rec["graph_id"])
            shutil.copy2(f, out / "graphs" / f.name)
            cnt += 1
            sheets.add(sk)
        per_source[sid] = cnt
        per_source_sheets[sid] = len(sheets)

    if test_sheets is None and new_test:              # v0: test 동결 저장
        FROZEN_TEST.parent.mkdir(parents=True, exist_ok=True)
        FROZEN_TEST.write_text(json.dumps(
            {"defined_by": version, "test_sheets": sorted(new_test)},
            ensure_ascii=False, indent=2), encoding="utf-8")

    for s, ids in splits.items():
        (out / "splits" / f"{s}.txt").write_text("\n".join(sorted(ids)) + "\n",
                                                 encoding="utf-8")

    RECIPES.mkdir(parents=True, exist_ok=True)
    (out / "recipe.json").write_text(json.dumps(recipe, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    n_graphs = sum(per_source.values())
    n_sheets = sum(per_source_sheets.values())
    manifest = {
        "version": version,
        "frozen_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n_graphs": n_graphs,
        "n_sheets": n_sheets,
        "per_source": per_source,
        "per_source_sheets": per_source_sheets,
        "splits": {s: len(v) for s, v in splits.items()},
        "test_frozen_by": (frozen or {}).get("defined_by", version),
        "test_from": sorted(test_from),
        "excluded": dict(excluded),
        "recipe": recipe,
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
