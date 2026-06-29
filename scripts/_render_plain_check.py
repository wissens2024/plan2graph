"""baseline(rectify) vs WALL-repair plain 렌더 — WALL이 렌더가능 그래프를 만드나 확인."""
import io, json, math, sys
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
from PIL import Image, ImageDraw
from plan2graph import cadrender
from plan2graph.graph_repair import repair_graph

corpus = json.load(open("data/staging/repair_corpus.json", encoding="utf-8"))


def to_g(rec):
    return {"rooms": {k: {"polygon": [list(p) for p in v["polygon"]], "role": v.get("role")}
                      for k, v in rec["rooms"].items()}}


def render(g):
    try:
        return Image.open(io.BytesIO(cadrender.render_png(cadrender.from_geomgraph(g)))).convert("RGB")
    except Exception as e:
        im = Image.new("RGB", (300, 300), (255, 230, 230))
        ImageDraw.Draw(im).text((6, 6), "ERR " + type(e).__name__, fill=(150, 0, 0))
        return im


base_imgs, wall_imgs = [], []
for rec in corpus[:8]:
    gb = to_g(rec); repair_graph(gb, drop_bad=True, declash=False)
    gw = to_g(rec); repair_graph(gw, drop_bad=True, declash="wall")
    base_imgs.append(render(gb)); wall_imgs.append(render(gw))


def panel(imgs, title, cell=300, cols=4):
    rows = max(1, math.ceil(len(imgs) / cols))
    P = Image.new("RGB", (cols * cell, rows * cell + 30), (255, 255, 255))
    d = ImageDraw.Draw(P); d.rectangle([0, 0, cols * cell, 30], fill=(230, 240, 255))
    d.text((8, 9), title, fill=(0, 60, 140))
    for i, im in enumerate(imgs):
        P.paste(im.resize((cell, cell)), ((i % cols) * cell, 30 + (i // cols) * cell))
    return P


top = panel(base_imgs, "baseline (rectify+make_valid+drop_bad)")
bot = panel(wall_imgs, "WALL declash (overlap 제거)")
combo = Image.new("RGB", (top.width, top.height + bot.height + 10), (255, 255, 255))
combo.paste(top, (0, 0)); combo.paste(bot, (0, top.height + 10))
combo.save("docs/runs/repair_plain_check.png")
print("-> docs/runs/repair_plain_check.png", combo.size)
