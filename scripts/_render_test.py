"""오버레이 렌더 검증 — 카테고리별 1장씩 썸네일 PNG로 저장(육안 확인용)."""
import os
from plan2graph import inspect_excluded as ix

cats = ix.categorize(ix.build_index("Training"))
lblidx = ix.label_index("Training")
out = os.path.expanduser("~/render_test")
os.makedirs(out, exist_ok=True)
for cat in ("spa_only", "str_only", "dual"):
    r = cats[cat][0]
    img = ix.render(r, lblidx, overlay=True)
    img.thumbnail((800, 800))
    p = f"{out}/{cat}_{r['house']}_{r['key']}.png"
    img.save(p)
    npoly = sum(len(ix.load_polys(*lblidx[lt][r['det'][lt]['key']]))
                for lt in ('SPA', 'STR') if lt in r['det'] and r['det'][lt]['key'] in lblidx.get(lt, {}))
    print(f"{cat}: {r['house']} {r['key']} labels={r['labels']} polys={npoly} -> {p}")
print("DONE")
