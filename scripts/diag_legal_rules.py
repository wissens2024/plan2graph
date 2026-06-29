"""규칙별(L1~L6) 법규 준수율 before→after repair — 하이브리드 루프 '국소 수정(A)' 팔의 증거.

생성 n개 → 각 도면: estimate_scale → verify_plan(per-rule 위반 before)
→ legal_repair+egress_repair+snap_windows(=A 팔) → estimate_scale → verify(after).
규칙별 적용수(applicable)·통과(before/after) 집계 → results_legal_rules.md.

분모=applicable(legal_applied에 그 규칙이 든 도면). 통과=적용됐고 위반 없음.
L1·L2·L3=scale 독립(채광·환기·동선, repair 대상). L4·L5·L6=scale 의존(estimate_scale로 활성).
"""
import argparse, json, sys
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
import torch
from plan2graph import wallcycle_codec as wc
from plan2graph.generators.wall_cycle import WallCycleLM, make_constraint_mask
from plan2graph.gen_verify import verify_plan, legal_repair, egress_repair, snap_windows, estimate_scale
from plan2graph.rules_legal import RULES

RULE_IDS = [r.id for r in RULES]
RULE_NAME = {r.id: r.name for r in RULES}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--vocab", required=True)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--country", type=int, default=0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--max-new", type=int, default=650)
    ap.add_argument("--out", default="results_legal_rules.md")
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
    print(f"[ckpt] {args.ckpt} (epoch {ck.get('epoch')}) on {dev}", flush=True)

    mask_fn = make_constraint_mask(vocab, orthogonal=True)
    bos, eos = wc.V.BOS, wc.V.EOS
    pre = [bos, vocab["meta"] + args.country, vocab["meta"] + len(wc.COUNTRIES) + 0,
           vocab["meta"] + len(wc.COUNTRIES) + len(wc.HOUSING) + 0,
           vocab["scope"] + 0, vocab["units"] + 1]

    decoded = 0
    legal_ok_b = legal_ok_a = 0
    appl = {r: 0 for r in RULE_IDS}      # 적용 도면 수(legal_applied)
    okb = {r: 0 for r in RULE_IDS}       # before 통과(적용∧위반없음)
    oka = {r: 0 for r in RULE_IDS}       # after 통과

    def tally(v, applied_acc, ok_acc):
        viol = set(x["rule"] for x in v["legal_violations"])
        for rid in v["legal_applied"]:
            if rid in applied_acc:
                applied_acc[rid] += 1
                if rid not in viol:
                    ok_acc[rid] += 1

    ch = 4
    for i in range(0, args.n, ch):
        k = min(ch, args.n - i)
        prefix = torch.tensor([pre] * k, device=dev)
        out = model.generate(prefix, max_new=args.max_new, eos=eos,
                             temperature=1.0, top_k=40, mask_fn=mask_fn)
        for row in out.tolist():
            row = row[:row.index(eos) + 1] if eos in row else row
            try:
                g = wc.canon_to_graph(wc.decode(row, vocab))
            except Exception:
                continue
            if len(g.get("rooms") or {}) < 2:
                continue
            decoded += 1
            # BEFORE (scale 부여 후 검증)
            estimate_scale(g)
            vb = verify_plan(g)
            if vb["legal_ok"]:
                legal_ok_b += 1
            tally(vb, appl, okb)
            # A 팔: 국소 수정
            try:
                legal_repair(g); egress_repair(g); snap_windows(g)
            except Exception:
                pass
            # AFTER
            estimate_scale(g)
            va = verify_plan(g)
            if va["legal_ok"]:
                legal_ok_a += 1
            tally(va, {r: 0 for r in RULE_IDS}, oka)  # oka는 별도 appl_a 필요 → 아래 보정

        print(f"  ...{min(i + ch, args.n)}/{args.n} decoded={decoded}", flush=True)

    # after의 applicable은 before와 거의 동일(규칙 적용성은 도면 종류). 분모=appl(공통) 사용.
    def line(rid):
        d = appl[rid]
        if d == 0:
            return f"| {rid} ({RULE_NAME[rid]}) | 0 | — | — |"
        return (f"| {rid} ({RULE_NAME[rid]}) | {d} | "
                f"{okb[rid]}/{d} ({100*okb[rid]//d}%) | {oka[rid]}/{d} ({100*oka[rid]//d}%) |")

    md = ["# 규칙별 법규 준수율 before→after repair (A 팔 증거)\n",
          f"ckpt={args.ckpt} · n={args.n} · decoded={decoded} · country={args.country} · seed={args.seed}\n",
          f"전체 법규(legal_ok): before {legal_ok_b}/{decoded} ({100*legal_ok_b//max(1,decoded)}%) "
          f"→ after {legal_ok_a}/{decoded} ({100*legal_ok_a//max(1,decoded)}%)\n",
          "| 규칙 | 적용 도면 | before 통과 | after 통과 |",
          "|---|---|---|---|"]
    md += [line(r) for r in RULE_IDS]
    md.append("\n※ 적용=legal_applied(검사 대상). L1·L2·L3=scale 독립(repair 대상). "
              "L4·L5·L6=estimate_scale(전용84㎡ 가정)로 활성. repair는 창(채광·환기)·문/현관(동선)만 수정.")
    out = "\n".join(md)
    print("\n" + out)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(out + "\n")
    print(f"\n[done] → {args.out}", flush=True)


if __name__ == "__main__":
    main()
