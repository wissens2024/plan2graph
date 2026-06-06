"""runs/index.jsonl 정규화 — 재실행으로 누적된 중복 원장 행 제거(같은 조건 최신만 유지).

키: (run_id, kind, reg_loop|subset|house_type). run_id에 seed가 들어가므로 시드별 구분됨.
같은 키의 옛 행(재평가 전)을 제거하고 **마지막(최신) 행만** 남긴다 → 시드수 정상화.
기본 드라이런(보고만). --apply 시 백업 후 덮어쓰기.
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IDX = ROOT / "runs" / "index.jsonl"


def key(r):
    disc = r.get("reg_loop") or r.get("subset") or r.get("house_type") or ""
    return (r.get("run_id"), r.get("kind"), disc)


def main(apply=False):
    rows = [json.loads(l) for l in IDX.read_text(encoding="utf-8").splitlines() if l.strip()]
    # 같은 키 → 마지막 행만(최신). 순서 보존 위해 역순 dedup 후 다시 뒤집기.
    seen, kept_rev = set(), []
    for r in reversed(rows):
        k = key(r)
        if k in seen:
            continue
        seen.add(k); kept_rev.append(r)
    kept = list(reversed(kept_rev))
    print("총 행: %d → 중복제거 후 %d (제거 %d)" % (len(rows), len(kept), len(rows) - len(kept)))

    def seedcount(rs, v):
        return len({r["run_id"] for r in rs if r.get("kind") == "eval"
                    and r.get("reg_loop") == "off"
                    and (r.get("run_id") or "").startswith("gen-%s-neural" % v)})
    print("combine 버전별 eval(off) 고유 run_id(=시드수):")
    for v in ["v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7"]:
        print("  %-3s before=%d after=%d" % (v, seedcount(rows, v), seedcount(kept, v)))

    if apply:
        bak = IDX.with_suffix(".jsonl.bak_normalize")
        bak.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        IDX.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in kept) + "\n", encoding="utf-8")
        print("적용 완료. 백업: %s" % bak)
    else:
        print("(드라이런 — 적용하려면 --apply)")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main("--apply" in sys.argv)
