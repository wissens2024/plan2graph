"""균형 frozen test 정의 — 한국형 소버린 AI 벤치마크.

AI-Hub dual(실측 SPA+STR)·현관==1 풀에서 주택유형(APT/DEH/ROW)을 **균형(동등)** 추출한다.
유형당 K시트(기본 100)를 결정적(해시)으로 골라 releases/_frozen_test.json 에 동결 →
이후 release.freeze 가 모든 버전에 공유(v0/v2 비교 타당·누수 방지).

근거:
- 원시분포는 APT 94%라 좋은 평균이 DEH/ROW 실패를 가린다. 소버린 AI는 세 유형을 모두
  인증해야 하므로 **균형 test + 매크로 평균** 헤드라인이 옳다.
- test 는 **AI-Hub 한정**(RPLAN/CubiCasa 는 외국 데이터 → 사전학습 전용, test 미포함).
- 단위 = 시트(세대분할 유닛은 같은 시트 → 같은 split, 누수 방지).

사용: python scripts/define_frozen_test.py [K]   (기본 K=100)
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph import sources  # noqa: E402

HOUSES = ("APT", "DEH", "ROW")
DUAL = {"dual", "dual_dedup_merge"}   # 클린 dual(예측노이즈 없음)만 벤치마크 test 자격
FROZEN = config.DATA_DIR / "releases" / "_frozen_test.json"


def _sheet(gid: str) -> str:
    return gid.rsplit("_u", 1)[0]


def _clean(rec: dict) -> bool:
    p = rec["constraints"]["program"]
    e = p.get("현관", 0)
    return not (e == 0 or e >= 2 or p.get("거실", 0) >= 2)


def _rank(seed: int, sheet: str) -> str:
    return hashlib.md5(f"{seed}:test:{sheet}".encode()).hexdigest()


def select(k: int = 100, seed: int = config.SPLIT_SEED):
    """주택유형별 dual·clean 시트를 해시순으로 K개씩 결정적 선택."""
    prov = sources.provenance_map("aihub")
    gdir = sources.graphs_dir("aihub")
    by_house: dict[str, set] = collections.defaultdict(set)
    for f in os.listdir(gdir):
        if not f.endswith(".json"):
            continue
        gid = f[:-5]
        if prov.get(gid) not in DUAL:
            continue
        rec = json.loads((gdir / f).read_text(encoding="utf-8"))
        if not _clean(rec):
            continue
        by_house[rec["meta"].get("house_type")].add(_sheet(gid))
    chosen, per_type = {}, {}
    for h in HOUSES:
        sheets = sorted(by_house.get(h, set()), key=lambda s: _rank(seed, s))
        pick = sheets[:k]
        chosen[h] = pick
        per_type[h] = {"available": len(sheets), "picked": len(pick)}
        if len(pick) < k:
            print(f"  ⚠ {h}: 가용 {len(sheets)} < K {k} — 전부 사용")
    test_sheets = sorted(s for v in chosen.values() for s in v)
    return test_sheets, per_type


def main(k: int = 100, seed: int = config.SPLIT_SEED):
    test_sheets, per_type = select(k, seed)
    if FROZEN.exists():   # 과거 정의 백업(무손실)
        bak = FROZEN.with_suffix(f".pre_balanced.{datetime.now():%Y%m%d_%H%M%S}.json")
        shutil.copy2(FROZEN, bak)
        print(f"백업: {bak.name}")
    out = {
        "defined_by": "balanced",
        "design": ("AI-Hub dual 균형(주택유형 동등가중) — 한국형 소버린 AI 벤치마크. "
                   "매크로 평균 헤드라인. test=AI-Hub 한정(RPLAN/CubiCasa=사전학습전용)."),
        "k_per_type": k,
        "seed": seed,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "per_type": per_type,
        "n_test_sheets": len(test_sheets),
        "supersedes": "prevalence test(APT편중 489/14/15, 240시트) → 균형 100/100/100",
        "test_sheets": test_sheets,
    }
    FROZEN.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"동결: {FROZEN}  ({len(test_sheets)} 시트, per_type={ {h: per_type[h]['picked'] for h in HOUSES} })")
    return out


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 100)
