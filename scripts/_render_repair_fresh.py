"""fresh 생성 → repair(WALL declash) → 렌더. convex-탈락(L자?) vs strict-통과 육안.
완전한 그래프라 벽이 제대로 렌더됨(코퍼스와 달리).
"""
import io, json, math, sys
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
import torch
from PIL import Image, ImageDraw
from plan2graph import cadrender, wallcycle_codec as wc
from plan2graph.generators.wall_cycle import WallCycleLM, make_constraint_mask
from plan2graph.graph_repair import repair_graph
import diag_placement as DP
import render_geomclean as RG
from survey_outline import footprint_metrics

CKPT = "ckpts/korplan_ar_k_gated_ft_ep130.pt"
VOCAB = "data/staging/tokens_korean_gated/vocab.json"
dev = "cuda" if torch.cuda.is_available() else "cpu"
vocab = json.load(open(VOCAB, encoding="utf-8"))
ck = torch.load(CKPT, map_location=dev); a = ck["args"]
model = WallCycleLM(vocab["size"], d_model=a["d_model"], n_layer=a["n_layer"],
                    n_head=a.get("n_head", 8), max_len=a["max_len"], dim_ff=a.get("dim_ff")).to(dev)
model.load_state_dict(ck["model"]); model.eval()
mask_fn = make_constraint_mask(vocab, orthogonal=True)
bos, eos = wc.V.BOS, wc.V.EOS
pre = [bos, vocab["meta"] + 0, vocab["meta"] + len(wc.COUNTRIES) + 0,
       vocab["meta"] + len(wc.COUNTRIES) + len(wc.HOUSING) + 0,
       vocab["scope"] + 0, vocab["units"] + 1]
torch.manual_seed(42)


def render(g):
    geom = cadrender.autocorrect(cadrender.from_geomgraph(g))
    geom.walls = [w for w in geom.walls if not RG._seg_diag(w.seg)]
    return Image.open(io.BytesIO(cadrender.render_png(geom))).convert("RGB")


NEED = 9
cfail, spass = [], []
guard = 0
while (len(cfail) < NEED or len(spass) < NEED) and guard < 80:
    guard += 1
    out = model.generate(torch.tensor([pre] * 8, device=dev), max_new=650,
                         eos=eos, temperature=1.0, top_k=40, mask_fn=mask_fn)
    for row in out.tolist():
        if len(cfail) >= NEED and len(spass) >= NEED:
            break
        row = row[:row.index(eos) + 1] if eos in row else row
        try:
            g = wc.canon_to_graph(wc.decode(row, vocab))
        except Exception:
            continue
        if len(g.get("rooms") or {}) < 2:
            continue
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
        try:
            img = render(g)
        except Exception:
            continue
        if fp["convex"] >= RG.FP_CONVEX_MIN and len(spass) < NEED:
            spass.append(img)
        elif fp["convex"] < RG.FP_CONVEX_MIN and len(cfail) < NEED:
            cfail.append(img)

print("convex_fail=%d strict_pass=%d (guard=%d)" % (len(cfail), len(spass), guard), flush=True)


def panel(imgs, title, color, cell=300, cols=3):
    rows = max(1, math.ceil(max(1, len(imgs)) / cols))
    P = Image.new("RGB", (cols * cell, rows * cell + 32), (255, 255, 255))
    d = ImageDraw.Draw(P); d.rectangle([0, 0, cols * cell, 32], fill=color)
    d.text((8, 10), title, fill=(60, 30, 0))
    for i, im in enumerate(imgs):
        P.paste(im.resize((cell, cell)), ((i % cols) * cell, 32 + (i // cols) * cell))
    return P


left = panel(cfail, "convex<0.75 (strict 탈락) — L자/오목 외곽인가?", (255, 235, 215))
right = panel(spass, "strict PASS (convex>=0.75, 직사각 외곽)", (220, 245, 220))
gap = 20
combo = Image.new("RGB", (left.width + gap + right.width, max(left.height, right.height)), (255, 255, 255))
combo.paste(left, (0, 0)); combo.paste(right, (left.width + gap, 0))
combo.save("docs/runs/repair_fresh_convex_check.png")
print("-> docs/runs/repair_fresh_convex_check.png", combo.size, flush=True)
