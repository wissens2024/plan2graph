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
SIG, SRC = sys.argv[1], sys.argv[2]
idx = dedup_review.load_index(CTX)
grp = (idx.get("groups") or {}).get(SIG)
if not grp:
    cand = [k for k in (idx.get("groups") or {}) if k.startswith(SIG[:8])]
    SIG = cand[0] if len(cand) == 1 else None
    grp = (idx.get("groups") or {}).get(SIG)
mem = grp["members"]
tg = [m for m in mem if m != SRC]


def st(p):
    f = os.path.join(CTX["EDITS"], p + ".json")
    if not os.path.exists(f):
        return None
    g = json.load(open(f, encoding="utf-8"))
    return (len(g.get("rooms") or {}), len(g.get("doors") or []),
            len(g.get("windows") or []), len(g.get("edges") or []))


print("그룹 %s · 사본 %d" % (SIG[:8], len(mem)))
r = dedup_review.propagate(CTX, SIG, SRC, mode="unify", force=True)
for k, v in r.items():
    print("   %-14s %s" % (k, v))
a = st(SRC)
print("\n원본 %-34s 방%d 문%d 창%d 엣지%d" % (SRC, *a))
bad = 0
for t in tg:
    b = st(t)
    print("   %-34s 방%d 문%d 창%d 엣지%d %s"
          % (t, b[0], b[1], b[2], b[3], "" if b == a else "  <-- 어긋남"))
    bad += (b != a)
print("어긋남 %d장" % bad)
out = os.path.join(ROOT, "data", "propagated_plans.json")
log = json.load(open(out, encoding="utf-8")) if os.path.exists(out) else []
log.append({"sig": SIG, "source": SRC, "targets": tg, "written": r.get("written"),
            "overwritten": r.get("overwritten"), "backup": r.get("backup")})
json.dump(log, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("전파본 목록 갱신 ->", out)
