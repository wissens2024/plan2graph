"""Table 5 — verifier-guided rejection sampling draw budget (gated RK).
생성 n개 per plan: clean(drawable G) · code(legal) before/after repair · joint(G∧code).
Wilson 95% CI + 기대 draw수(1/p) + 95% 성공 draw수 → results_rejection_rk.md.
"""
import argparse, json, math, sys
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
import torch
from plan2graph import wallcycle_codec as wc
from plan2graph.generators.wall_cycle import WallCycleLM, make_constraint_mask
from plan2graph.gen_verify import (verify_plan, legal_repair, egress_repair,
                                   snap_windows, estimate_scale)
from plan2graph.graph_repair import repair_graph


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def draws95(p):
    return math.ceil(math.log(0.05) / math.log(1 - p)) if 0 < p < 1 else None


def dv_fail(v):  # 채광/환기(L1/L2) 위반 있나
    return any(r.get("rule", "").startswith(("L1_daylight_window", "L2")) for r in v["legal_violations"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--vocab", required=True)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--country", type=int, default=0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="results_rejection_rk.md")
    args = ap.parse_args()

    if args.seed > 0:
        torch.manual_seed(args.seed); torch.cuda.manual_seed_all(args.seed)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    vocab = json.load(open(args.vocab, encoding="utf-8"))
    ck = torch.load(args.ckpt, map_location=dev); a = ck["args"]
    dimff = ck["model"]["blocks.0.mlp.w1.weight"].shape[0]
    model = WallCycleLM(vocab["size"], d_model=a["d_model"], n_layer=a["n_layer"],
                        n_head=a.get("n_head", 8), max_len=a["max_len"], dim_ff=dimff).to(dev)
    model.load_state_dict(ck["model"]); model.eval()
    mask_fn = make_constraint_mask(vocab, orthogonal=True)
    bos, eos = wc.V.BOS, wc.V.EOS
    pre = [bos, vocab["meta"] + args.country, vocab["meta"] + len(wc.COUNTRIES) + 0,
           vocab["meta"] + len(wc.COUNTRIES) + len(wc.HOUSING) + 0,
           vocab["scope"] + 0, vocab["units"] + 1]

    N = args.n
    c = dict(clean=0, dv_before=0, dv_after=0, code_before=0, code_after=0,
             clean_code_before=0, clean_code_after=0)
    ch = 4
    for i in range(0, N, ch):
        k = min(ch, N - i)
        prefix = torch.tensor([pre] * k, device=dev)
        out = model.generate(prefix, max_new=650, eos=eos, temperature=1.0, top_k=40, mask_fn=mask_fn)
        for row in out.tolist():
            row = row[:row.index(eos) + 1] if eos in row else row
            try:
                g = wc.canon_to_graph(wc.decode(row, vocab))
            except Exception:
                continue
            if len(g.get("rooms") or {}) < 2:
                continue
            try:
                repair_graph(g, drop_bad=True, declash="wall")
            except Exception:
                pass
            estimate_scale(g)
            vb = verify_plan(g)
            G = vb["geom_ok"]; Lb = vb["legal_ok"]; dvb = not dv_fail(vb)
            if G: c["clean"] += 1
            if dvb: c["dv_before"] += 1
            if Lb: c["code_before"] += 1
            if G and Lb: c["clean_code_before"] += 1
            try:
                legal_repair(g); egress_repair(g); snap_windows(g)
            except Exception:
                pass
            estimate_scale(g)
            va = verify_plan(g)
            La = va["legal_ok"]; dva = not dv_fail(va)
            if dva: c["dv_after"] += 1
            if La: c["code_after"] += 1
            if G and La: c["clean_code_after"] += 1
        print(f"  ...{min(i + ch, N)}/{N}", flush=True)

    def line(name, k):
        lo, hi = wilson(k, N); p = k / N
        ed = f"{1/p:.1f}" if p > 0 else "—"
        d95 = draws95(p); d95 = str(d95) if d95 else "—"
        return f"| {name} | {k}/{N} | {100*p:.1f}% | {100*lo:.1f}–{100*hi:.1f}% | {ed} | {d95} |"

    md = ["# Table 5 — Verifier-guided rejection sampling (gated RK ep180)\n",
          f"n={N} · seed={args.seed} · country={args.country}\n",
          "| Gate | Success | Pass rate | Wilson 95% CI | Expected draws (1/p) | Draws for 95% |",
          "|---|---|---|---|---|---|",
          line("clean (drawable, geometric)", c["clean"]),
          line("code: daylight+vent, before repair", c["dv_before"]),
          line("code: daylight+vent, after repair", c["dv_after"]),
          line("code: all rules, before repair", c["code_before"]),
          line("code: all rules, after repair", c["code_after"]),
          line("clean ∧ code (all), before repair", c["clean_code_before"]),
          line("★ clean ∧ code (all), after repair", c["clean_code_after"]),
          "\n★ = hybrid loop accept rate (single draw → accepted compliant drawing). "
          "Expected draws = 1/p; repair (A arm) lifts the regulatory gate so geometry becomes binding."]
    out = "\n".join(md)
    print("\n" + out)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(out + "\n")
    print(f"\n[done] → {args.out}", flush=True)


if __name__ == "__main__":
    main()
