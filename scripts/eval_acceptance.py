"""Table 7·8 재실행 — 신프로토콜(n=1000·신임계·RK seed42 ep180).

생성 → rectify → drawable(표현별 임계) ∥ estimate_scale→verify(before)
→ 국소수정(legal_repair·egress_repair·snap_windows) → verify(after)
→ 규칙별 before/after 통과율(Table 7) + 결합 수용률·기대시도·B95(Table 8) → JSON.
"""
from __future__ import annotations
import argparse, json, math, sys
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
import torch
from plan2graph import wallcycle_codec as wc
from plan2graph.generators.wall_cycle import WallCycleLM, make_constraint_mask
from plan2graph.gen_verify import (verify_plan, legal_repair, egress_repair,
                                   snap_windows, estimate_scale)
from plan2graph.rules_legal import RULES
import diag_placement as DP
from survey_outline import footprint_metrics
from render_geomclean import _min_unique_verts, _has_diagonal

RULE_IDS = [r.id for r in RULES]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--vocab", required=True)
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--max-new", type=int, default=650)
    ap.add_argument("--country", type=int, default=0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--aspect-max", type=float, default=10.0)
    ap.add_argument("--fp-fill", type=float, default=0.60)
    ap.add_argument("--fp-convex", type=float, default=0.70)
    ap.add_argument("--json-out", required=True)
    args = ap.parse_args()

    torch.manual_seed(args.seed); torch.cuda.manual_seed_all(args.seed)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    vocab = json.load(open(args.vocab, encoding="utf-8"))
    ck = torch.load(args.ckpt, map_location=dev); a = ck["args"]
    dimff = ck["model"]["blocks.0.mlp.w1.weight"].shape[0]
    model = WallCycleLM(vocab["size"], d_model=a["d_model"], n_layer=a["n_layer"],
                        n_head=a.get("n_head", 8), max_len=a["max_len"], dim_ff=dimff).to(dev)
    model.load_state_dict(ck["model"]); model.eval()
    print(f"[ckpt] {args.ckpt} (epoch {ck.get('epoch')})", flush=True)

    mask_fn = make_constraint_mask(vocab, orthogonal=True)
    bos, eos = wc.V.BOS, wc.V.EOS
    pre = [bos, vocab["meta"] + args.country, vocab["meta"] + len(wc.COUNTRIES) + 0,
           vocab["meta"] + len(wc.COUNTRIES) + len(wc.HOUSING) + 0,
           vocab["scope"] + 0, vocab["units"] + 1]

    N = args.n
    drawable_n = legal_b = legal_a = accepted = decoded = 0
    appl = {r: 0 for r in RULE_IDS}
    okb = {r: 0 for r in RULE_IDS}
    oka = {r: 0 for r in RULE_IDS}

    def tally(v, ok_acc, appl_acc=None):
        viol = set(x["rule"] for x in v["legal_violations"])
        for rid in v["legal_applied"]:
            if rid in ok_acc:
                if appl_acc is not None:
                    appl_acc[rid] += 1
                if rid not in viol:
                    ok_acc[rid] += 1

    ch = 4
    done = 0
    for i in range(0, N, ch):
        k = min(ch, N - i)
        prefix = torch.tensor([pre] * k, device=dev)
        out = model.generate(prefix, max_new=args.max_new, eos=eos,
                             temperature=1.0, top_k=40, mask_fn=mask_fn)
        for row in out.tolist():
            done += 1
            row = row[:row.index(eos) + 1] if eos in row else row
            try:
                g = wc.canon_to_graph(wc.decode(row, vocab))
            except Exception:
                continue
            if len(g.get("rooms") or {}) < 2:
                continue
            decoded += 1
            for r in g["rooms"].values():
                if r.get("polygon"):
                    r["polygon"] = wc.rectify_diagonals(r["polygon"])
            # drawable (신임계) — 국소수정은 방 폴리곤 불변이므로 여기서 확정
            m = DP.metrics(g)
            fp = footprint_metrics(g)
            drawable = (m and m["selfint_rooms"] == 0 and m["overlap_frac"] < 0.25
                        and m["span_ratio"] < args.aspect_max and _min_unique_verts(g) >= 4
                        and not _has_diagonal(g) and fp and fp["pieces"] == 1
                        and fp["fill"] >= args.fp_fill and fp["convex"] >= args.fp_convex)
            if drawable:
                drawable_n += 1
            # 법규 before
            estimate_scale(g)
            vb = verify_plan(g)
            if vb["legal_ok"]:
                legal_b += 1
            tally(vb, okb, appl)
            # 국소수정 → after
            try:
                legal_repair(g); egress_repair(g); snap_windows(g)
            except Exception:
                pass
            estimate_scale(g)
            va = verify_plan(g)
            if va["legal_ok"]:
                legal_a += 1
            tally(va, oka)
            if drawable and va["legal_ok"]:
                accepted += 1
        if done % 100 < ch:
            print(f"  ...{done}/{N} drawable={drawable_n} accept={accepted}", flush=True)

    p = accepted / N
    z = 1.96
    den = 1 + z * z / N
    ctr = (p + z * z / (2 * N)) / den
    half = z * math.sqrt(p * (1 - p) / N + z * z / (4 * N * N)) / den
    out = {
        "ckpt": args.ckpt, "n": N, "eval_seed": args.seed,
        "thresholds": {"aspect_max": args.aspect_max, "fp_fill": args.fp_fill,
                       "fp_convex": args.fp_convex, "overlap": 0.25},
        "decoded": decoded, "drawable": drawable_n,
        "legal_ok_before": legal_b, "legal_ok_after": legal_a,
        "rules": {r: {"applicable": appl[r], "before": okb[r], "after": oka[r]}
                  for r in RULE_IDS},
        "accepted": accepted, "accept_rate": round(100 * p, 1),
        "wilson95": [round(100 * (ctr - half), 1), round(100 * (ctr + half), 1)],
        "expected_draws": round(1 / p, 1) if p > 0 else None,
        "B95": (math.ceil(math.log(0.05) / math.log(1 - p)) if 0 < p < 1 else None),
    }
    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1), flush=True)


if __name__ == "__main__":
    main()
