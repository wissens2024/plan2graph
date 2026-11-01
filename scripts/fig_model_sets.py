"""R / K / RK 생성 도면 개별 PNG 세트 — 라벨 있는 것 / 없는 것 두 벌.

박재완 연구자 요청(2026-08-15):
  · R / K / RK 를 각각 나누어서
  · 각 모델마다 라벨 있는 것 / 없는 것
  · 몽타주 'Figure' 말고 추적 가능한 파일명의 개별 파일
  · 개구부가 잘못 그려진 그림은 금지

개구부 처리 — 중요:
  직렬화 코덱은 개구부를 {kind, edge, pos} 로만 인코딩한다(폭 없음). 기존 렌더러는
  cadrender.py 의 고정 상수(width_px, 최소 12px)로 문·창을 그려서, 데이터에 없는
  치수가 있는 것처럼 보이게 만든다. 여기서는 geom.doors/windows 를 비워 기본 심볼을
  끄고, 같은 위치에 *위치 마커*만 덧그린다. cadrender.py 는 건드리지 않는다.

표본은 무필터(고정 시드로 앞에서부터). drawable 판정은 manifest.json 에 기록만 하고
걸러내지 않는다 — 실패 도면에서 K/RK 격차가 더 잘 보이기 때문.

사용:
  CUDA_VISIBLE_DEVICES=2 PYTHONPATH=src:scripts python scripts/fig_model_sets.py \
      --ckpt ckpts/... --vocab ... --tag RK --country 0 \
      --aspect-max 10 --fp-convex 0.70 --n 20 --outdir figset/RK
"""
from __future__ import annotations
import argparse, io, json, os, sys
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
import torch
from PIL import Image
from plan2graph import cadrender, wallcycle_codec as wc
from plan2graph.generators.wall_cycle import WallCycleLM, make_constraint_mask
import diag_placement as DP
from survey_outline import footprint_metrics
from render_geomclean import _min_unique_verts, _has_diagonal

V = wc.V
DOOR_C, WIN_C, ENT_C = "#e8890c", "#2f6fd0", "#c33333"

# 논문(영문) 그림용 실명 — 용어는 원고와 맞춘다:
#   §3.1.2 "entrance vestibules", §5.2.3 "balconies and outdoor-unit rooms"
ROLE_EN = {
    "거실": "Living",        "주방": "Kitchen",       "현관": "Entrance",
    "침실": "Bedroom",       "안방": "Master bed",    "화장실": "Toilet",
    "욕실": "Bathroom",      "전용화장실": "En-suite", "전용욕실": "En-suite",
    "드레스룸": "Dressing",   "파우더룸": "Powder",     "발코니": "Balcony",
    "실외기실": "Outdoor unit", "다목적공간": "Utility",  "복도": "Corridor",
    "전실": "Vestibule",     "알파룸": "Alpha room",
}


def judge(g, args):
    """eval_3seed.py 와 동일한 drawable-clean 판정 → (drawable, 실패모드)."""
    m = DP.metrics(g)
    ov, sp = m["overlap_frac"], m["span_ratio"]
    fp = footprint_metrics(g)
    checks = [
        ("selfint", m["selfint_rooms"] == 0),
        ("verts", _min_unique_verts(g) >= 4),
        ("diagonal", not _has_diagonal(g)),
        ("footprint_split", bool(fp) and fp["pieces"] == 1),
        ("fill", bool(fp) and fp["fill"] >= args.fp_fill),
        ("convex", bool(fp) and fp["convex"] >= args.fp_convex),
        ("overlap", ov < 0.25),
        ("aspect", sp < args.aspect_max),
    ]
    bad = [k for k, ok in checks if not ok]
    return (not bad), bad, dict(overlap=round(ov, 3), aspect=round(sp, 2),
                                fill=round(fp["fill"], 3) if fp else None,
                                convex=round(fp["convex"], 3) if fp else None,
                                selfint_rooms=m["selfint_rooms"],
                                n_rooms=m["n_rooms"])


def draw_plan(geom, *, labels: bool, dpi: int, role_map=None):
    """개구부 기본 심볼을 끄고 위치 마커만 덧그린 PNG 바이트.

    role_map 을 주면 렌더 동안만 cadrender.ROLE_KO 를 갈아끼워 실명 언어를 바꾼다
    (cadrender.py 자체는 수정하지 않는다).
    """
    saved_roles = cadrender.ROLE_KO
    if role_map is not None:
        cadrender.ROLE_KO = role_map
    try:
        return _draw_plan(geom, labels=labels, dpi=dpi)
    finally:
        cadrender.ROLE_KO = saved_roles


def _draw_plan(geom, *, labels: bool, dpi: int):
    # ① 원래 개구부 위치를 확보 (창은 렌더러와 동일하게 벽에 스냅)
    doors, wins = [], []
    for d in geom.doors:
        doors.append((d.pos, bool(getattr(d, "is_entrance", False))))
    for w in geom.windows:
        try:
            wins.append(cadrender._snap_to_wall(geom, w.pos))
        except Exception:
            wins.append(w.pos)
    # ② 기본 심볼 끄기 — 폭이 데이터에 없으므로 치수를 암시하면 안 된다
    geom.doors, geom.windows = [], []
    fig = cadrender.render_fig(geom, dims=False, labels=labels, fixtures=False)
    ax = fig.axes[0]
    # cadrender 가 붙이는 빨간 "[자기교정 잔여 N건]" 배너 제거 —
    # 정보는 manifest 의 autocorrect_issues 로 보존한다.
    ax.set_title("")
    for t in fig.texts:
        t.set_visible(False)
    # ③ 위치 마커만 덧그리기
    for (x, y), is_ent in doors:
        ax.plot(x, y, marker="o", color=DOOR_C, ms=4.5, mew=0, zorder=6)
        if is_ent:
            ax.plot(x, y, marker="o", mfc="none", mec=ENT_C, ms=9, mew=1.4, zorder=7)
    for (x, y) in wins:
        ax.plot(x, y, marker="s", color=WIN_C, ms=4.0, mew=0, zorder=6)
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=dpi,
                                    bbox_inches="tight", facecolor="white")
    cadrender._close(fig)
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--vocab", required=True)
    ap.add_argument("--tag", required=True, help="R | K | RK")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--max-new", type=int, default=650)
    ap.add_argument("--country", type=int, default=0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--aspect-max", type=float, default=10.0)
    ap.add_argument("--fp-fill", type=float, default=0.60)
    ap.add_argument("--fp-convex", type=float, default=0.70)
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    torch.manual_seed(args.seed); torch.cuda.manual_seed_all(args.seed)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    vocab = json.load(open(args.vocab, encoding="utf-8"))
    ck = torch.load(args.ckpt, map_location=dev); a = ck["args"]
    ep = ck.get("epoch")
    dimff = ck["model"]["blocks.0.mlp.w1.weight"].shape[0]
    model = WallCycleLM(vocab["size"], d_model=a["d_model"], n_layer=a["n_layer"],
                        n_head=a.get("n_head", 8), max_len=a["max_len"], dim_ff=dimff).to(dev)
    model.load_state_dict(ck["model"]); model.eval()
    print(f"[{args.tag}] {args.ckpt} (epoch {ep}) on {dev}", flush=True)

    mask_fn = make_constraint_mask(vocab, orthogonal=True)
    pre = [V.BOS, vocab["meta"] + args.country,
           vocab["meta"] + len(wc.COUNTRIES) + 0,
           vocab["meta"] + len(wc.COUNTRIES) + len(wc.HOUSING) + 0,
           vocab["scope"] + 0, vocab["units"] + 1]

    manifest, idx, ch = [], 0, 4
    with torch.no_grad():
        while idx < args.n:
            k = min(ch, args.n - idx)
            out = model.generate(torch.tensor([pre] * k, device=dev),
                                 max_new=args.max_new, eos=V.EOS,
                                 temperature=1.0, top_k=40, mask_fn=mask_fn)
            for row in out.tolist():
                if idx >= args.n:
                    break
                idx += 1
                stem = f"{args.tag}_seed{args.seed}_ep{ep}_{idx:04d}"
                rec = {"file_stem": stem, "index": idx, "model": args.tag,
                       "train_seed": args.seed, "epoch": ep}
                row = row[:row.index(V.EOS) + 1] if V.EOS in row else row
                try:
                    g = wc.canon_to_graph(wc.decode(row, vocab))
                    for r in g["rooms"].values():
                        if r.get("polygon"):
                            r["polygon"] = wc.rectify_diagonals(r["polygon"])
                    if len(g.get("rooms") or {}) < 2:
                        raise ValueError("too few rooms")
                except Exception as e:
                    rec.update(status="decode_error", detail=f"{type(e).__name__}: {e}")
                    manifest.append(rec); print(f"  {stem}: decode_error", flush=True)
                    continue
                ok, bad, met = judge(g, args)
                rec.update(status="ok", drawable=ok, failed_checks=bad, metrics=met,
                           n_doors=len(g.get("doors") or []),
                           n_windows=len(g.get("windows") or []))
                sub = "drawable" if ok else "failed"
                os.makedirs(os.path.join(args.outdir, sub), exist_ok=True)
                rec["folder"] = sub
                variants = ((True, None, "label_ko"),      # 국내 검토용
                            (True, ROLE_EN, "label_en"),   # 논문(영문) 그림용
                            (False, None, "nolabel"))
                try:
                    for lab, rmap, suf in variants:
                        geom = cadrender.autocorrect(cadrender.from_geomgraph(g))
                        rec["autocorrect_issues"] = len(getattr(geom, "issues", []) or [])
                        png = draw_plan(geom, labels=lab, dpi=args.dpi, role_map=rmap)
                        with open(os.path.join(args.outdir, sub, f"{stem}_{suf}.png"), "wb") as f:
                            f.write(png)
                except Exception as e:
                    rec.update(status="render_error", detail=f"{type(e).__name__}: {e}")
                    print(f"  {stem}: render_error {e}", flush=True)
                manifest.append(rec)
                print(f"  {stem}: drawable={rec.get('drawable')} "
                      f"fail={bad} rooms={met['n_rooms']}", flush=True)

    mpath = os.path.join(args.outdir, "manifest.json")
    nd = sum(1 for r in manifest if r.get("drawable"))
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump({"model": args.tag, "ckpt": args.ckpt, "epoch": ep,
                   "train_seed": args.seed, "gen_seed": args.seed,
                   "country": args.country, "n_requested": args.n,
                   "selection": "unfiltered — fixed seed, first N, no cherry-picking",
                   "opening_rendering": "position markers only; width is NOT encoded "
                                        "in the serialization and is not drawn",
                   "thresholds": {"aspect_max": args.aspect_max, "fp_fill": args.fp_fill,
                                  "fp_convex": args.fp_convex, "overlap": 0.25},
                   "drawable_count": nd, "plans": manifest}, f,
                  ensure_ascii=False, indent=1)
    print(f"[{args.tag}] drawable {nd}/{len(manifest)} → {args.outdir}", flush=True)


if __name__ == "__main__":
    main()
