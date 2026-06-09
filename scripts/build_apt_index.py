"""APT 유닛 인덱스 빌더 — gold 큐레이션 워크플로우의 universe.

기존 변환 그래프(staging/aihub/graphs/*.json)에서 **APT 유닛 전량**을 열거한다.
재추출하지 않고 이미 변환된 결과를 재사용([[staging-is-current-releases-frozen]]).
graph_id = "APT_FP_{crc8}_{size}_u{i}" → sheet_id="APT_FP_{crc8}_{size}", unit_i=i.

출력: data/staging/aihub_gold/_apt_units.json
  [{unit_id, sheet_id, unit_i, n_rooms, house}, ...]  n_rooms 오름차순(쉬운 것부터).

사용: python scripts/build_apt_index.py            # staging 기준
      python scripts/build_apt_index.py --house APT # (기본 APT)
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph import goldset  # noqa: E402

_NON_ROOM = {"exterior", "wall", "외부", "벽", None}
_UID_RE = re.compile(r"^(?P<sheet>.+)_u(?P<i>\d+)$")


def _graph_dirs():
    """변환 그래프 후보 디렉터리(우선순위: staging → releases/v0)."""
    cands = [config.DATA_DIR / "staging" / "aihub" / "graphs",
             config.release_dir("v0") / "graphs"]
    return [d for d in cands if d.is_dir()]


def _n_rooms(rec: dict) -> int:
    nodes = rec.get("layout", {}).get("nodes", [])
    return sum(1 for n in nodes if n.get("type") not in _NON_ROOM)


def build(house: str = "APT") -> list[dict]:
    dirs = _graph_dirs()
    if not dirs:
        raise SystemExit("변환 그래프 디렉터리를 못 찾음 (staging/aihub/graphs). "
                         "서버에서 build_aihub 변환 먼저.")
    src = dirs[0]
    print(f"그래프 소스: {src}")
    seen, units = set(), []
    for p in sorted(src.glob(f"{house}_*.json")):
        gid = p.stem
        m = _UID_RE.match(gid)
        if not m:
            continue
        if gid in seen:
            continue
        seen.add(gid)
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        units.append({
            "unit_id": gid,
            "sheet_id": m.group("sheet"),
            "unit_i": int(m.group("i")),
            "n_rooms": _n_rooms(rec),
            "house": house,
        })
    units.sort(key=lambda u: (u["n_rooms"], u["unit_id"]))
    return units


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--house", default="APT")
    a = ap.parse_args()
    units = build(a.house)
    out = goldset.save_index(units)
    print(f"{a.house} 유닛 {len(units):,}개 → {out}")
    if units:
        rc = [u["n_rooms"] for u in units]
        print(f"방 수: 최소 {min(rc)} · 중앙값 {sorted(rc)[len(rc)//2]} · 최대 {max(rc)}")


if __name__ == "__main__":
    main()
