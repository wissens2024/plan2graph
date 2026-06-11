"""AI-Hub manifest 실측 — 추론 없이 기록 그대로 집계."""
import json
from collections import Counter, defaultdict
import config

mp = config.DATA_DIR / "staging" / "aihub" / "manifest.jsonl"
rows = [json.loads(l) for l in mp.read_text(encoding="utf-8").splitlines() if l.strip()]
print("매니페스트:", mp)
print("총 행수(받은 엔트리):", format(len(rows), ","))
print("행 1개 필드 키:", sorted(rows[0].keys()))
print()

FIELDS = ("file_id", "fingerprint", "disposition", "reason", "house", "dup_of")
def show_example(disp, reason):
    for r in rows:
        if r.get("disposition") == disp and r.get("reason") == reason:
            keep = {k: r.get(k) for k in FIELDS if k in r}
            keep["n_graph_ids"] = len(r.get("graph_ids") or [])
            keep["graph_ids_head"] = (r.get("graph_ids") or [])[:2]
            print("[" + disp + "/" + str(reason) + "] 예:", json.dumps(keep, ensure_ascii=False))
            return
    print("[" + disp + "/" + str(reason) + "] 해당 행 없음")

for d, r in [("use", "dual"), ("use", "v2v_str_recovered"), ("excl", "duplicate"),
             ("excl", "nonfp"), ("excl", "objocr"), ("fix", "convert_failed")]:
    show_example(d, r)
print()

# 처분/사유별: 행수 · graph보유행수 · 세대합(Σ len graph_ids)
agg = defaultdict(lambda: [0, 0, 0])
for r in rows:
    k = (r.get("disposition"), r.get("reason"))
    n = len(r.get("graph_ids") or [])
    agg[k][0] += 1
    agg[k][1] += (1 if n else 0)
    agg[k][2] += n
print("처분/사유                |    행수 | graph보유행 | 세대합(Σgraph_ids)")
for k in sorted(agg, key=lambda x: (str(x[0]), str(x[1]))):
    a = agg[k]
    name = str(k[0]) + "/" + str(k[1])
    print(name.ljust(24) + " | " + format(a[0], ",").rjust(7) + " | "
          + format(a[1], ",").rjust(10) + " | " + format(a[2], ",").rjust(10))
print()

# 중복 실측
dups = [r for r in rows if r.get("reason") == "duplicate"]
fp_g = {r.get("fingerprint"): len(r.get("graph_ids") or [])
        for r in rows if (r.get("graph_ids") or [])}
print("중복 행수:", format(len(dups), ","))
print("  중복행이 graph_ids 직접 보유:", sum(1 for r in dups if (r.get("graph_ids") or [])))
print("  중복행 dup_of 보유:", sum(1 for r in dups if r.get("dup_of")))
print("  중복행 dup_of가 가리키는 고유 원본 수:", format(len({r.get("dup_of") for r in dups}), ","))
print("  중복행 원본 세대수 합(Σ fp_g[dup_of]):", format(sum(fp_g.get(r.get("dup_of"), 0) for r in dups), ","))
print("  한 원본당 중복행수 top5:", Counter(r.get("dup_of") for r in dups).most_common(5))
print()
print("고유 변환 세대(Σ graph_ids, 모든 행):", format(sum(len(r.get("graph_ids") or []) for r in rows), ","))
print("graph_ids 보유 행(=실변환 도면):", format(sum(1 for r in rows if (r.get("graph_ids") or [])), ","))
