"""repair 개발용 코퍼스 캡처 + strict-fail 원인 분해.
g128 production에서 N 생성 → 현행 파이프라인(rectify) 적용 → 그래프+strict 서브플래그 저장.
이후 repair는 이 JSON(고정) 위에서 CPU로 반복 개발(GPU 재사용 0).
"""
import io, json, os, sys
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
import torch
from plan2graph import wallcycle_codec as wc
from plan2graph.generators.wall_cycle import WallCycleLM, make_constraint_mask
import diag_placement as DP
import render_geomclean as RG
from survey_outline import footprint_metrics

CKPT = "ckpts/korplan_ar_k_gated_ft_ep130.pt"
VOCAB = "data/staging/tokens_korean_gated/vocab.json"
N = 120
OUT = "data/staging/repair_corpus.json"
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


def subflags(g):
    """현행 rectify 적용된 g의 strict 서브기준 통과 여부."""
    try:
        m = DP.metrics(g)
    except Exception:
        return None
    loose = m["selfint_rooms"] == 0 and m["overlap_frac"] < 0.25 and m["span_ratio"] < 8
    verts_ok = RG._min_unique_verts(g) >= 4
    nodiag = not RG._has_diagonal(g)
    fp = footprint_metrics(g)
    pieces1 = bool(fp) and fp["pieces"] == 1
    fill_ok = bool(fp) and fp["fill"] >= RG.FP_FILL_MIN
    convex_ok = bool(fp) and fp["convex"] >= RG.FP_CONVEX_MIN
    strict = loose and verts_ok and nodiag and pieces1 and fill_ok and convex_ok
    return dict(loose=loose, verts_ok=verts_ok, nodiag=nodiag, pieces1=pieces1,
                fill_ok=fill_ok, convex_ok=convex_ok, strict=strict,
                fill=round(fp["fill"], 3) if fp else None,
                convex=round(fp["convex"], 3) if fp else None,
                pieces=fp["pieces"] if fp else None)


corpus = []
n = 0
while len(corpus) < N and n < N * 4:
    out = model.generate(torch.tensor([pre] * 8, device=dev), max_new=650,
                         eos=eos, temperature=1.0, top_k=40, mask_fn=mask_fn)
    for row in out.tolist():
        if len(corpus) >= N:
            break
        n += 1
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
        f = subflags(g)
        if f is None:
            continue
        # 그래프 직렬화(방 폴리곤+role만 — repair에 충분)
        rooms = {str(k): {"polygon": [[float(x), float(y)] for x, y in (v.get("polygon") or [])],
                          "role": v.get("role")} for k, v in g["rooms"].items()}
        corpus.append(dict(rooms=rooms, flags=f))

json.dump(corpus, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)

# ── 분해 리포트 ──
tot = len(corpus)
loose = [c for c in corpus if c["flags"]["loose"]]
print("=" * 60)
print("코퍼스 %d개 (decoded rooms>=2) → %s" % (tot, OUT))
print("loose clean: %d/%d (%.0f%%)" % (len(loose), tot, 100 * len(loose) / tot))
strict = [c for c in corpus if c["flags"]["strict"]]
print("strict clean: %d/%d (%.0f%%)" % (len(strict), tot, 100 * len(strict) / tot))
print("-" * 60)
print("★ loose 통과(%d개) 중 strict 서브기준 *실패* 수 (이게 repair 타깃):" % len(loose))
for key, label in [("verts_ok", "꼭짓점<4(삼각형)"), ("nodiag", "잔여 사선"),
                   ("pieces1", "비연결(pieces>1, 흩어짐)"), ("fill_ok", "fill<0.60(들쭉)"),
                   ("convex_ok", "convex<0.75(오목/삐죽)")]:
    fails = sum(1 for c in loose if not c["flags"][key])
    print("  %-22s : %3d / %d  (%.0f%%)" % (label, fails, len(loose), 100 * fails / max(len(loose), 1)))
