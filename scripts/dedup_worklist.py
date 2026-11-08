#!/usr/bin/env python3
"""dedup_worklist — 아직 '하나로 통일'이 안 된 중복 그룹의 작업 목록.

`dedup_index.json`(읽기 전용 산출물)을 받아, 보정이 2건 이상인데 사본끼리 결과가
다른 그룹만 뽑는다.

⚠ '다수안'을 믿으면 안 된다. 보정본 30,542건 중 18,018건(59%)이 **전파 복사본**
(`meta.propagated`)이다. 사본 40건이 같은 답이라도 그건 한 사람의 판단이 39장에
복사된 것일 수 있다. 그래서 이 스크립트는 **전파본을 빼고 독립 사람 판단만** 센다.
실측: APT·단위세대 불일치 883그룹 중 803개(91%)가 독립 판단으로는 동점 — 즉 대부분
**작업자 1명 대 1명**이다.

분류(학습 코퍼스에 걸친 그룹만 작업 대상):
  A 구조  방 수가 다르다 → 합치기/나누기 판단이 갈렸다. 규칙으로 못 고친다.
  B 라벨  방 수는 같고 역할 조합이 다르다 → 역할 정규화 규칙으로 기계 통일 가능.
  C 위치  방 수·조합까지 같고 어느 칸에 붙었나만 다르다 → 눈으로.
  D 보류  학습 코퍼스에 한 장도 없다 → 통일해도 모델에 영향 0.

산출:
  docs/DEDUP_WORKLIST.md          사람이 읽는 작업표
  data/staging/dedup_worklist.csv 한 줄 = 한 그룹

원칙: 읽기 전용. 어떤 보정본도 고치지 않는다.
"""
import argparse
import collections
import csv
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGING = os.path.join(ROOT, "data", "staging")


def roles_diff(base, other):
    """기준 대비 이 변종의 차이 → '+파우더룸 -펜트리' 식 한 줄."""
    plus, minus = [], []
    for k in sorted(set(base) | set(other)):
        d = other.get(k, 0) - base.get(k, 0)
        if d > 0:
            plus.append("+%s%s" % (k, "" if d == 1 else "x%d" % d))
        elif d < 0:
            minus.append("-%s%s" % (k, "" if d == -1 else "x%d" % -d))
    return " ".join(plus + minus) or "조합 동일(위치만 다름)"


def diff_kind(variants):
    if len({v["n_rooms"] for v in variants}) > 1:
        return "구조"
    if len({tuple(sorted(v["roles"].items())) for v in variants}) == 1:
        return "위치"
    return "라벨"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default=os.path.join(STAGING, "dedup_index.json"))
    ap.add_argument("--gated", default=os.path.join(STAGING, "korean_gated_ids.json"))
    ap.add_argument("--edits", default=os.path.join(STAGING, "corrected", "edits"))
    ap.add_argument("--house", default="APT")
    ap.add_argument("--scope", default="unit", help="unit|floor|unknown|all")
    ap.add_argument("--md", default=os.path.join(ROOT, "docs", "DEDUP_WORKLIST.md"))
    ap.add_argument("--csv", default=os.path.join(STAGING, "dedup_worklist.csv"))
    args = ap.parse_args()

    idx = json.load(open(args.index, encoding="utf-8"))
    raw = json.load(open(args.gated, encoding="utf-8"))
    gated = set(raw if isinstance(raw, list) else (raw.get("ids") or raw.keys()))

    conflicts = {sig: g for sig, g in idx["groups"].items() if g.get("status") == "conflict"}

    # 전파본 판별 — 불일치 그룹 소속 보정본만 읽는다.
    propagated = {}
    for g in conflicts.values():
        for pid in g["members"]:
            if pid in propagated:
                continue
            p = os.path.join(args.edits, pid + ".json")
            if not os.path.exists(p):
                continue
            try:
                meta = (json.load(open(p, encoding="utf-8")).get("meta") or {})
            except Exception:  # noqa: BLE001
                continue
            propagated[pid] = bool(meta.get("propagated"))
    n_prop = sum(1 for v in propagated.values() if v)
    print("[불일치 그룹 소속 보정본] %d건 — 전파 %d · 사람직접 %d"
          % (len(propagated), n_prop, len(propagated) - n_prop))

    rows, tally, roletok, delta = [], collections.Counter(), collections.Counter(), collections.Counter()
    for sig, g in conflicts.items():
        vs = sorted(g["variants"], key=lambda v: -v["count"])
        ind = [sum(1 for m in v["members"] if propagated.get(m) is False) for v in vs]
        nz = sorted((x for x in ind if x > 0), reverse=True)
        if len(nz) <= 1:
            verdict = "단일판단"      # 전파가 변종을 만들었다
        elif nz[0] == nz[1]:
            verdict = "동점"          # 작업자 대 작업자
        else:
            verdict = "독립다수"
        kind = diff_kind(vs)
        row = {
            "group": sig,
            "house": g.get("house") or "?",
            "scope": g.get("scope") or "?",
            "copies": g["n"],
            "corrected": sum(v["count"] for v in vs),
            "uncorrected": g["n"] - sum(v["count"] for v in vs),
            "variants": len(vs),
            "copy_counts": "/".join(str(v["count"]) for v in vs[:8]),
            "indep_counts": "/".join(str(x) for x in ind[:8]),
            "verdict": verdict,
            "kind": kind,
            "rooms": "/".join(str(v["n_rooms"]) for v in vs[:8]),
            "gated": sum(1 for m in g["members"] if m in gated),
            "reps": " | ".join(v["members"][0] for v in vs[:8]),
        }
        row["_vs"] = [(v["members"][0], v["count"], ind[i + 1], v["n_rooms"],
                       roles_diff(vs[0]["roles"], v["roles"]))
                      for i, v in enumerate(vs[1:8])]
        rows.append(row)
        tally[(row["house"], row["scope"], kind)] += 1
        if row["gated"] and row["house"] == args.house and row["scope"] == args.scope:
            for v in vs[1:]:
                delta[v["n_rooms"] - vs[0]["n_rooms"]] += 1
                for k in set(vs[0]["roles"]) | set(v["roles"]):
                    d = v["roles"].get(k, 0) - vs[0]["roles"].get(k, 0)
                    if d:
                        roletok[k] += abs(d)

    print("\n[불일치 그룹 전체] %d" % len(rows))
    for k in sorted(tally, key=lambda k: -tally[k]):
        print("  %-4s %-8s %-5s %5d" % (k[0], k[1], k[2], tally[k]))

    sel = [r for r in rows
           if (args.house == "all" or r["house"] == args.house)
           and (args.scope == "all" or r["scope"] == args.scope)]
    for r in sel:
        r["bucket"] = "D" if not r["gated"] else {"구조": "A", "라벨": "B", "위치": "C"}[r["kind"]]
    sel.sort(key=lambda r: (r["bucket"], -r["gated"], -r["copies"]))
    bk = {b: [r for r in sel if r["bucket"] == b] for b in "ABCD"}

    print("\n=== %s · %s 불일치 %d그룹 ===" % (args.house, args.scope, len(sel)))
    print("  독립 사람 판단:", dict(collections.Counter(r["verdict"] for r in sel)))
    for b, name in (("A", "구조 차이·사람"), ("B", "라벨 차이·규칙"),
                    ("C", "위치만 차이·사람"), ("D", "학습무관·보류")):
        v = bk[b]
        print("  %s %-14s 그룹 %4d · 사본 %6d · 학습 %5d"
              % (b, name, len(v), sum(r["copies"] for r in v), sum(r["gated"] for r in v)))

    cols = ["bucket", "group", "house", "scope", "copies", "corrected", "uncorrected",
            "variants", "copy_counts", "indep_counts", "verdict", "kind", "rooms",
            "gated", "reps"]
    with open(args.csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in sel:
            w.writerow({k: r[k] for k in cols})
    print("\n[csv] %s" % args.csv)

    def table(f, rows_):
        f.write("| # | 그룹 | 사본 | 보정 | 미보정 | 변종 | 사본분포 | 독립판단 | 방 수 | 학습 | 대표 |\n")
        f.write("|--:|---|--:|--:|--:|--:|---|---|---|--:|---|\n")
        for i, r in enumerate(rows_, 1):
            f.write("| %d | `%s` | %d | %d | %d | %d | %s | %s | %s | %d | `%s` |\n"
                    % (i, r["group"], r["copies"], r["corrected"], r["uncorrected"],
                       r["variants"], r["copy_counts"], r["indep_counts"], r["rooms"],
                       r["gated"], r["reps"].split(" | ")[0]))
        f.write("\n<details><summary>변종별 대표 도면번호 · 첫 변종 대비 차이</summary>\n\n")
        for i, r in enumerate(rows_, 1):
            f.write("%d. `%s` — 기준 `%s` (사본 %s건, 방 %s)\n"
                    % (i, r["group"], r["reps"].split(" | ")[0],
                       r["copy_counts"].split("/")[0], r["rooms"].split("/")[0]))
            for rep, cnt, ind, nr, dif in r["_vs"]:
                f.write("   - `%s` (사본 %d · 독립 %d · 방 %d) → %s\n" % (rep, cnt, ind, nr, dif))
        f.write("\n</details>\n")

    with open(args.md, "w", encoding="utf-8") as f:
        f.write("# 중복 도면 — 통일 안 된 그룹 작업표 (DEDUP_WORKLIST)\n\n")
        f.write("> 인덱스 `%s` · `scripts/dedup_worklist.py` (읽기 전용)\n" % idx.get("built_at"))
        f.write("> 대상 = **%s · %s**. 보정이 2건 이상인데 **사본끼리 결과가 다른** 그룹 %d개.\n"
                % (args.house, args.scope, len(sel)))
        f.write("> 전체 불일치 %d그룹(층 평면도·타 주거형태 포함).\n" % len(rows))
        f.write("> 보는 곳: 에디터 사이드바 **중복 검수** → 도면번호로 검색.\n\n")
        f.write("## ⚠ 먼저 — '다수안'을 믿으면 안 된다\n\n")
        f.write("보정본 %d건 중 **%d건(%.0f%%)이 전파 복사본**(`meta.propagated`)이다. "
                "사본 40장이 같은 답이어도 그건 한 사람 판단이 39장에 복사된 것일 수 있다.\n\n"
                % (30542, 18018, 59))
        f.write("그래서 `독립판단` 열 = 변종별 **전파본을 뺀 사람 직접 보정 건수**다. 실측 결과:\n\n")
        vc = collections.Counter(r["verdict"] for r in sel)
        f.write("| 독립 판단 구성 | 그룹 | 뜻 |\n|---|--:|---|\n")
        f.write("| 동점 | %d | 작업자 1명 대 1명 — 많이 고른 쪽이 없다 |\n" % vc.get("동점", 0))
        f.write("| 독립다수 | %d | 진짜 다수가 있다 |\n" % vc.get("독립다수", 0))
        f.write("| 단일판단 | %d | 사람 판단은 하나인데 전파 과정에서 변종이 갈렸다 |\n\n"
                % vc.get("단일판단", 0))
        f.write("→ **대부분이 1대1 대립이다. 다수결로 끝낼 수 없고, 기준을 정해야 한다.**\n\n")
        f.write("## 분류\n\n| | 그룹 | 사본 | 학습 도면 | 처리 |\n|---|--:|--:|--:|---|\n")
        for b, name, how in (
                ("A", "**구조 차이 — 사람**", "방 수가 다르다. 합치기/나누기 판단이 갈렸고 규칙으로 못 고친다"),
                ("B", "라벨 차이 — 규칙", "방 수 같고 역할 조합만 다르다. 역할 정규화로 기계 통일"),
                ("C", "위치만 차이 — 사람", "조합까지 같고 어느 칸에 붙었나만 다르다"),
                ("D", "보류", "학습 코퍼스에 한 장도 없다 — 통일해도 모델에 영향 0")):
            v = bk[b]
            f.write("| %s. %s | %d | %d | %d | %s |\n"
                    % (b, name, len(v), sum(r["copies"] for r in v), sum(r["gated"] for r in v), how))
        human = bk["A"] + bk["C"]
        f.write("\n→ **사람이 봐야 할 그룹 = A + C = %d개** (학습 도면 %d장).\n\n"
                % (len(human), sum(r["gated"] for r in human)))
        f.write("## 갈린 지점이 몇 개 안 된다 — 기준을 정하면 한 번에 닫힌다\n\n")
        f.write("A+B+C %d그룹에서 변종끼리 **방 수** 차이:\n\n"
                % (len(bk["A"]) + len(bk["B"]) + len(bk["C"])))
        f.write("| 방 수 차이 | 변종 쌍 |\n|---|--:|\n")
        for d, c in sorted(delta.items()):
            f.write("| %+d | %d |\n" % (d, c))
        f.write("\n차이가 난 **역할**(절대 변동량 합):\n\n| 역할 | 변동 |\n|---|--:|\n")
        for k, c in roletok.most_common(12):
            f.write("| %s | %d |\n" % (k, c))
        tot = sum(roletok.values()) or 1
        f.write("\n상위 5개 역할이 전체 변동의 **%.0f%%**. "
                "즉 '펜트리를 몇 개로 쪼개나 · 애매한 방을 기타/알파룸/다목적공간 중 무엇으로 부르나 · "
                "파우더룸을 떼나'를 **한 번 정하면** 대부분이 닫힌다.\n\n"
                % (100.0 * sum(c for _, c in roletok.most_common(5)) / tot))
        for b, title in (("A", "A. 구조 차이 %d그룹 — 사람이 봐야 한다"),
                         ("B", "B. 라벨 차이 %d그룹 — 역할 정규화로 기계 통일"),
                         ("C", "C. 위치만 차이 %d그룹")):
            f.write("## " + (title % len(bk[b])) + "\n\n")
            table(f, bk[b])
            f.write("\n")
        f.write("## D. 학습 코퍼스에 없는 %d그룹 — 지금 하지 않는다\n\n" % len(bk["D"]))
        f.write("통일해도 학습·평가에 들어가지 않는다. 전수 목록은 "
                "`data/staging/dedup_worklist.csv`의 `bucket=D` 행.\n")
    print("[md ] %s" % args.md)


if __name__ == "__main__":
    main()
