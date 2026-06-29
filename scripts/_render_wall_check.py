"""WALL-repair 후 convex-탈락(=L자?) 샘플 vs strict-통과 샘플 렌더 비교 (CPU).
convex 완화가 정당한지 육안 검증.
"""
import io, json, math, sys
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
from PIL import Image, ImageDraw
from plan2graph import cadrender
from plan2graph.graph_repair import repair_graph
import diag_placement as DP
import render_geomclean as RG
from survey_outline import footprint_metrics

corpus = json.load(open("data/staging/repair_corpus.json", encoding="utf-8"))


def to_g(rec):
    return {"rooms": {k: {"polygon": [list(p) for p in v["polygon"]], "role": v.get("role")}
                      for k, v in rec["rooms"].items()}}


def render(g):
    geom = cadrender.autocorrect(cadrender.from_geomgraph(g))
    geom.walls = [w for w in geom.walls if not RG._seg_diag(w.seg)]
    return Image.open(io.BytesIO(cadrender.render_png(geom))).convert("RGB")


convex_fail, strict_pass = [], []
for rec in corpus:
    g = to_g(rec)
    repair_graph(g, drop_bad=True, declash="wall")
    try:
        m = DP.metrics(g)
    except Exception:
        continue
    loose = m["selfint_rooms"] == 0 and m["overlap_frac"] < 0.25 and m["span_ratio"] < 8
    fp = footprint_metrics(g)
    if not fp:
        continue
    base = (loose and RG._min_unique_verts(g) >= 4 and not RG._has_diagonal(g)
            and fp["pieces"] == 1 and fp["fill"] >= RG.FP_FILL_MIN)
    if not base:
        continue
    if fp["convex"] >= RG.FP_CONVEX_MIN and len(strict_pass) < 9:
        strict_pass.append(render(g))
    elif fp["convex"] < RG.FP_CONVEX_MIN and len(convex_fail) < 9:
        convex_fail.append((render(g), round(fp["convex"], 2)))

print("convex_fail=%d strict_pass=%d" % (len(convex_fail), len(strict_pass)))


def panel(imgs, title, captions=None, cell=300, cols=3):
    rows = max(1, math.ceil(max(1, len(imgs)) / cols))
    P = Image.new("RGB", (cols * cell, rows * cell + 34), (255, 255, 255))
    d = ImageDraw.Draw(P)
    d.rectangle([0, 0, cols * cell, 34], fill=(255, 244, 224))
    d.text((8, 11), title, fill=(140, 70, 0))
    for i, im in enumerate(imgs):
        P.paste(im.resize((cell, cell)), ((i % cols) * cell, 34 + (i // cols) * cell))
        if captions:
            d.text(((i % cols) * cell + 6, 34 + (i // cols) * cell + 6), captions[i], fill=(180, 0, 0))
    return P


left = panel([x[0] for x in convex_fail], "convex<0.75 FAIL (L자? 뭉개짐?) — convex값 표기",
             captions=[str(x[1]) for x in convex_fail])
right = panel(strict_pass, "strict PASS (convex>=0.75, 직사각형)")
gap = 20
combo = Image.new("RGB", (left.width + gap + right.width, max(left.height, right.height)), (255, 255, 255))
combo.paste(left, (0, 0)); combo.paste(right, (left.width + gap, 0))
combo.save("docs/runs/repair_wall_convex_check.png")
print("-> docs/runs/repair_wall_convex_check.png (%dx%d)" % combo.size)
