"""wall-cycle 생성 → 법규검증 + 기하교정 + 렌더 파이프라인 (ADR-0012 §7 self-correction 배선).

ckpt 로드 → 생성 N → decode → g-0.4 그래프 →
  ① 기하: cadrender.from_geomgraph → autocorrect(R1~R8) → verify
  ② 법규: geomgraph→nx → rules_legal.check_legal (창보유 등; scale 없으면 창보유만)
  → rerank: valid & 법규통과 & 기하clean 샘플을 렌더(PNG 저장).

이것이 wall-cycle 생성기와 룰/법규 엔진을 잇는 배선 — 지금까지 위상 생성기에만 연결돼 있던 것.

사용(서버 115):
  CUDA_VISIBLE_DEVICES=1 PYTHONPATH=src python scripts/eval_wallcycle_pipeline.py \
    --ckpt ckpt_wallcycle_kr.pt --vocab data/staging/tokens_parsed_apt/vocab.json \
    --n 64 --constrained --out /tmp/wc_samples
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, "src")
import networkx as nx  # noqa: E402
import torch  # noqa: E402

from plan2graph import cadrender, rules_legal, wallcycle_codec as wc  # noqa: E402
from plan2graph.generators.wall_cycle import WallCycleLM, make_constraint_mask  # noqa: E402


def to_nx_legal(g: dict) -> nx.Graph:
    """g-0.4 dict → rules_legal.check_legal 입력용 nx.Graph(type/n_windows/area_px + graph.scale)."""
    G = nx.Graph()
    G.graph["scale"] = g.get("scale_mm_per_px")
    win_by_room = {}
    for w in (g.get("windows") or []):
        rid = w.get("belongs_to")
        win_by_room[rid] = win_by_room.get(rid, 0) + 1
    for nid, r in (g.get("rooms") or {}).items():
        n = int(nid) if str(nid).lstrip("-").isdigit() else nid
        G.add_node(n, type=r.get("role"), area_px=r.get("area_px"),
                   n_windows=win_by_room.get(n, 0))
    for e in (g.get("edges") or []):
        a, b = e.get("from"), e.get("to")
        if a is not None and b is not None:
            G.add_edge(a, b, via=e.get("via"))
    return G


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--vocab", required=True)
    ap.add_argument("--n", type=int, default=64)
    ap.add_argument("--constrained", action="store_true")
    ap.add_argument("--out", default="/tmp/wc_samples")
    ap.add_argument("--render", type=int, default=4, help="통과 샘플 렌더 장수")
    args = ap.parse_args()

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    vocab = json.load(open(args.vocab, encoding="utf-8"))
    ck = torch.load(args.ckpt, map_location=dev)
    a = ck["args"]
    model = WallCycleLM(vocab["size"], d_model=a["d_model"], n_layer=a["n_layer"],
                        n_head=a.get("n_head", 8), max_len=a["max_len"]).to(dev)
    model.load_state_dict(ck["model"])
    model.eval()
    print(f"[ckpt] {args.ckpt} (epoch {ck.get('epoch')}) on {dev}")

    mask_fn = make_constraint_mask(vocab) if args.constrained else None
    bos, eos = wc.V.BOS, wc.V.EOS
    pre = [bos, vocab["meta"] + 0, vocab["meta"] + len(wc.COUNTRIES) + 0,
           vocab["meta"] + len(wc.COUNTRIES) + len(wc.HOUSING) + 0,
           vocab["scope"] + 0, vocab["units"] + 1]
    prefix = torch.tensor([pre] * args.n, device=dev)
    out = model.generate(prefix, max_new=a["max_len"] - len(pre), eos=eos,
                         temperature=1.0, top_k=40, mask_fn=mask_fn)

    os.makedirs(args.out, exist_ok=True)
    n_valid = n_legal = n_geoclean = n_full = 0
    legal_viol = 0
    rendered = 0
    for row in out.tolist():
        if eos in row:
            row = row[:row.index(eos) + 1]
        try:
            canon = wc.decode(row, vocab)
            g = wc.canon_to_graph(canon)
        except Exception:  # noqa: BLE001
            continue
        nr = len(g["rooms"])
        valid = nr >= 2 and all(len(rm["cycle"]) >= 3 for rm in canon.rooms) \
            and all(d.get("on_wall") for d in g["doors"])
        if valid:
            n_valid += 1
        # ② 법규
        legal = rules_legal.check_legal(to_nx_legal(g))
        if legal["passed"]:
            n_legal += 1
        legal_viol += legal["n_violations"]
        # ① 기하 + 교정
        geo_ok = False
        try:
            geom = cadrender.from_geomgraph(g)
            geom = cadrender.autocorrect(geom)
            geo_v = cadrender.verify(geom)
            geo_ok = len(geo_v) == 0
        except Exception:  # noqa: BLE001
            geo_v = ["render_err"]
        if geo_ok:
            n_geoclean += 1
        # rerank: 전부 통과 → 렌더
        if valid and legal["passed"] and geo_ok:
            n_full += 1
            if rendered < args.render:
                try:
                    png = cadrender.render_png(geom)
                    open(os.path.join(args.out, f"pass_{rendered}.png"), "wb").write(png)
                    rendered += 1
                except Exception:  # noqa: BLE001
                    pass

    N = args.n
    print("=" * 56)
    print(f"생성 {N} (constrained={args.constrained})")
    print(f"  valid(구조)      : {n_valid}/{N} ({100*n_valid/N:.1f}%)")
    print(f"  법규 통과         : {n_legal}/{N} ({100*n_legal/N:.1f}%)  위반합 {legal_viol}")
    print(f"  기하 clean(교정후) : {n_geoclean}/{N} ({100*n_geoclean/N:.1f}%)")
    print(f"  ★ 전부 통과(rerank): {n_full}/{N} ({100*n_full/N:.1f}%)")
    print(f"  렌더 저장         : {rendered}장 → {args.out}/pass_*.png")


if __name__ == "__main__":
    main()
