"""미보정 집단 vs 보정 집단 — 선택 편향 검정.

알바가 쉬운 것부터 골랐다면 미보정은 체계적으로 더 어렵고, 규칙을 그대로
적용하면 안 된다. 두 집단을 동일 지표로 재어 차이를 드러낸다.
"""
from __future__ import annotations
import json, os, sys

sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
import diag_placement as DP
from survey_outline import footprint_metrics
from render_geomclean import _min_unique_verts, _has_diagonal

PARSED = "data/staging/parsed/graphs"
EDITS = "data/staging/corrected/edits"
GATED = "data/staging/korean_gated_ids.json"
CLEAN = "data/staging/korean_clean_ids.json"
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/cmp_unedited.json"
ASPECT, FILL, CONVEX, OVER = 10.0, 0.60, 0.70, 0.25


def load_ids(p):
    if not os.path.exists(p):
        return set()
    d = json.load(open(p, encoding="utf-8"))
    if isinstance(d, dict):
        for k in ("ids", "plan_ids", "kept", "items"):
            if isinstance(d.get(k), list):
                return set(str(x) for x in d[k])
        return set(str(x) for x in d)
    return set(str(x) for x in d)


def judge(g):
    rooms = g.get("rooms") or {}
    if len(rooms) < 2:
        return None
    try:
        m = DP.metrics(g); fp = footprint_metrics(g)
    except Exception:
        return None
    if not fp:
        return None
    ok = (m["selfint_rooms"] == 0 and _min_unique_verts(g) >= 4 and not _has_diagonal(g)
          and fp["pieces"] == 1 and fp["fill"] >= FILL and fp["convex"] >= CONVEX
          and m["overlap_frac"] < OVER and m["span_ratio"] < ASPECT)
    return {"drawable": bool(ok), "selfint0": m["selfint_rooms"] == 0,
            "pieces1": fp["pieces"] == 1}


gated, clean = load_ids(GATED), load_ids(CLEAN)
pid_p = sorted(e.name[:-5] for e in os.scandir(PARSED) if e.name.endswith(".json"))
pid_e = set(e.name[:-5] for e in os.scandir(EDITS) if e.name.endswith(".json"))
print("[pool] parsed=%d edited=%d unedited=%d" % (len(pid_p), len(pid_e),
      len(pid_p) - len(pid_e & set(pid_p))), flush=True)

G = {}
for grp in ("edited", "unedited"):
    G[grp] = {"n": 0, "house": {}, "valid_passed": 0, "in_clean": 0, "in_gated": 0,
              "rooms": [], "doors": [], "wins": [], "entr": {},
              "drawable": 0, "judged": 0, "selfint0": 0, "pieces1": 0,
              "door_w": 0, "door_tot": 0, "win_w": 0, "win_tot": 0, "has_scale": 0}

for n, pid in enumerate(pid_p, 1):
    try:
        g = json.load(open("%s/%s.json" % (PARSED, pid), encoding="utf-8"))
    except Exception:
        continue
    grp = "edited" if pid in pid_e else "unedited"
    A = G[grp]
    A["n"] += 1
    ht = g.get("house") or (g.get("meta") or {}).get("house_type") or "?"
    A["house"][ht] = A["house"].get(ht, 0) + 1
    A["valid_passed"] += int(bool((g.get("validation") or {}).get("passed")))
    A["in_clean"] += int(pid in clean); A["in_gated"] += int(pid in gated)
    rooms = g.get("rooms") or {}
    A["rooms"].append(len(rooms))
    doors = g.get("doors") or []; wins = g.get("windows") or []
    A["doors"].append(len(doors)); A["wins"].append(len(wins))
    A["door_tot"] += len(doors); A["win_tot"] += len(wins)
    A["door_w"] += sum(1 for d in doors if d.get("width_px"))
    A["win_w"] += sum(1 for w in wins if w.get("width_px"))
    A["has_scale"] += int(bool(g.get("scale_mm_per_px")))
    ne = sum(1 for r in rooms.values() if (r.get("role") or r.get("base")) == "현관")
    key = "0" if ne == 0 else ("1" if ne == 1 else "2+")
    A["entr"][key] = A["entr"].get(key, 0) + 1
    j = judge(g)
    if j:
        A["judged"] += 1; A["drawable"] += int(j["drawable"])
        A["selfint0"] += int(j["selfint0"]); A["pieces1"] += int(j["pieces1"])
    if n % 5000 == 0:
        print("  ...%d/%d" % (n, len(pid_p)), flush=True)


def med(xs):
    xs = sorted(xs); return xs[len(xs) // 2] if xs else None


out = {}
for grp in ("edited", "unedited"):
    A = G[grp]
    N = A["n"] or 1; J = A["judged"] or 1
    out[grp] = {
        "n": A["n"],
        "house": dict(sorted(A["house"].items(), key=lambda kv: -kv[1])),
        "pct_valid_passed": 100.*A["valid_passed"]/N,
        "pct_in_clean": 100.*A["in_clean"]/N, "pct_in_gated": 100.*A["in_gated"]/N,
        "rooms_median": med(A["rooms"]), "rooms_mean": sum(A["rooms"])/N,
        "doors_median": med(A["doors"]), "wins_median": med(A["wins"]),
        "entrance": A["entr"],
        "pct_entrance_2plus": 100.*A["entr"].get("2+", 0)/N,
        "judged": A["judged"], "pct_drawable": 100.*A["drawable"]/J,
        "pct_selfint0": 100.*A["selfint0"]/J, "pct_pieces1": 100.*A["pieces1"]/J,
        "pct_door_width": (100.*A["door_w"]/A["door_tot"]) if A["door_tot"] else None,
        "pct_win_width": (100.*A["win_w"]/A["win_tot"]) if A["win_tot"] else None,
        "pct_has_scale": 100.*A["has_scale"]/N,
    }
json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print("=" * 76, flush=True)
hdr = "%-22s %>12s %>12s"
for k, lbl in (("n", "도면 수"), ("pct_in_clean", "clean 포함%"), ("pct_in_gated", "gated 포함%"),
               ("pct_valid_passed", "validation통과%"), ("rooms_mean", "방 평균"),
               ("rooms_median", "방 중앙"), ("doors_median", "문 중앙"),
               ("wins_median", "창 중앙"), ("pct_entrance_2plus", "현관2+%(층 추정)"),
               ("pct_drawable", "drawable%"), ("pct_selfint0", "자기교차0%"),
               ("pct_pieces1", "1조각%"), ("pct_door_width", "문폭보유%"),
               ("pct_win_width", "창폭보유%"), ("pct_has_scale", "축척보유%")):
    a, b = out["edited"][k], out["unedited"][k]
    fa = ("%12.1f" % a) if isinstance(a, float) else ("%12s" % a)
    fb = ("%12.1f" % b) if isinstance(b, float) else ("%12s" % b)
    print("%-22s %s %s" % (lbl, fa, fb), flush=True)
print("(좌=보정됨, 우=미보정)", flush=True)
print("house 보정됨 :", json.dumps(out["edited"]["house"], ensure_ascii=False), flush=True)
print("house 미보정 :", json.dumps(out["unedited"]["house"], ensure_ascii=False), flush=True)
print("[json]", OUT, flush=True)
