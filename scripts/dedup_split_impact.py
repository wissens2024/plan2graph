#!/usr/bin/env python3
"""dedup_split_impact — split 방식이 중복 불일치의 영향을 어떻게 바꾸는지 전수 측정.

왜 필요한가: 중복 통일을 '어디까지 할까'는 split 방식에 **의존한다**. 지금처럼 `plan_id`를
해시해 split하면 같은 평면의 다르게 보정된 사본이 train과 test로 흩어져 **평가가 오염**된다.
그룹 서명(`dedup_index.sig_of`)을 해시하면 그룹 전체가 한 split에 들어가므로 그 오염이
구조적으로 사라지고, 남는 문제는 **test에 떨어진 불일치 그룹**뿐이다.

재는 것:
  [1] 지금 vs 서명해시 — 가로지르는 그룹 수 · test에 닿는 장수
  [2] 대표 1개로 동결할 때 실제로 내용이 바뀌는 보정본(소수 변종) 수
  [3] test 집합의 실체 — 사본으로 부풀어 있나(장수 vs 고유 평면)
  [4] test 고유 평면의 사람 보증 상태(보정 없음 / 단일 / 일치 / 불일치)
  [5] 미보정 도면의 구성 — 전파로 메울 것 / 규칙이 유일한 것 / 손대면 안 되는 것

산출: 표준출력 + `data/staging/dedup_test_blockers.csv`
      (= 평가 정답지를 확정하기 위해 사람이 봐야 하는 그룹 목록)

원칙: **읽기 전용.** 어떤 보정본도 고치지 않는다.

usage:
  python scripts/dedup_split_impact.py
  python scripts/dedup_split_impact.py --no-csv
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGING = os.path.join(ROOT, "data", "staging")
KINDS = ("A구조", "B라벨", "C위치")


def load_ids(path):
    d = json.load(open(path, encoding="utf-8"))
    if isinstance(d, dict):
        for k in ("ids", "plan_ids", "kept", "items"):
            if isinstance(d.get(k), list):
                return set(map(str, d[k]))
        return set(map(str, d))
    return set(map(str, d))


def bucket(key, mod=20, test=1, val=1):
    """build_token_dataset._bucket 와 동일한 결정론 split."""
    h = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % mod
    return "test" if h < test else ("val" if h < test + val else "train")


def kind_of(g):
    """불일치의 종류 — dedup_worklist.diff_kind 와 같은 판정."""
    vs = g["variants"]
    if len({v["n_rooms"] for v in vs}) > 1:
        return "A구조"
    if len({tuple(sorted(v["roles"].items())) for v in vs}) == 1:
        return "C위치"
    return "B라벨"


def roles_diff(base, other):
    out = []
    for k in sorted(set(base) | set(other)):
        d = other.get(k, 0) - base.get(k, 0)
        if d:
            out.append(("+" if d > 0 else "-") + k + ("" if abs(d) == 1 else "x%d" % abs(d)))
    return " ".join(out) or "조합동일·위치만"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default=os.path.join(STAGING, "dedup_index.json"))
    ap.add_argument("--gated", default=os.path.join(STAGING, "korean_gated_ids.json"))
    ap.add_argument("--edits", default=os.path.join(STAGING, "corrected", "edits"))
    ap.add_argument("--parsed", default=os.path.join(STAGING, "parsed", "graphs"))
    ap.add_argument("--csv", default=os.path.join(STAGING, "dedup_test_blockers.csv"))
    ap.add_argument("--no-csv", action="store_true")
    args = ap.parse_args()

    idx = json.load(open(args.index, encoding="utf-8"))
    groups, sig_of = idx["groups"], idx["sig_of"]
    gated = load_ids(args.gated)
    corrected = set(f[:-5] for f in os.listdir(args.edits) if f.endswith(".json"))
    conf = {s: g for s, g in groups.items() if g.get("status") == "conflict"}

    print("모집단: gated %d장 / 고유 %d개 — 보정 %d · 미보정 %d"
          % (len(gated), len({sig_of.get(m, m) for m in gated}),
             len(gated & corrected), len(gated - corrected)))

    # [1] split 전/후
    straddle, test_now = collections.Counter(), collections.Counter()
    after_g, after_p = collections.Counter(), collections.Counter()
    for s, g in conf.items():
        gm = [m for m in g["members"] if m in gated]
        if not gm:
            continue
        k = kind_of(g)
        bks = {bucket(m) for m in gm}
        if "test" in bks:
            if len(bks) > 1:
                straddle[k] += 1
            test_now[k] += sum(1 for m in gm if bucket(m) == "test")
        b = bucket(s)
        after_g[(k, b)] += 1
        after_p[(k, b)] += len(gm)

    print("\n[1] 불일치 %d그룹이 평가에 닿는 양" % len(conf))
    print("  지금(plan_id 해시) — 사본이 train/test를 가로지른다")
    print("    %-6s %10s %10s" % ("종류", "가로지름", "test장수"))
    for k in KINDS:
        print("    %-6s %10d %10d" % (k, straddle[k], test_now[k]))
    print("    %-6s %10d %10d" % ("합", sum(straddle.values()), sum(test_now.values())))
    print("  서명해시 — 그룹이 한 split에만 (가로지름 0, 구조적)")
    print("    %-6s %8s %8s %8s %10s" % ("종류", "train", "val", "test", "test장수"))
    for k in KINDS:
        print("    %-6s %8d %8d %8d %10d"
              % (k, after_g[(k, "train")], after_g[(k, "val")],
                 after_g[(k, "test")], after_p[(k, "test")]))
    tg = sum(after_g[(k, "test")] for k in KINDS)
    tp = sum(after_p[(k, "test")] for k in KINDS)
    rg = sum(after_g[(k, "train")] for k in KINDS)
    print("    %-6s %8d %8s %8d %10d" % ("합", rg, "-", tg, tp))

    # [2] 대표 동결 시 실제로 바뀌는 장수
    chg = collections.Counter()
    for s, g in conf.items():
        vs = sorted(g["variants"], key=lambda v: -v["count"])
        b = bucket(s)
        for v in vs[1:]:                      # 최다 변종이 이긴다
            chg[b] += sum(1 for m in v["members"] if m in gated)
    print("\n[2] 대표 1개로 동결 시 내용이 바뀌는 보정본: train %d · val %d · test %d (합 %d)"
          % (chg["train"], chg["val"], chg["test"], sum(chg.values())))
    base = len(gated & corrected) or 1
    print("    → train 쪽 %d장 = 보정 %d장의 %.1f%% (라벨 노이즈 상한)"
          % (chg["train"], base, 100.0 * chg["train"] / base))

    # [3][4] test 집합의 실체
    print("\n[3] test 집합 — 사본으로 부풀어 있나")
    for name, keyf in (("지금(plan_id)", lambda m: m), ("서명해시", lambda m: sig_of.get(m, m))):
        te = [m for m in gated if bucket(keyf(m)) == "test"]
        uq = {sig_of.get(m, m) for m in te}
        print("    %-14s %4d장 · 고유 %4d개 (사본 %.1f배) · 불일치 그룹 %d"
              % (name, len(te), len(uq), len(te) / max(len(uq), 1),
                 sum(1 for s in uq if s in conf)))

    te = [m for m in gated if bucket(sig_of.get(m, m)) == "test"]
    uq = {sig_of.get(m, m) for m in te}
    st = collections.Counter(groups.get(s, {}).get("status", "?") for s in uq)
    print("\n[4] 서명해시 test 고유 %d개의 사람 보증 상태" % len(uq))
    for k in ("none", "single", "agree", "conflict"):
        print("    %-9s %4d" % (k, st[k]))
    print("    → 사람 손이 더 필요한 것 = 불일치 %d그룹 대표 확정 + 보정 없음 %d장 = %d건"
          % (st["conflict"], st["none"], st["conflict"] + st["none"]))

    # 작업표
    rows = []
    for s in sorted(uq):
        g = groups.get(s)
        if not g or g.get("status") != "conflict":
            continue
        vs = sorted(g["variants"], key=lambda v: -v["count"])
        rows.append({
            "group": s, "kind": kind_of(g), "house": g.get("house") or "?",
            "copies": g["n"], "gated": sum(1 for m in g["members"] if m in gated),
            "variants": len(vs),
            "rooms": "/".join(str(v["n_rooms"]) for v in vs),
            "counts": "/".join(str(v["count"]) for v in vs),
            "rep": vs[0]["members"][0],
            "diff": " || ".join(roles_diff(vs[0]["roles"], v["roles"]) for v in vs[1:]),
        })
    rows.sort(key=lambda r: (r["kind"], -r["gated"]))
    if rows and not args.no_csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print("    작업표 → %s (%d그룹)" % (os.path.relpath(args.csv, ROOT), len(rows)))

    # [5] 미보정 구성
    allp = set(f[:-5] for f in os.listdir(args.parsed) if f.endswith(".json"))
    unc = allp - corrected
    apt = {p for p in unc if p.startswith("APT")}
    hole = gated - corrected
    sib = sum(1 for p in hole
              if any(m in corrected
                     for m in groups.get(sig_of.get(p, ""), {}).get("members", [])))
    print("\n[5] 미보정 %d장의 구성" % len(unc))
    print("    house_type:", dict(collections.Counter(p.split("_")[0] for p in unc)))
    print("    gated 미보정 %d = 보정된 사본 있음 %d(전파로 메움) + 없음 %d(규칙이 유일)"
          % (len(hole), sib, len(hole) - sib))
    print("    게이트밖 APT %d (학습 코퍼스 영향 0 · 도면화 개발용)" % len(apt - gated))
    print("    ROW+DEH %d — 규칙 적용 금지(오염 0 일반화 평가셋)" % (len(unc) - len(apt)))


if __name__ == "__main__":
    main()
