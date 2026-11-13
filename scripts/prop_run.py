#!/usr/bin/env python3
"""확정본을 같은 도면인 사본 전부에 전파한다 (사용자 2026-11-13 "동일 도면 모두 전파해줘").

원본은 전부 사장님이 화면에서 직접 보고 확정한 도면이다.
전파본 목록을 남긴다 — 독립 이중 주석(IAA 원자료)은 전파하면 사라지므로
나중에 IAA 를 다시 잴 때 이 목록을 걷어내야 한다([[iaa-from-duplicates]]).
"""
import json, os, sys
ROOT = "/home/ju/plan2graph"
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "src"))
from plan2graph import dedup_review
from plan2graph.topoedit import ROLE_COLOR

ST = os.path.join(ROOT, "data", "staging")
CTX = {"GRAPHS": os.path.join(ST, "parsed", "graphs"),
       "EDITS": os.path.join(ST, "corrected", "edits"),
       "INDEX": os.path.join(ST, "dedup_index.json"),
       "ROLE_COLOR": ROLE_COLOR, "PNG": lambda *a, **k: None}

JOBS = [
    ("b4eea7dbd8a1dd6c", "APT_FP_01613b7f_17446927_u0"),
    ("8a2d9ad0af9b5b47", "APT_FP_43f02133_17446927_u0"),
    ("71fbdaecbc02c06d", "APT_FP_09f1fdb3_17446941_u2"),
    ("688128a70cee9996", "APT_FP_0a142c11_17446941_u1"),
    ("276ca07fdab82a6a", "APT_FP_1314cee9_17438443_u1"),
    ("5e989a225fecfdd8", "APT_FP_396cb308_17447720_u3"),
    ("33261e03", "APT_FP_1314cee9_17438443_u0"),
    ("feadbbc1c77125f2", "APT_FP_091800b0_17446941_u1"),
]
APPLY = "--apply" in sys.argv
idx = dedup_review.load_index(CTX)
groups = idx.get("groups") or {}

# 짧게 적은 sig 는 앞자리로 찾아 채운다
fixed = []
for sig, src in JOBS:
    if sig not in groups:
        cand = [k for k in groups if k.startswith(sig[:8])]
        if len(cand) == 1:
            sig = cand[0]
        else:
            print("%-18s 그룹 못 찾음 (후보 %d)" % (sig[:8], len(cand)))
            continue
    fixed.append((sig, src))

tot = 0
log = []
for sig, src in fixed:
    mem = groups[sig]["members"]
    tg = [m for m in mem if m != src]
    tot += len(tg)
    if not APPLY:
        have = sum(1 for m in tg if os.path.exists(os.path.join(CTX["EDITS"], m + ".json")))
        print("%s  <- %-34s  사본 %d (보정됨 %d · 미보정 %d)"
              % (sig[:8], src, len(mem), have, len(tg) - have))
        continue
    r = dedup_review.propagate(CTX, sig, src, mode="unify", force=True)
    def _n(v):
        return v if isinstance(v, int) else len(v or [])
    w = _n(r.get("written")); o = _n(r.get("overwritten"))
    s = r.get("skipped") or []; e = r.get("errors") or []
    if isinstance(s, int): s = []
    if isinstance(e, int): e = []
    print("%s  <- %-34s  새로 채움 %2d · 덮어씀 %2d · 건너뜀 %2d · 오류 %d"
          % (sig[:8], src, w, o, len(s), len(e)))
    for row in s:
        print("      건너뜀 %s" % (row,))
    for row in e:
        print("      오류   %s" % (row,))
    log.append({"sig": sig, "source": src, "targets": tg,
                "written": r.get("written"), "overwritten": r.get("overwritten"),
                "skipped": s, "errors": e, "backup": r.get("backup")})

if not APPLY:
    print("\n그룹 %d · 전파 대상 %d장 · [드라이런] --apply 로 실행" % (len(fixed), tot))
else:
    out = os.path.join(ROOT, "data", "propagated_plans.json")
    old = []
    if os.path.exists(out):
        try:
            old = json.load(open(out, encoding="utf-8"))
        except Exception:
            old = []
    json.dump(old + log, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n전파본 목록 -> %s  (IAA 재측정 때 걷어내라)" % out)
