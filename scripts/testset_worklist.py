#!/usr/bin/env python3
"""testset_worklist — 평가셋(고유 136개)을 사람이 직접 보기 위한 작업표.

왜 136개인가: split을 **그룹 서명 해시**로 바꾸면(ROADMAP A-3-1) gated test는 417장이지만
**고유 평면은 136개**다. 사본은 같은 평면이므로 **대표 1장만 보정하면 평가 정답지가 선다.**
그 136개의 사람 보증 상태를 재면 보정 없음 49 · 단일 57 · 일치 13 · 불일치 17(§8.13).

우선순위:
  P1 불일치 17 — 사본끼리 다르게 보정됐다. 변종을 비교해 **대표를 확정**해야 한다(중복 검수 화면).
  P2 미보정 49 — 보정이 아예 없다. **대표 1장을 보정**하면 된다(에디터).
  P3 확인 70 — 이미 사람 보증이 있다(단일 57 · 일치 13). 눈으로 확인만.

산출:
  docs/TESTSET_WORKLIST.md          클릭 가능한 작업표(링크 포함)
  data/staging/testset_worklist.csv 한 줄 = 한 그룹

원칙: **읽기 전용.** 어떤 보정본도 고치지 않는다.

usage:
  python scripts/testset_worklist.py
  python scripts/testset_worklist.py --base http://localhost:8600
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

PRIO = {"conflict": ("P1", "불일치 — 변종 비교 후 대표 확정"),
        "none": ("P2", "미보정 — 대표 1장 보정"),
        "single": ("P3", "확인 — 사람 보증 있음(단일)"),
        "agree": ("P3", "확인 — 사람 보증 있음(사본 일치)")}


def load_ids(path):
    d = json.load(open(path, encoding="utf-8"))
    if isinstance(d, dict):
        for k in ("ids", "plan_ids", "kept", "items"):
            if isinstance(d.get(k), list):
                return set(map(str, d[k]))
        return set(map(str, d))
    return set(map(str, d))


def bucket(key, mod=20, test=1, val=1):
    h = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % mod
    return "test" if h < test else ("val" if h < test + val else "train")


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
    ap.add_argument("--base", default="https://plan2graph.aines.kr/editor",
                    help="에디터 base URL (터널이면 http://localhost:8600)")
    ap.add_argument("--md", default=os.path.join(ROOT, "docs", "TESTSET_WORKLIST.md"))
    ap.add_argument("--csv", default=os.path.join(STAGING, "testset_worklist.csv"))
    args = ap.parse_args()

    idx = json.load(open(args.index, encoding="utf-8"))
    groups, sig_of = idx["groups"], idx["sig_of"]
    gated = load_ids(args.gated)
    corrected = set(f[:-5] for f in os.listdir(args.edits) if f.endswith(".json"))

    # 서명해시 split 기준 test 고유 그룹
    test_ids = [m for m in gated if bucket(sig_of.get(m, m)) == "test"]
    sigs = sorted({sig_of.get(m, m) for m in test_ids})

    rows = []
    for s in sigs:
        g = groups.get(s) or {}
        st = g.get("status", "none")
        members = g.get("members") or [s]
        gm = sorted(m for m in members if m in gated)
        done = [m for m in gm if m in corrected]
        # 대표 = 보정된 것이 있으면 그중 첫 번째, 없으면 gated 첫 번째
        rep = (sorted(done) or gm or sorted(members))[0]
        vs = sorted(g.get("variants") or [], key=lambda v: -v["count"])
        rows.append({
            "prio": PRIO.get(st, ("P3", "확인"))[0],
            "status": st,
            "할일": PRIO.get(st, ("P3", "확인"))[1],
            "group": s,
            "대표도면": rep,
            "사본": len(members),
            "test장수": len(gm),
            "보정됨": len(done),
            "변종": len(vs),
            "방수": "/".join(str(v["n_rooms"]) for v in vs) if vs else "",
            "변종대표": " | ".join(v["members"][0] for v in vs[:6]),
            "차이": " || ".join(roles_diff(vs[0]["roles"], v["roles"]) for v in vs[1:]) if len(vs) > 1 else "",
        })
    rows.sort(key=lambda r: (r["prio"], -r["test장수"], r["대표도면"]))

    with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    cnt = collections.Counter(r["status"] for r in rows)
    base = args.base.rstrip("/")
    L = []
    A = L.append
    A("# 평가셋 작업표 — 고유 평면 **%d개**" % len(rows))
    A("")
    A("> 생성 = `scripts/testset_worklist.py`(읽기 전용). 근거 = [DATA.md §8.13](DATA.md) ·")
    A("> 범위 = **서명해시 split의 test**(gated %d장 → 고유 %d개)." % (len(test_ids), len(rows)))
    A("> **사본은 같은 평면이다 — 대표 1장만 보정하면 평가 정답지가 선다.**")
    A("> 에디터: `%s/` · 중복 검수: `%s/dedup`" % (base, base))
    A("")
    A("| 상태 | 그룹 | 뜻 |")
    A("|---|--:|---|")
    A("| 보정 없음 | **%d** | 대표 1장 보정 |" % cnt["none"])
    A("| 사본끼리 불일치 | **%d** | 변종 비교 후 대표 확정 |" % cnt["conflict"])
    A("| 단일 보정 | %d | 사람 보증 있음 — 확인만 |" % cnt["single"])
    A("| 사본끼리 일치 | %d | 사람 보증 있음 — 확인만 |" % cnt["agree"])
    A("")
    A("---")
    for p, title, note in (
        ("P1", "P1 — 사본끼리 불일치 (변종 비교 후 대표 확정)",
         "중복 검수 화면이 변종을 나란히 보여준다. 대표를 고르면 `전파`로 형제에 복사된다."),
        ("P2", "P2 — 보정 없음 (대표 1장 보정)",
         "에디터에서 역할·기하·축척을 보정한다. 같은 그룹의 나머지 사본은 평가에 쓰지 않는다."),
        ("P3", "P3 — 이미 사람 보증 있음 (눈으로 확인만)",
         "틀린 게 보이면 고치고, 아니면 넘어간다. 이 70개는 안 봐도 평가셋이 성립한다."),
    ):
        sub = [r for r in rows if r["prio"] == p]
        A("")
        A("## %s · %d개" % (title, len(sub)))
        A("")
        A(note)
        A("")
        if p == "P1":
            A("| # | 사본 | 변종 | 방 수 | 차이 | 열기 |")
            A("|--:|--:|--:|---|---|---|")
            for i, r in enumerate(sub, 1):
                # 표 셀 안의 '|'는 마크다운 표를 깬다 — '/'로 바꾼다(CSV는 원본 유지)
                diff = (r["차이"] or "").replace(" || ", " / ").replace("|", "/")
                A("| %d | %d | %d | %s | %s | [중복 검수](%s/dedup?group=%s) · [대표 에디터](%s/?gid=%s) |"
                  % (i, r["사본"], r["변종"], r["방수"], diff[:70] or "—",
                     base, r["group"], base, r["대표도면"]))
        else:
            A("| # | 도면 번호(대표) | 사본 | 열기 |")
            A("|--:|---|--:|---|")
            for i, r in enumerate(sub, 1):
                A("| %d | `%s` | %d | [에디터](%s/?gid=%s)%s |"
                  % (i, r["대표도면"], r["사본"], base, r["대표도면"],
                     "" if r["사본"] < 2 else " · [중복](%s/dedup?group=%s)" % (base, r["group"])))
    A("")
    A("---")
    A("관련: [DATA.md §8.13](DATA.md) · [ROADMAP.md A-2](ROADMAP.md) · "
      "[DEDUP_STATUS.md](DEDUP_STATUS.md) · 측정 `scripts/dedup_split_impact.py`")
    with open(args.md, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")

    print("고유 %d개 (P1 %d · P2 %d · P3 %d)"
          % (len(rows), cnt["conflict"], cnt["none"], cnt["single"] + cnt["agree"]))
    print("→ %s" % os.path.relpath(args.md, ROOT))
    print("→ %s" % os.path.relpath(args.csv, ROOT))


if __name__ == "__main__":
    main()
