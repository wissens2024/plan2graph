"""도면화 가능성(realizability) 실측 — drawable(기하)이 구조적으로 못 보는 축.

drawable-clean은 방 폴리곤만 본다. 그런데 '그려 쓸 수 있는 도면'은 그 위에
문 폭·힌지·스윙, 창 폭, 축척, 종횡비가 있어야 한다. 이 필드들이
  (A) 자동 파싱 결과에 얼마나 있나
  (B) 사람 보정이 그것을 얼마나 채웠나
를 쌍(paired)으로 전수 측정한다. 재학습 불필요.
"""
from __future__ import annotations
import argparse, json, os, sys

PARSED = "data/staging/parsed/graphs"
EDITS = "data/staging/corrected/edits"


def feats(g):
    doors = g.get("doors") or []
    wins = g.get("windows") or []
    dw = sum(1 for d in doors if d.get("width_px"))
    do = sum(1 for d in doors if (d.get("orientation") or {}).get("swing_dir_deg") is not None)
    dh = sum(1 for d in doors if (d.get("orientation") or {}).get("hinge"))
    ww = sum(1 for w in wins if w.get("width_px"))
    wo = sum(1 for w in wins if w.get("orientation_deg") is not None)
    walls = g.get("walls") or []
    wth = sum(1 for w in walls if w.get("thickness_mm") is not None)
    bbox = g.get("bbox_px")
    aspect = None
    if bbox and len(bbox) == 4 and bbox[3]:
        try:
            aspect = float(bbox[2]) / float(bbox[3])
        except Exception:
            aspect = None
    # 도면화 가능 = 문·창 전부 폭 보유 + 문 전부 스윙 보유 + 축척 보유
    full_door = (len(doors) > 0 and dw == len(doors) and do == len(doors))
    full_win = (len(wins) == 0 or ww == len(wins))
    has_scale = bool(g.get("scale_mm_per_px"))
    return {
        "n_doors": len(doors), "n_wins": len(wins), "n_walls": len(walls),
        "d_width": dw, "d_swing": do, "d_hinge": dh,
        "w_width": ww, "w_orient": wo, "wall_thick": wth,
        "has_scale": has_scale, "aspect": aspect,
        "full_door": full_door, "full_win": full_win,
        "realizable": bool(full_door and full_win and has_scale),
        "realizable_noscale": bool(full_door and full_win),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=0, help="0=전수")
    ap.add_argument("--json-out", required=True)
    a = ap.parse_args()

    pid_p = set(e.name[:-5] for e in os.scandir(PARSED) if e.name.endswith(".json"))
    pid_e = set(e.name[:-5] for e in os.scandir(EDITS) if e.name.endswith(".json"))
    paired = sorted(pid_p & pid_e)
    if a.n:
        paired = paired[:a.n]
    print("[pool] parsed=%d edits=%d paired=%d" % (len(pid_p), len(pid_e), len(paired)), flush=True)

    AGG = {}
    for arm in ("parsed", "corrected"):
        AGG[arm] = {k: 0 for k in ("n", "full_door", "full_win", "has_scale",
                                   "realizable", "realizable_noscale", "zero_doors")}
        AGG[arm].update({k: 0 for k in ("n_doors", "n_wins", "n_walls", "d_width",
                                        "d_swing", "d_hinge", "w_width", "w_orient", "wall_thick")})
    flip = {"P0_C1": 0, "P1_C0": 0, "both1": 0, "both0": 0}
    asp = []
    n = 0
    for pid in paired:
        try:
            gp = json.load(open("%s/%s.json" % (PARSED, pid), encoding="utf-8"))
            ge = json.load(open("%s/%s.json" % (EDITS, pid), encoding="utf-8"))
        except Exception:
            continue
        fp, fe = feats(gp), feats(ge)
        n += 1
        for arm, f in (("parsed", fp), ("corrected", fe)):
            A = AGG[arm]
            A["n"] += 1
            for k in ("n_doors", "n_wins", "n_walls", "d_width", "d_swing",
                      "d_hinge", "w_width", "w_orient", "wall_thick"):
                A[k] += f[k]
            for k in ("full_door", "full_win", "has_scale", "realizable", "realizable_noscale"):
                A[k] += int(f[k])
            A["zero_doors"] += int(f["n_doors"] == 0)
        p, c = fp["realizable"], fe["realizable"]
        flip["both1" if (p and c) else "both0" if (not p and not c) else "P0_C1" if c else "P1_C0"] += 1
        if fe["aspect"]:
            asp.append(fe["aspect"])
        if n % 2000 == 0:
            print("  ...%d  P.real=%d C.real=%d" % (n, AGG["parsed"]["realizable"],
                                                    AGG["corrected"]["realizable"]), flush=True)

    def pct(a, b):
        return (100.0 * a / b) if b else None

    out = {"n_paired": n, "arms": {}, "flip_realizable": flip}
    for arm in ("parsed", "corrected"):
        A = AGG[arm]
        out["arms"][arm] = {
            "n": A["n"],
            "pct_full_door": pct(A["full_door"], A["n"]),
            "pct_full_win": pct(A["full_win"], A["n"]),
            "pct_has_scale": pct(A["has_scale"], A["n"]),
            "pct_realizable": pct(A["realizable"], A["n"]),
            "pct_realizable_noscale": pct(A["realizable_noscale"], A["n"]),
            "pct_zero_doors": pct(A["zero_doors"], A["n"]),
            "doors_total": A["n_doors"], "doors_with_width": A["d_width"],
            "doors_with_swing": A["d_swing"], "doors_with_hinge": A["d_hinge"],
            "pct_door_width": pct(A["d_width"], A["n_doors"]),
            "pct_door_swing": pct(A["d_swing"], A["n_doors"]),
            "wins_total": A["n_wins"], "wins_with_width": A["w_width"],
            "pct_win_width": pct(A["w_width"], A["n_wins"]),
            "pct_win_orient": pct(A["w_orient"], A["n_wins"]),
            "walls_total": A["n_walls"],
            "pct_wall_thickness": pct(A["wall_thick"], A["n_walls"]),
        }
    asp.sort()
    if asp:
        out["aspect_corrected"] = {
            "n": len(asp), "p5": asp[int(.05 * len(asp))], "median": asp[len(asp) // 2],
            "p95": asp[int(.95 * len(asp))], "min": asp[0], "max": asp[-1]}
    json.dump(out, open(a.json_out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("=" * 72, flush=True)
    for arm in ("parsed", "corrected"):
        o = out["arms"][arm]
        print("%-10s n=%5d  realizable=%5.1f%% (축척무시 %5.1f%%)  문폭=%5.1f%% 문스윙=%5.1f%% "
              "창폭=%5.1f%% 축척=%5.1f%%  문0개=%4.1f%%"
              % (arm, o["n"], o["pct_realizable"], o["pct_realizable_noscale"],
                 o["pct_door_width"] or 0, o["pct_door_swing"] or 0,
                 o["pct_win_width"] or 0, o["pct_has_scale"], o["pct_zero_doors"]), flush=True)
    print("flip(realizable) parsed→corrected:", flip, flush=True)
    print("aspect(corrected):", out.get("aspect_corrected"), flush=True)
    print("[json] %s" % a.json_out, flush=True)


if __name__ == "__main__":
    main()
