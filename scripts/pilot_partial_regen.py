"""부분 재생성(prefix rollback) 파일럿 — "왜 AR인가"의 반복적 정제 주장 검증.

두 팔을 같은 모델·같은 검증기·같은 수용 기준(drawable-clean)으로 비교한다.

  A) 독립 재추출   : 실패하면 도면을 처음부터 다시 생성 (현행 논문 Eq.11 가정)
  B) 부분 재생성   : 실패 원인 방을 찾아 그 방 직전까지 토큰을 되감고 이어서 디코딩
                     (코너 섹션은 보존 → 기하 꼭짓점은 유지, 방 배치만 재생성)

측정: 수용까지 **디코딩한 총 토큰 수**와 시도 횟수. 토큰 수가 AR의 실제 계산 비용이다.
B가 A보다 적은 토큰으로 수용에 도달하면 "AR이라서 국소 재생성이 가능하다"가 수치로 증명된다.

실패 모드 tally도 함께 남긴다 — B가 안 통하면 그 이유(코너 단계 결함인지 방 배치 결함인지)를
말해주기 때문이다.
"""
from __future__ import annotations
import argparse, json, sys, time
sys.path.insert(0, "src"); sys.path.insert(0, "scripts")
import torch
from shapely.geometry import Polygon
from shapely.ops import unary_union
from plan2graph import wallcycle_codec as wc
from plan2graph.generators.wall_cycle import WallCycleLM, make_constraint_mask
import diag_placement as DP
from survey_outline import footprint_metrics
from render_geomclean import _min_unique_verts, _has_diagonal

V = wc.V


# ── eval_3seed.py와 동일한 drawable-clean 판정 ────────────────────────────────
def single_pieces(g, tol_frac=0.0):
    polys = []
    for r in g["rooms"].values():
        pp = r.get("polygon") or []
        if len(pp) >= 3:
            try:
                q = Polygon(pp)
                if q.is_valid and q.area > 1:
                    polys.append(q)
            except Exception:
                pass
    if not polys:
        return 99
    if tol_frac > 0:
        minx = min(p.bounds[0] for p in polys); miny = min(p.bounds[1] for p in polys)
        maxx = max(p.bounds[2] for p in polys); maxy = max(p.bounds[3] for p in polys)
        span = max(maxx - minx, maxy - miny)
        polys = [p.buffer(tol_frac * span) for p in polys]
    u = unary_union(polys)
    return len(u.geoms) if u.geom_type == "MultiPolygon" else 1


def judge(row, vocab, args):
    """토큰 행 → (drawable: bool, canon 또는 None, 실패모드 str)."""
    eos = V.EOS
    row = row[:row.index(eos) + 1] if eos in row else row
    try:
        canon = wc.decode(row, vocab)
        g = wc.canon_to_graph(canon)
    except Exception:
        return False, None, "decode_error"
    if len(g.get("rooms") or {}) < 2:
        return False, None, "too_few_rooms"
    for r in g["rooms"].values():
        if r.get("polygon"):
            r["polygon"] = wc.rectify_diagonals(r["polygon"])
    m = DP.metrics(g)
    ov, sp = m["overlap_frac"], m["span_ratio"]
    fp = footprint_metrics(g)
    selfint_ok = (m["selfint_rooms"] == 0)
    verts_ok = _min_unique_verts(g) >= 4
    diag_ok = not _has_diagonal(g)
    fp_ok = bool(fp) and fp["pieces"] == 1
    fill_ok = bool(fp) and fp["fill"] >= args.fp_fill
    conv_ok = bool(fp) and fp["convex"] >= args.fp_convex
    ov_ok = ov < 0.25
    sp_ok = sp < args.aspect_max
    shape_ok = selfint_ok and verts_ok and diag_ok and fp_ok and fill_ok and conv_ok
    if shape_ok and ov_ok and sp_ok:
        return True, canon, "ok"
    # 실패 모드 — 우선순위대로 하나만 기록
    if not selfint_ok:
        mode = "selfint"
    elif not ov_ok:
        mode = "overlap"
    elif not sp_ok:
        mode = "aspect"
    elif not diag_ok:
        mode = "diagonal"
    elif not verts_ok:
        mode = "verts"
    elif not fp_ok:
        mode = "footprint_split"
    elif not fill_ok:
        mode = "fill"
    elif not conv_ok:
        mode = "convex"
    else:
        mode = "other"
    return False, canon, mode


# ── 원인 방 찾기 (canon.rooms 순서 = 토큰 블록 순서) ──────────────────────────
def offending_room(canon, aspect_max):
    """실패를 유발한 가장 이른 방의 ordinal. 특정 불가면 None."""
    try:
        cpx = [wc._dequant(q, canon.bbox, canon.grid) for q in canon.corners]
    except Exception:
        return None
    polys = []
    for rm in canon.rooms:
        cyc = rm.get("cycle") or []
        pts = [cpx[c] for c in cyc if 0 <= c < len(cpx)]
        if len(pts) < 3:
            polys.append(None); continue
        try:
            polys.append(Polygon(pts))
        except Exception:
            polys.append(None)
    if not polys:
        return None
    # ① 자기교차/퇴화 → 가장 이른 방
    for i, p in enumerate(polys):
        if p is None or (not p.is_valid) or p.area <= 1:
            return i
    # ② 종횡비 초과 → 가장 이른 방
    for i, p in enumerate(polys):
        x0, y0, x1, y1 = p.bounds
        w, h = x1 - x0, y1 - y0
        if min(w, h) < 1e-6 or max(w, h) / min(w, h) >= aspect_max:
            return i
    # ③ 겹침 → 가장 심한 쌍의 나중 방(그 방이 앞 방을 침범한 것으로 본다)
    worst, widx = 0.0, None
    for i in range(len(polys)):
        for j in range(i + 1, len(polys)):
            try:
                a = polys[i].intersection(polys[j]).area
            except Exception:
                continue
            if a > worst:
                worst, widx = a, j
    if widx is not None and worst > 0:
        return widx
    return None


def rollback(tokens, room_idx):
    """방 room_idx 직전까지 토큰을 자른다. 코너 섹션은 보존. 불가하면 None."""
    if V.SEC_ROOMS not in tokens:
        return None
    i_rooms = tokens.index(V.SEC_ROOMS)
    if room_idx is None or room_idx <= 0:
        return tokens[:i_rooms + 1]                    # 방 전체 재생성(코너 유지)
    ends = [i for i, t in enumerate(tokens) if t == V.ROOM_END and i > i_rooms]
    if room_idx > len(ends):
        return None
    return tokens[:ends[room_idx - 1] + 1]             # 방 0..room_idx-1 유지


# ── 한 번의 디코딩 ────────────────────────────────────────────────────────────
def decode_once(model, prefix_list, vocab, args, mask_fn, dev):
    """prefix(list[int])에서 이어서 생성 → (row:list[int], 새로 디코딩한 토큰 수)."""
    x = torch.tensor([prefix_list], device=dev)
    budget = max(1, args.max_len_total - len(prefix_list))
    out = model.generate(x, max_new=budget, eos=V.EOS,
                         temperature=1.0, top_k=40, mask_fn=mask_fn)
    row = out[0].tolist()
    return row, len(row) - len(prefix_list)


def run_trial(model, base_pre, vocab, args, mask_fn, dev, mode):
    """mode='independent' | 'rollback'. → dict(accepted, tokens, attempts, modes[])"""
    tokens_spent, attempts, fail_modes = 0, 0, []
    seq, seq_off = None, None
    rollback_used, streak, restarts = 0, 0, 0
    while attempts < args.max_attempts:
        if mode == "rollback" and seq is not None:
            pre = rollback(seq, seq_off)
            if pre is None or len(pre) >= args.max_len_total - 8:
                pre = list(base_pre); seq = None; streak = 0; restarts += 1
            else:
                rollback_used += 1; streak += 1
        else:
            pre = list(base_pre)
        row, spent = decode_once(model, pre, vocab, args, mask_fn, dev)
        tokens_spent += spent
        attempts += 1
        ok, canon, fmode = judge(row, vocab, args)
        if ok:
            return dict(accepted=True, tokens=tokens_spent, attempts=attempts,
                        fail_modes=fail_modes, rollback_used=rollback_used,
                        restarts=restarts)
        fail_modes.append(fmode)
        if mode == "rollback":
            # 같은 코너 집합으로 patience번 되감아도 안 되면 전체 재생성으로 전환
            # (§4.5의 "국소 보정 실패 → 재생성" 정책과 동일)
            if canon is None or streak >= args.rollback_patience:
                seq, seq_off, streak = None, None, 0   # 다음 루프는 base_pre로 전체 재생성
            else:
                seq = row[:row.index(V.EOS) + 1] if V.EOS in row else row
                seq_off = offending_room(canon, args.aspect_max)
    return dict(accepted=False, tokens=tokens_spent, attempts=attempts,
                fail_modes=fail_modes, rollback_used=rollback_used,
                restarts=restarts)


def summarize(rows):
    acc = [r for r in rows if r["accepted"]]
    n = len(rows)
    out = {
        "trials": n,
        "accepted": len(acc),
        "censored": n - len(acc),
        "mean_tokens_to_accept": round(sum(r["tokens"] for r in acc) / len(acc), 1) if acc else None,
        "mean_attempts_to_accept": round(sum(r["attempts"] for r in acc) / len(acc), 2) if acc else None,
        "median_tokens_to_accept": None,
        "total_tokens_all_trials": sum(r["tokens"] for r in rows),
        "rollback_used_total": sum(r["rollback_used"] for r in rows),
        "restarts_total": sum(r.get("restarts", 0) for r in rows),
    }
    if acc:
        ts = sorted(r["tokens"] for r in acc)
        mid = len(ts) // 2
        out["median_tokens_to_accept"] = ts[mid] if len(ts) % 2 else (ts[mid - 1] + ts[mid]) / 2
    tally = {}
    for r in rows:
        for m in r["fail_modes"]:
            tally[m] = tally.get(m, 0) + 1
    out["fail_mode_tally"] = dict(sorted(tally.items(), key=lambda kv: -kv[1]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--vocab", required=True)
    ap.add_argument("--trials", type=int, default=100)
    ap.add_argument("--max-attempts", type=int, default=12)
    ap.add_argument("--rollback-patience", type=int, default=3,
                    help="같은 코너 집합으로 연속 되감기 허용 횟수. 초과하면 전체 재생성")
    ap.add_argument("--max-len-total", type=int, default=650)
    ap.add_argument("--country", type=int, default=0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--aspect-max", type=float, default=10.0)
    ap.add_argument("--fp-fill", type=float, default=0.60)
    ap.add_argument("--fp-convex", type=float, default=0.70)
    ap.add_argument("--json-out", required=True)
    args = ap.parse_args()

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    vocab = json.load(open(args.vocab, encoding="utf-8"))
    ck = torch.load(args.ckpt, map_location=dev); a = ck["args"]
    dimff = ck["model"]["blocks.0.mlp.w1.weight"].shape[0]
    model = WallCycleLM(vocab["size"], d_model=a["d_model"], n_layer=a["n_layer"],
                        n_head=a.get("n_head", 8), max_len=a["max_len"], dim_ff=dimff).to(dev)
    model.load_state_dict(ck["model"]); model.eval()
    print(f"[ckpt] {args.ckpt} (epoch {ck.get('epoch')}) on {dev}", flush=True)

    mask_fn = make_constraint_mask(vocab, orthogonal=True)
    base_pre = [V.BOS, vocab["meta"] + args.country,
                vocab["meta"] + len(wc.COUNTRIES) + 0,
                vocab["meta"] + len(wc.COUNTRIES) + len(wc.HOUSING) + 0,
                vocab["scope"] + 0, vocab["units"] + 1]

    results = {}
    with torch.no_grad():
        for mode in ("independent", "rollback"):
            torch.manual_seed(args.seed); torch.cuda.manual_seed_all(args.seed)
            rows, t0 = [], time.time()
            for t in range(args.trials):
                rows.append(run_trial(model, base_pre, vocab, args, mask_fn, dev, mode))
                if (t + 1) % 10 == 0:
                    s = summarize(rows)
                    print(f"  [{mode}] {t + 1}/{args.trials} "
                          f"accepted={s['accepted']} mean_tok={s['mean_tokens_to_accept']} "
                          f"({time.time() - t0:.0f}s)", flush=True)
            results[mode] = summarize(rows)
            results[mode]["per_trial"] = rows

    A, B = results["independent"], results["rollback"]
    verdict = {}
    if A["mean_tokens_to_accept"] and B["mean_tokens_to_accept"]:
        verdict["token_saving_pct"] = round(
            100 * (A["mean_tokens_to_accept"] - B["mean_tokens_to_accept"]) / A["mean_tokens_to_accept"], 1)
        verdict["attempt_saving_pct"] = round(
            100 * (A["mean_attempts_to_accept"] - B["mean_attempts_to_accept"]) / A["mean_attempts_to_accept"], 1)
        verdict["rollback_wins"] = B["mean_tokens_to_accept"] < A["mean_tokens_to_accept"]

    out = {"ckpt": args.ckpt, "epoch": ck.get("epoch"), "seed": args.seed,
           "trials": args.trials, "max_attempts": args.max_attempts,
           "thresholds": {"aspect_max": args.aspect_max, "fp_fill": args.fp_fill,
                          "fp_convex": args.fp_convex, "overlap": 0.25},
           "acceptance_criterion": "drawable-clean",
           "independent": A, "rollback": B, "verdict": verdict}
    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print("=" * 60, flush=True)
    for k in ("independent", "rollback"):
        r = results[k]
        print(f"  {k:12s} accepted={r['accepted']}/{r['trials']} "
              f"mean_tokens={r['mean_tokens_to_accept']} "
              f"mean_attempts={r['mean_attempts_to_accept']}", flush=True)
    print(f"  verdict: {verdict}", flush=True)
    print(f"[json] {args.json_out}", flush=True)


if __name__ == "__main__":
    main()
