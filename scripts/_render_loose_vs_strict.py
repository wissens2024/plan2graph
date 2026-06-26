"""느슨한 clean(헤드라인 65%) vs strict clean(48%) 나란히 — 같은 g128 production.
왼쪽 = loose 통과 ∧ strict 탈락 (헤드라인이 clean으로 세지만 사선/뭉개짐).
오른쪽 = strict 통과 (진짜 도면).
둘 다 동일 렌더 경로(autocorrect+render_png) → 차이는 오직 '어떤 샘플이 통과되나'.
"""
import io, json, math, sys
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
import torch
from PIL import Image, ImageDraw
from plan2graph import cadrender, wallcycle_codec as wc
from plan2graph.generators.wall_cycle import WallCycleLM, make_constraint_mask
import diag_placement as DP
import render_geomclean as RG

CKPT = "ckpts/korplan_ar_k_gated_ft_ep130.pt"
VOCAB = "data/staging/tokens_korean_gated/vocab.json"
NEED = 12
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


def render_g(g):
    geom = cadrender.autocorrect(cadrender.from_geomgraph(g))
    return Image.open(io.BytesIO(cadrender.render_png(geom))).convert("RGB")


mangled, clean = [], []
guard = 0
while (len(mangled) < NEED or len(clean) < NEED) and guard < 120:
    guard += 1
    out = model.generate(torch.tensor([pre] * 8, device=dev), max_new=650,
                         eos=eos, temperature=1.0, top_k=40, mask_fn=mask_fn)
    for row in out.tolist():
        row = row[:row.index(eos) + 1] if eos in row else row
        try:
            g = wc.canon_to_graph(wc.decode(row, vocab))
        except Exception:
            continue
        if len(g.get("rooms") or {}) < 2:
            continue
        for r in g["rooms"].values():
            if r.get("polygon"):
                r["polygon"] = wc.rectify_diagonals(r["polygon"])
        m = DP.metrics(g)
        loose = m["selfint_rooms"] == 0 and m["overlap_frac"] < 0.25 and m["span_ratio"] < 8
        if not loose:
            continue
        ok, _ = RG.is_clean(g)
        try:
            img = render_g(g)
        except Exception:
            continue
        if ok and len(clean) < NEED:
            clean.append(img)
        elif (not ok) and len(mangled) < NEED:
            mangled.append(img)

print("collected mangled=%d clean=%d (guard=%d)" % (len(mangled), len(clean), guard), flush=True)


def panel(imgs, title, cell=300, cols=3):
    rows = max(1, math.ceil(NEED / cols))
    W, H = cols * cell, rows * cell + 40
    P = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(P)
    d.rectangle([0, 0, W, 40], fill=(245, 230, 230) if "LOOSE" in title else (230, 245, 230))
    d.text((10, 14), title, fill=(150, 0, 0) if "LOOSE" in title else (0, 110, 0))
    for i, im in enumerate(imgs[:NEED]):
        P.paste(im.resize((cell, cell)), ((i % cols) * cell, 40 + (i // cols) * cell))
    return P


left = panel(mangled, "LOOSE-clean PASS but STRICT FAIL  (counted in headline 65%, but mangled/diagonal)")
right = panel(clean, "STRICT-clean PASS  (real floorplan, 48%)")
gap = 24
combo = Image.new("RGB", (left.width + gap + right.width, max(left.height, right.height)), (255, 255, 255))
combo.paste(left, (0, 0)); combo.paste(right, (left.width + gap, 0))
combo.save("docs/runs/montage_loose_vs_strict_g128.png")
print("-> docs/runs/montage_loose_vs_strict_g128.png  (%dx%d)" % combo.size, flush=True)
