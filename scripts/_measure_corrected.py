"""Corrected(staging/corrected) 실측 — 기록 그대로. Parsed 매니페스트와 별개."""
import json
import re
from collections import Counter
from pathlib import Path
import config
from plan2graph import topoedit as te, dataset_status as ds

base = config.DATA_DIR / "staging" / "corrected"
G = te.GRAPHS_DIR
print("Corrected 폴더:", base)
print("  하위 항목:", sorted(p.name for p in base.iterdir()) if base.exists() else "(없음)")
print("  GRAPHS_DIR:", G, "exists=", G.exists())
mani = base / "_manifest.json"
print("  _manifest.json 있음?:", mani.exists())
print()

files = sorted(G.glob("*.json")) if G.exists() else []
print("그래프 json(=세대) 파일수:", format(len(files), ","))
if files:
    g0 = json.loads(files[0].read_text(encoding="utf-8"))
    print("그래프 1건 필드 키:", sorted(g0.keys()))
    meta_keys = sorted((g0.get("meta") or {}).keys())
    print("  meta 키:", meta_keys)
    print("  plan_id 예:", g0.get("plan_id"), "| corrected:", g0.get("corrected"),
          "| validation.passed:", (g0.get("validation") or {}).get("passed"))
print()

# 세대(파일) → 도면(plan_id에서 _u\d+ 제거)
draws = Counter()
units_per_draw = Counter()
corrected_n = 0
for f in files:
    try:
        g = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        continue
    pid = g.get("plan_id") or f.stem
    drw = re.sub(r"_u\d+$", "", pid)
    units_per_draw[drw] += 1
    if g.get("corrected"):
        corrected_n += 1
print("고유 도면(시트) 수:", format(len(units_per_draw), ","))
print("총 세대(그래프) 수:", format(sum(units_per_draw.values()), ","))
print("corrected=true(사람 보정완료) 세대:", format(corrected_n, ","))
print("도면당 세대수 분포 top5:", units_per_draw.most_common(5))
print()
# 중복 개념이 G에 있나? — 같은 fingerprint(지문)로 묶이는 중복 entry 존재 여부
fps = [g0k for g0k in []]
dup_like = sum(1 for f in files if "dup" in f.stem.lower())
print("파일명에 dup 흔적:", dup_like, "(0이면 G엔 중복 엔트리 개념 없음 — 고유 원본만 변환)")
print()
print("=== corrected_status(정본 G 회계) ===")
a = ds.corrected_status(G)
print("  세대: use", a["use"], "/ fix", a["fix"], "/ excl", a["excl"], "/ done", a["done"], "= total", a["total"])
print("  도면:", a["draw"], "| n_drawings", a.get("n_drawings"))
