"""snap 역설 정량 분석 — per-sample 분포 + coverage 신지표.
overlap_frac이 '틈(파편화)'을 보상하고 '맞붙음'을 처벌하는지 검증.
coverage = union_area / bbox_area = 도면이 bbox를 얼마나 채우는가(진짜 아파트=높음, 떠다니는 박스=낮음).
"""
from __future__ import annotations
import argparse, json, os, sys, time
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
import torch
from shapely.geometry import Polygon, box
from shapely.ops import unary_union
from shapely.validation import make_valid
from plan2graph import wallcycle_codec as wc
from plan2graph.generators.wall_cycle import WallCycleLM, make_constraint_mask
import diag_placement as DP


def coverage_and_pieces(g):
    polys = []
    for r in g["rooms"].values():
        pp = r.get("polygon") or []
        if len(pp) >= 3:
            try:
                q = Polygon(pp)
                if not q.is_valid:
                    q = make_valid(q)
                if getattr(q, "area", 0) > 1:
                    polys.append(q)
            except Exception:
                pass
    if not polys:
        return 0.0, 99
    u = unary_union(polys)
    minx = min(p.bounds[0] for p in polys); miny = min(p.bounds[1] for p in polys)
    maxx = max(p.bounds[2] for p in polys); maxy = max(p.bounds[3] for p in polys)
    bbox_area = max(1.0, (maxx - minx) * (maxy - miny))
    cov = u.area / bbox_area
    pieces = len(u.geoms) if u.geom_type == "MultiPolygon" else 1
    return cov, pieces


def run(ckpt, vocab_path, country, n, seed, dev, ch, label):
    torch.manual_seed(seed); torch.cuda.manual_seed_all(seed)
    vocab = json.load(open(vocab_path, encoding="utf-8"))
    ck = torch.load(ckpt, map_location=dev); a = ck["args"]
    model = WallCycleLM(vocab["size"], d_model=a["d_model"], n_layer=a["n_layer"],
                        n_head=a.get("n_head", 8), max_len=a["max_len"], dim_ff=a.get("dim_ff")).to(dev)
    model.load_state_dict(ck["model"]); model.eval()
    mask_fn = make_constraint_mask(vocab, orthogonal=True)
    bos, eos = wc.V.BOS, wc.V.EOS
    pre = [bos, vocab["meta"] + country, vocab["meta"] + len(wc.COUNTRIES) + 0,
           vocab["meta"] + len(wc.COUNTRIES) + len(wc.HOUSING) + 0,
           vocab["scope"] + 0, vocab["units"] + 1]
    samples = []
    t0 = time.time()
    for i in range(0, n, ch):
        k = min(ch, n - i)
        prefix = torch.tensor([pre] * k, device=dev)
        out = model.generate(prefix, max_new=650, eos=eos, temperature=1.0, top_k=40, mask_fn=mask_fn)
        print(f"  [{label}] {min(i+ch,n)}/{n}  ({time.time()-t0:.0f}s)", flush=True)
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
            cov, pieces = coverage_and_pieces(g)
            samples.append(dict(n_rooms=m["n_rooms"], selfint=m["selfint_rooms"],
                                overlap=m["overlap_frac"], overlap_any=bool(m["overlap_any"]),
                                span=m["span_ratio"], pieces=pieces, coverage=round(cov, 4)))
    return samples


def summarize(label, S):
    n = len(S)
    if n == 0:
        print(f"{label}: decoded 0"); return
    def frac(pred): return sum(1 for s in S if pred(s)) / n
    def mean(key): return sum(s[key] for s in S) / n
    single = frac(lambda s: s["pieces"] == 1)
    selfint0 = frac(lambda s: s["selfint"] == 0)
    ov_ok = frac(lambda s: s["overlap"] < 0.25)
    span_ok = frac(lambda s: s["span"] < 8)
    clean = frac(lambda s: s["selfint"] == 0 and s["overlap"] < 0.25 and s["span"] < 8)
    # overlap buckets
    b0 = frac(lambda s: s["overlap"] == 0)
    b1 = frac(lambda s: 0 < s["overlap"] < 0.05)
    b2 = frac(lambda s: 0.05 <= s["overlap"] < 0.25)
    b3 = frac(lambda s: 0.25 <= s["overlap"] < 0.5)
    b4 = frac(lambda s: s["overlap"] >= 0.5)
    # coverage by group
    cov_all = mean("coverage")
    sing = [s for s in S if s["pieces"] == 1]
    multi = [s for s in S if s["pieces"] != 1]
    cov_single = sum(s["coverage"] for s in sing) / len(sing) if sing else 0
    cov_multi = sum(s["coverage"] for s in multi) / len(multi) if multi else 0
    # "real floorplan" = single AND overlap<.25 AND coverage>0.6 (틈 적고 안겹치고 연결)
    real = frac(lambda s: s["pieces"] == 1 and s["overlap"] < 0.25 and s["coverage"] > 0.6 and s["span"] < 8)
    print(f"\n### {label}  (decoded n={n})")
    print(f"  rooms mean {mean('n_rooms'):.1f}")
    print(f"  single {single*100:.0f}% | selfint0 {selfint0*100:.0f}% | overlap<.25 {ov_ok*100:.0f}% | span<8 {span_ok*100:.0f}% | CLEAN {clean*100:.0f}%")
    print(f"  overlap 분포: =0 {b0*100:.0f}% | 0-.05 {b1*100:.0f}% | .05-.25 {b2*100:.0f}% | .25-.5 {b3*100:.0f}% | >=.5 {b4*100:.0f}%")
    print(f"  coverage(충전율) 평균 {cov_all:.2f} | single방 {cov_single:.2f} | multi방 {cov_multi:.2f}")
    print(f"  ★ real(single∧overlap<.25∧cov>.6∧span<8) {real*100:.0f}%")
    return dict(label=label, n=n, single=single, selfint0=selfint0, ov_ok=ov_ok, clean=clean,
                cov=cov_all, cov_single=cov_single, real=real,
                ov_buckets=[b0,b1,b2,b3,b4])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--ch", type=int, default=32)
    args = ap.parse_args()
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    VN = "data/staging/tokens_korean_clean_nosnap/vocab.json"
    VS = "data/staging/tokens_korean_clean_snap/vocab.json"
    models = [
        ("target-only nosnap", "ckpts/korplan_ar_k_nosnap_ep50.pt", VN, 0),
        ("target-only snap", "ckpts/korplan_ar_k_snap_ep50.pt", VS, 0),
        ("RPLAN→FT nosnap", "ckpts/korplan_ar_rk_nosnap_ep100.pt", VN, 0),
        ("RPLAN→FT snap", "ckpts/korplan_ar_rk_snap_ep100.pt", VS, 0),
    ]
    out = {}
    res = []
    for label, ck, voc, country in models:
        if not os.path.exists(ck):
            print(f"{label}: ckpt 없음 {ck}"); continue
        print(f">>> {label} 생성 시작", flush=True)
        S = run(ck, voc, country, args.n, args.seed, dev, args.ch, label)
        out[label] = S
        r = summarize(label, S)
        if r: res.append(r)
    json.dump(out, open("/tmp/snap_analysis.json", "w"), ensure_ascii=False)
    print("\n저장 → /tmp/snap_analysis.json")


if __name__ == "__main__":
    main()
