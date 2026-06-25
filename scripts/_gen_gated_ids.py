"""한국 기하 게이트 ID셋 생성 (게이트③): clean_ids 전수에 snap_split(tol3.5) 적용 후
selfint0 ∧ core-single ∧ overlap<5% 통과만 → korean_gated_ids.json.
주변부(연결성 제외)=발코니·실외기실·테라스·기타. 토큰화 대상 한정용(Parsed 40k→~10k).
"""
import sys, json, glob, os
sys.path.insert(0, "src")
from plan2graph import wallcycle_codec as wc
from shapely.geometry import Polygon
from shapely.ops import unary_union

TOL = 3.5
PERIPH = {"발코니", "실외기실", "테라스", "기타"}

ids = json.load(open("data/staging/korean_clean_ids.json", encoding="utf-8"))
idset = set(ids["ids"] if isinstance(ids, dict) else ids)
files = [f for f in sorted(glob.glob("data/staging/corrected/graphs/APT_*.json"))
         if os.path.basename(f)[:-5] in idset]
print("clean_ids 그래프 %d개 게이트 검사 시작 (tol=%.1f)" % (len(files), TOL), flush=True)

passed = []
n = 0
for f in files:
    n += 1
    if n % 2000 == 0:
        print("  ...%d/%d  통과 %d" % (n, len(files), len(passed)), flush=True)
    try:
        g = json.load(open(f, encoding="utf-8"))
    except Exception:
        continue
    if len(g.get("rooms") or {}) < 2:
        continue
    try:
        c = wc.canonicalize(g, grid=128)
        c = wc.snap_split(c, tol=TOL)
        G = wc.canon_to_graph(c)
    except Exception:
        continue
    allp, core, bad, tot = [], [], 0, 0.0
    for rm in G["rooms"].values():
        p = rm.get("polygon") or []
        if len(p) < 3:
            bad += 1
            continue
        P = Polygon(p)
        if not P.is_valid:
            bad += 1
            continue
        if P.area <= 1:
            continue
        allp.append(P)
        tot += P.area
        if rm.get("role") not in PERIPH:
            core.append(P)
    if bad > 0 or not core:
        continue
    uc = unary_union(core)
    ua = unary_union(allp)
    ov = (tot - ua.area) / tot if tot > 0 else 1.0
    if uc.geom_type != "MultiPolygon" and ov < 0.05:
        passed.append(os.path.basename(f)[:-5])

out = "data/staging/korean_gated_ids.json"
json.dump({"ids": passed, "gate": "selfint0 & core-single & overlap<5%",
           "snap_tol": TOL, "periph": sorted(PERIPH),
           "source_pool": "korean_clean_ids", "n_source": len(files)},
          open(out, "w", encoding="utf-8"), ensure_ascii=False)
print("게이트 통과 %d / %d (%.0f%%) → %s" % (len(passed), len(files), 100*len(passed)/len(files), out), flush=True)
