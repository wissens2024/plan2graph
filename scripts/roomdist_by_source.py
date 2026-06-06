"""출처별(AI-Hub·RPLAN·CubiCasa) 방 종류 노드 수 집계 → data/releases/roomdist_by_source.json.

대시보드가 8만+ 그래프를 매번 읽지 않게 1회 스냅샷(데이터 동결이라 재계산 불필요).
AI-Hub=v0(클린 dual), RPLAN=global_rplan, CubiCasa=global_cubicasa (각 단일출처 릴리스).
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)
import config  # noqa: E402

REL = config.DATA_DIR / "releases"
SRC = {"AI-Hub": "v0", "RPLAN": "global_rplan", "CubiCasa": "global_cubicasa"}


def main():
    out = {}
    for name, rel in SRC.items():
        gdir = REL / rel / "graphs"
        if not gdir.exists():
            print(f"  [없음] {rel}/graphs"); continue
        c, ng = Counter(), 0
        for f in gdir.glob("*.json"):
            r = json.loads(f.read_text(encoding="utf-8"))
            for n in r["layout"]["nodes"]:
                t = n.get("type")
                if t and t != "exterior":
                    c[t] += 1
            ng += 1
        out[name] = dict(c.most_common())
        print(f"  {name}({rel}): {ng:,} graphs · {len(c)} types · {sum(c.values()):,} nodes")
    (REL / "roomdist_by_source.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {REL / 'roomdist_by_source.json'}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
