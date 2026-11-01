"""실데이터 천장 실측 — 생성 모델과 동일한 drawable-clean 기준을 '사람이 그린 원본'에 적용.

세 가지를 같은 자로 잰다:
  A. parsed 원본 그대로         → 데이터 자체가 기준을 통과하나
  B. parsed → 코덱 왕복         → 표현(코덱)이 통과율을 얼마나 깎나 = 모델의 천장
  C. 보정본(edits) → 코덱 왕복  → 사람 보정이 그 천장을 올리나

더불어 보정이 '무엇을' 바꿨는지 분류한다(라벨만 / 기하 / 구조 / 축척).
"""
from __future__ import annotations
import argparse, json, os, random, sys

sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
from plan2graph import wallcycle_codec as wc
import diag_placement as DP
from survey_outline import footprint_metrics
from render_geomclean import _min_unique_verts, _has_diagonal

PARSED = "data/staging/parsed/graphs"
EDITS = "data/staging/corrected/edits"
KEYS = ["selfint0", "overlap_ok", "span_ok", "verts_ok", "nodiag",
        "fp_pieces_ok", "fp_fill_ok", "fp_convex_ok", "drawable"]
ARMS = ["A_parsed_raw", "B_parsed_codec", "C0_corr_raw", "C_corr_codec"]


def drawable(g, aspect_max, fp_fill, fp_convex, overlap_max=0.25):
    """eval_3seed.py의 drawable 판정과 동일."""
    if len(g.get("rooms") or {}) < 2:
        return None
    m = DP.metrics(g)
    fp = footprint_metrics(g)
    verts = _min_unique_verts(g) >= 4
    nodiag = not _has_diagonal(g)
    pieces_ok = bool(fp) and fp["pieces"] == 1
    fill_ok = bool(fp) and fp["fill"] >= fp_fill
    convex_ok = bool(fp) and fp["convex"] >= fp_convex
    shape_ok = (m["selfint_rooms"] == 0 and verts and nodiag
                and pieces_ok and fill_ok and convex_ok)
    ov_ok = m["overlap_frac"] < overlap_max
    sp_ok = m["span_ratio"] < aspect_max
    return {"selfint0": m["selfint_rooms"] == 0, "overlap_ok": ov_ok, "span_ok": sp_ok,
            "verts_ok": verts, "nodiag": nodiag, "fp_pieces_ok": pieces_ok,
            "fp_fill_ok": fill_ok, "fp_convex_ok": convex_ok,
            "drawable": bool(shape_ok and ov_ok and sp_ok)}


def via_codec(g, grid):
    """생성 경로와 동일: canonicalize -> encode -> decode -> graph -> rectify."""
    canon = wc.canonicalize(g, grid=grid)
    vb = wc._vocab(grid)
    toks = wc.encode(canon, vb)
    g2 = wc.canon_to_graph(wc.decode(toks, vb))
    for r in g2["rooms"].values():
        if r.get("polygon"):
            r["polygon"] = wc.rectify_diagonals(r["polygon"])
    return g2, len(toks)


def poly_key(poly):
    return tuple(sorted((round(x), round(y)) for x, y in (poly or [])))


def classify_edit(gp, ge):
    """보정이 무엇을 바꿨나."""
    rp = {int(k): v for k, v in (gp.get("rooms") or {}).items()}
    rc = {int(k): v for k, v in (ge.get("rooms") or {}).items()}
    added, removed = set(rc) - set(rp), set(rp) - set(rc)
    both = set(rp) & set(rc)
    role_ch = sum(1 for i in both
                  if (rp[i].get("role") or rp[i].get("base")) != (rc[i].get("role") or rc[i].get("base")))
    geom_ch = sum(1 for i in both if poly_key(rp[i].get("polygon")) != poly_key(rc[i].get("polygon")))
    scale_added = (not gp.get("scale_mm_per_px")) and bool(ge.get("scale_mm_per_px"))
    struct = bool(added or removed) or gp.get("n_edges") != ge.get("n_edges") \
        or gp.get("n_doors") != ge.get("n_doors") or gp.get("n_windows") != ge.get("n_windows")
    if struct or geom_ch:
        kind = "geometry_or_structure"
    elif role_ch:
        kind = "label_only"
    elif scale_added:
        kind = "scale_only"
    else:
        kind = "no_change"
    return {"kind": kind, "role_changed_rooms": role_ch, "geom_changed_rooms": geom_ch,
            "rooms_added": len(added), "rooms_removed": len(removed),
            "n_rooms_parsed": len(rp), "n_rooms_corr": len(rc),
            "scale_added": scale_added, "struct": struct}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=800)
    ap.add_argument("--grid", type=int, default=128)
    ap.add_argument("--aspect-max", type=float, default=10.0)
    ap.add_argument("--fp-fill", type=float, default=0.60)
    ap.add_argument("--fp-convex", type=float, default=0.70)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--json-out", required=True)
    args = ap.parse_args()
    TH = (args.aspect_max, args.fp_fill, args.fp_convex)

    pids_e = set(e.name[:-5] for e in os.scandir(EDITS) if e.name.endswith(".json"))
    pids_p = set(e.name[:-5] for e in os.scandir(PARSED) if e.name.endswith(".json"))
    paired = sorted(pids_p & pids_e)
    sample = random.Random(args.seed).sample(paired, min(args.n, len(paired)))
    print("[pool] parsed=%d edits=%d paired=%d sample=%d"
          % (len(pids_p), len(pids_e), len(paired), len(sample)), flush=True)

    T = dict((a, dict((k, 0) for k in KEYS)) for a in ARMS)
    N = dict((a, 0) for a in ARMS)
    err = dict((a, 0) for a in ARMS)
    kinds = {}
    role_rooms = geom_rooms = tot_rooms = scale_add = 0
    flip = {"B_fail_C_pass": 0, "B_pass_C_fail": 0, "both_pass": 0, "both_fail": 0}
    toklen = []

    for i, pid in enumerate(sample):
        try:
            gp = json.load(open("%s/%s.json" % (PARSED, pid), encoding="utf-8"))
            ge = json.load(open("%s/%s.json" % (EDITS, pid), encoding="utf-8"))
        except Exception:
            continue

        d = classify_edit(gp, ge)
        kinds[d["kind"]] = kinds.get(d["kind"], 0) + 1
        role_rooms += d["role_changed_rooms"]
        geom_rooms += d["geom_changed_rooms"]
        tot_rooms += d["n_rooms_parsed"]
        scale_add += int(d["scale_added"])

        res = {}
        for arm, g, use_codec in (("A_parsed_raw", gp, False), ("B_parsed_codec", gp, True),
                                  ("C0_corr_raw", ge, False), ("C_corr_codec", ge, True)):
            try:
                gg = g
                if use_codec:
                    gg, nt = via_codec(g, args.grid)
                    if arm == "B_parsed_codec":
                        toklen.append(nt)
                r = drawable(gg, *TH)
            except Exception:
                err[arm] += 1
                res[arm] = None
                continue
            if r is None:
                res[arm] = None
                continue
            N[arm] += 1
            for k in KEYS:
                T[arm][k] += int(r[k])
            res[arm] = r["drawable"]

        b, c = res.get("B_parsed_codec"), res.get("C_corr_codec")
        if b is not None and c is not None:
            if b and c:
                flip["both_pass"] += 1
            elif (not b) and (not c):
                flip["both_fail"] += 1
            elif c:
                flip["B_fail_C_pass"] += 1
            else:
                flip["B_pass_C_fail"] += 1

        if (i + 1) % 100 == 0:
            print("  ...%d/%d  A=%d B=%d C=%d" % (i + 1, len(sample),
                  T["A_parsed_raw"]["drawable"], T["B_parsed_codec"]["drawable"],
                  T["C_corr_codec"]["drawable"]), flush=True)

    out = {"n_requested": args.n, "n_sample": len(sample), "grid": args.grid,
           "thresholds": {"aspect_max": args.aspect_max, "fp_fill": args.fp_fill,
                          "fp_convex": args.fp_convex, "overlap": 0.25},
           "seed": args.seed,
           "pool": {"parsed": len(pids_p), "edits": len(pids_e), "paired": len(paired)},
           "arms": dict((a, {"n": N[a], "errors": err[a], "tally": T[a],
                             "pct": dict((k, (100.0 * T[a][k] / N[a]) if N[a] else None)
                                         for k in KEYS)}) for a in ARMS),
           "edit_kinds": kinds,
           "edit_detail": {"role_changed_rooms": role_rooms, "geom_changed_rooms": geom_rooms,
                           "total_rooms_parsed": tot_rooms, "scale_added_plans": scale_add},
           "paired_flip_B_to_C": flip,
           "token_len_parsed_codec": {"n": len(toklen),
                                      "mean": (sum(toklen) / len(toklen)) if toklen else None,
                                      "max": max(toklen) if toklen else None}}
    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print("=" * 70, flush=True)
    for a in ARMS:
        p = out["arms"][a]["pct"]
        if not N[a]:
            print("%-16s n=0 (all failed, errors=%d)" % (a, err[a]), flush=True)
            continue
        print("%-16s n=%5d err=%4d drawable=%5.1f%%  selfint0=%5.1f%% nodiag=%5.1f%% "
              "fill=%5.1f%% convex=%5.1f%% 1piece=%5.1f%% span=%5.1f%%"
              % (a, N[a], err[a], p["drawable"], p["selfint0"], p["nodiag"],
                 p["fp_fill_ok"], p["fp_convex_ok"], p["fp_pieces_ok"], p["span_ok"]), flush=True)
    print("-" * 70, flush=True)
    print("edit kinds:", kinds, flush=True)
    print("edit detail:", out["edit_detail"], flush=True)
    print("flip B->C:", flip, flush=True)
    print("[json] %s" % args.json_out, flush=True)


if __name__ == "__main__":
    main()
