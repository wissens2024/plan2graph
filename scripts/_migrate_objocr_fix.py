"""매니페스트 마이그레이션: objocr 행 disposition excl→fix (재빌드 없이 _disposition 변경 반영).
백업 후 패치. T·G 공통 처분모델 정합(objocr=보정대상)."""
import json
import shutil
from collections import Counter
import config

mp = config.DATA_DIR / "staging" / "aihub" / "manifest.jsonl"
rows = [json.loads(l) for l in mp.read_text(encoding="utf-8").splitlines() if l.strip()]

before = Counter(r.get("disposition") for r in rows)
changed = 0
for r in rows:
    if r.get("reason") == "objocr" and r.get("disposition") != "fix":
        r["disposition"] = "fix"
        changed += 1

bak = mp.with_suffix(".jsonl.bak_objocr")
shutil.copy2(mp, bak)
mp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
after = Counter(r.get("disposition") for r in rows)

print("백업:", bak)
print("objocr 행 excl→fix:", format(changed, ","))
print("처분 before:", dict(before))
print("처분 after :", dict(after))
print("검산 합:", format(sum(after.values()), ","))
