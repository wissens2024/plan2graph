"""임시 미리보기 콘택트시트 — pool/*.json → 파스텔 무라벨 렌더 그리드 1장."""
import glob
import io
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MPoly
from PIL import Image, ImageDraw, ImageFont
from plan2graph import cadrender as CR

ROLE_GROUP = {"거실": "living", "주방": "kitchen", "안방": "master", "침실": "bedroom",
              "화장실": "bath", "욕실": "bath", "전용화장실": "bath", "전용욕실": "bath",
              "공용욕실": "bath", "파우더룸": "powder", "현관": "entrance", "복도": "corridor",
              "전실": "vestibule", "발코니": "balcony", "베란다": "balcony", "드레스룸": "dress",
              "실외기실": "utility", "다목적공간": "multi", "다용도실": "multi", "팬트리": "multi",
              "알파룸": "alpha", "창고": "storage"}
GC = {"living": "#FBE2CE", "kitchen": "#FCF2C9", "master": "#C9DDF1", "bedroom": "#DFDAEF",
      "bath": "#C9E8DC", "powder": "#E6F0E0", "entrance": "#EADCC4", "corridor": "#EAEAEA",
      "vestibule": "#ECE0EC", "balcony": "#D9EFCE", "dress": "#F3D9E8", "utility": "#DEE5EC",
      "multi": "#E5EFD9", "alpha": "#F0E7D4", "storage": "#E8E4DA", "other": "#EDEDED"}


def render(geom):
    allx, ally = [], []
    for r in geom.rooms:
        for (x, y) in r.polygon:
            allx.append(x); ally.append(y)
    x0, x1, y0, y1 = min(allx), max(allx), min(ally), max(ally)
    W, H = max(x1 - x0, 1), max(y1 - y0, 1)
    fig, ax = plt.subplots(figsize=(3 * (W / H), 3))
    ax.set_aspect("equal"); ax.invert_yaxis(); ax.axis("off")
    for r in geom.rooms:
        if len(r.polygon) >= 3:
            ax.add_patch(MPoly(r.polygon, closed=True,
                               facecolor=GC[ROLE_GROUP.get(r.role, "other")], edgecolor="none", zorder=1))
    for w in geom.walls:
        (a, b), (c, d) = w.seg
        ax.plot([a, c], [b, d], color="#161616", lw=3.4 if w.type == "exterior" else 1.3,
                zorder=3, solid_capstyle="round")
    m = 0.03 * max(W, H)
    ax.set_xlim(x0 - m, x1 + m); ax.set_ylim(y1 + m, y0 - m)
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                                    pad_inches=0.04, facecolor="white"); plt.close(fig)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def main():
    src, out = sys.argv[1], sys.argv[2]
    files = sorted(glob.glob(os.path.join(src, "*.json")))[:30]
    imgs = []
    for f in files:
        import json
        g = json.load(open(f, encoding="utf-8"))
        try:
            imgs.append((os.path.basename(f)[:-5], render(CR.autocorrect(CR.from_geomgraph(g))),
                         len(g.get("rooms") or {})))
        except Exception as e:
            print("skip", f, e)
    cols = 6
    rows = math.ceil(len(imgs) / cols)
    cell, cap = 300, 26
    cw, ch = cell + 12, cell + 12 + cap
    sheet = Image.new("RGB", (cols * cw, rows * ch), "white")
    d = ImageDraw.Draw(sheet)
    try:
        fnt = ImageFont.truetype("C:/Windows/Fonts/malgun.ttf", 18)
    except Exception:
        fnt = ImageFont.load_default()
    for k, (name, im, nr) in enumerate(imgs):
        im = im.copy(); im.thumbnail((cell, cell))
        ox = (k % cols) * cw + (cw - im.width) // 2
        oy = (k // cols) * ch + cap + (cell - im.height) // 2
        sheet.paste(im, (ox, oy))
        d.text(((k % cols) * cw + 4, (k // cols) * ch + 2), f"{name[8:18]} ({nr})", fill="#111", font=fnt)
    sheet.save(out)
    print("saved", out, len(imgs), "plans")


if __name__ == "__main__":
    main()
