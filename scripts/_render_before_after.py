"""같은 생성 도면의 repair 전(rectify만) vs 후(make_valid+drop_bad+WALL declash).
repair가 fix하는 것(겹침·자기교차)을 같은 샘플에서 직접 보여줌.
"""
import copy, io, json, math, sys
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
torch.manual_seed(11)


def render(g):
    return Image.open(io.BytesIO(cadrender.render_png(cadrender.from_geomgraph(g)))).convert("RGB")


def drawable(g):
    try:
        ok, _ = RG.is_clean(g)
        return ok
    except Exception:
        return False


def overlap_selfint(g):
    try:
        m = DP.metrics(g)
        return m["overlap_frac"], m["selfint_rooms"]
    except Exception:
        return 0, 0


pairs = []
guard = 0
while len(pairs) < 6 and guard < 60:
    guard += 1
    out = model.generate(torch.tensor([pre] * 8, device=dev), max_new=650,
                         eos=eos, temperature=1.0, top_k=40, mask_fn=mask_fn)
    for row in out.tolist():
        if len(pairs) >= 6:
            break
        row = row[:row.index(eos) + 1] if eos in row else row
        try:
            g0 = wc.canon_to_graph(wc.decode(row, vocab))
        except Exception:
            continue
        if len(g0.get("rooms") or {}) < 2:
            continue
        g_raw = copy.deepcopy(g0)
        for r in g_raw["rooms"].values():
            if r.get("polygon"):
                r["polygon"] = wc.rectify_diagonals(r["polygon"])
        g_rep = copy.deepcopy(g0)
        repair_graph(g_rep, drop_bad=True, declash="wall")
        ov, si = overlap_selfint(g_raw)
        # repair가 살린 케이스(raw 불가 → rep 가능) 또는 raw에 눈에 띄는 결함
        if (not drawable(g_raw) and drawable(g_rep)) or ov > 0.05 or si > 0:
            try:
                pairs.append((render(g_raw), render(g_rep), round(ov, 2), si))
            except Exception:
                pass

print("pairs=%d (guard=%d)" % (len(pairs), guard), flush=True)

cell = 300
rows = max(1, len(pairs))
W = 2 * cell + 30
H = rows * cell + 30
P = Image.new("RGB", (W, H), (255, 255, 255))
d = ImageDraw.Draw(P)
d.rectangle([0, 0, cell, 26], fill=(255, 224, 224)); d.text((8, 8), "RAW (repair 전: rectify만)", fill=(150, 0, 0))
d.rectangle([cell + 30, 0, 2 * cell + 30, 26], fill=(224, 245, 224)); d.text((cell + 38, 8), "REPAIRED (make_valid+declash)", fill=(0, 110, 0))
for i, (raw, rep, ov, si) in enumerate(pairs):
    y = 30 + i * cell
    P.paste(raw.resize((cell, cell)), (0, y))
    P.paste(rep.resize((cell, cell)), (cell + 30, y))
    d.text((6, y + 4), "overlap %.0f%% selfint %d" % (ov * 100, si), fill=(150, 0, 0))
P.save("docs/runs/repair_before_after.png")
print("-> docs/runs/repair_before_after.png", P.size, flush=True)
