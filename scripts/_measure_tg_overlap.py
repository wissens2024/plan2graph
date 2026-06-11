"""T 매니페스트 ↔ G graphs 도면집합 대조 — 지문 충돌 없이 처분 집합으로 실측."""
import json
import re
from collections import Counter, defaultdict
import config
from plan2graph import topoedit as te

mp = config.DATA_DIR / "staging" / "aihub" / "manifest.jsonl"
rows = [json.loads(l) for l in mp.read_text(encoding="utf-8").splitlines() if l.strip()]

# 지문 → 그 지문을 가진 모든 행의 (disp,reason) 리스트 (충돌 없이 전부 수집)
fp_disps = defaultdict(list)
fp_drawid = defaultdict(set)
for r in rows:
    fp_disps[r.get("fingerprint")].append((r.get("disposition"), r.get("reason")))
    if r.get("drawing_id"):
        fp_drawid[r.get("fingerprint")].add(r.get("drawing_id"))
print("매니페스트 고유 지문수:", format(len(fp_disps), ","))
print("  지문당 행수 분포 top5:", Counter(len(v) for v in fp_disps.values()).most_common(5))

G = te.GRAPHS_DIR
gdraws = {re.sub(r"_u\d+$", "", f.stem) for f in G.glob("*.json")}


def fp_of(draw):
    m = re.match(r"^[A-Z]+_FP_(.+)$", draw)
    return m.group(1) if m else draw


gfps = {fp_of(d) for d in gdraws}
print("\nG 고유 도면:", format(len(gdraws), ","), "| G 고유 지문:", format(len(gfps), ","))

g_use = g_only_dup = g_only_fix = g_notin = g_other = 0
example_only_dup = []
for fp in gfps:
    ds = fp_disps.get(fp)
    if not ds:
        g_notin += 1
        continue
    disps = {d for d, _ in ds}
    reasons = {r for _, r in ds}
    if "use" in disps:
        g_use += 1
    elif reasons == {"duplicate"}:
        g_only_dup += 1
        if len(example_only_dup) < 3:
            example_only_dup.append((fp, ds))
    elif disps == {"fix"}:
        g_only_fix += 1
    else:
        g_other += 1

print("\nG 지문이 T 매니페스트에서:")
print("  use 행을 가진 지문 (= T도 사용한 원본):", format(g_use, ","))
print("  duplicate 행만 있는 지문 (= T가 버린 중복):", format(g_only_dup, ","))
print("  fix 행만:", format(g_only_fix, ","))
print("  기타:", format(g_other, ","))
print("  매니페스트에 없음:", format(g_notin, ","))
for fp, ds in example_only_dup:
    print("   [only-dup 예] fp=", fp, "rows=", ds)
