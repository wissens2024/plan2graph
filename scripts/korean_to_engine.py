#!/usr/bin/env python3
"""
korean_to_engine.py

Convert a Korean unified-graph JSON (plan2graph corrected graph) -> the sovereign
Korean floor-plan diffusion engine training representation (ADR-0006).

The engine reuses DiffPlanner's architecture & dataloaders (node_diff /
adjacency_diff / partitioning_diff). Those loaders consume per-plan JSON records
in a canvas-256 INTEGER coordinate space and do the [-1,1] normalization
themselves at load time (see diffplanner_work/*/rplan_datasets.py:get_datatensor):
    norm = value / canvas_size * 2 - 1
So we EMIT the same canvas-256 integer record shape the engine already reads, but
with two GATE-1 changes vs the old DiffPlanner-RPLAN converter:
    * CONFIGURABLE room cap (MAX_ROOMS, default 24) instead of the hard 8 cap that
      forced dropping Korean-specific rooms.
    * FULL Korean role vocabulary (no collapse to 6 categories, connectors KEPT).

Engine-native fields per record (consumed by the loaders):
    name              : str
    boundary          : [[x,y,oriCode,isNew], ...]  outer outline, entrance edge first
    boundary_expand   : [[x,y] * 40]                ints strictly in (0,256)
    entrance_expand   : [[x,y] * 4]                 entrance-room box
    rooms             : [ {id, category, size, location[x,y],
                           box[x0,y0,x1,y1], r_boundary[[x,y]...], order} ]
    adjacencies       : [[id_a, id_b], ...]

GATE-1 inspection extras (ignored by the engine loaders, kept for analysis/QA):
    max_rooms         : the cap this record was padded against
    num_category      : size of the role vocabulary
    adjacency_matrix  : MAX_ROOMS x MAX_ROOMS 0/1 (padded; -1 not used here)
    rooms_normalized  : per-room features already mapped to [-1,1]
    role_names        : per-room original Korean role (debug)

Single-unit gate: keep plans with EXACTLY one 현관 (entrance). Plans with >1
현관 are merged multi-unit and are SKIPPED here (they should be split upstream).

The boundary / corner / expand-to-40 logic is adapted from
diffplanner_work/korean_convert.py (and dataset/data_preparation.py:expand_polygon)
but generalized for the configurable cap and full role set; r_boundary now keeps
the TRUE room polygon (simplified) instead of degrading to an axis-aligned box.
"""
import json
import os
import copy
import math

import numpy as np
from shapely import geometry as gm
from shapely.ops import unary_union
from shapely.validation import make_valid

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
CANVAS = 256
# GATE-1: 온전(사용) 데이터 기준 방 수 — median 14 · p95 17 · max 23.
# 24는 쓰레기(병합·과다)로 부풀려진 값이라 폐기. p95 커버 = 18.
MAX_ROOMS = 18
MARGIN = 14             # px margin inside the canvas
TARGET_CORNERS = 40     # boundary_expand point count (engine fixed)
ORTHO_BUFFER = 4        # orthogonalization buffer for boundary hull
MAX_ROOM_POLY_PTS = 32  # cap on r_boundary corner count (simplify if over)

# ----------------------------------------------------------------------------
# Korean role -> engine category id (FULL set, no collapse, connectors kept).
# Ids are stable/ordered; the entrance (현관) is NOT a room node in the engine
# (its box becomes entrance_expand), so it has no category id here.
# Rare roles fold into their obvious parent: 파우더룸->화장실, 전용욕실->욕실.
# Unknown future roles fall through to 기타.
# ----------------------------------------------------------------------------
ROLE2CAT = {
    "거실": 0,        # living
    "안방": 1,        # master bedroom
    "침실": 2,        # bedroom
    "주방": 3,        # kitchen
    "화장실": 4,      # toilet
    "욕실": 5,        # bathroom
    "발코니": 6,      # balcony
    "드레스룸": 7,    # dressing room
    "전실": 8,        # antechamber / vestibule (connector)
    "복도": 9,        # corridor (connector)
    "실외기실": 10,   # outdoor-unit room (utility)
    "다목적공간": 11, # multi-purpose
    "기타": 12,       # etc / unknown
    # rare roles folded into parents
    "파우더룸": 4,    # -> 화장실
    "전용욕실": 5,    # -> 욕실
}
NUM_CATEGORY = 13       # distinct category ids 0..12
ENTRANCE_ROLE = "현관"

CAT_NAME = {
    0: "거실", 1: "안방", 2: "침실", 3: "주방", 4: "화장실", 5: "욕실",
    6: "발코니", 7: "드레스룸", 8: "전실", 9: "복도", 10: "실외기실",
    11: "다목적공간", 12: "기타",
}

# When a plan has MORE than MAX_ROOMS mappable rooms, drop the least meaningful
# first. Lower number => dropped first. Connectors/utility/balcony go first;
# living/bedrooms/kitchen/bath are protected.
DROP_PRIORITY = {
    10: 0,  # 실외기실
    9: 1,   # 복도
    8: 2,   # 전실
    6: 3,   # 발코니
    12: 4,  # 기타
    11: 5,  # 다목적공간
    7: 6,   # 드레스룸
    4: 7,   # 화장실
    5: 8,   # 욕실
    3: 9,   # 주방
    2: 10,  # 침실
    1: 11,  # 안방
    0: 12,  # 거실 (never dropped)
}


# ----------------------------------------------------------------------------
# Geometry helpers (adapted from korean_convert.py / data_preparation.py)
# ----------------------------------------------------------------------------
def clip_pt(x, y):
    x = min(CANVAS - 2, max(2, int(round(x))))
    y = min(CANVAS - 2, max(2, int(round(y))))
    return x, y


def find_longest_edge(poly):
    mx, idx, n = 0, -1, len(poly)
    for i in range(n):
        p1, p2 = poly[i], poly[(i + 1) % n]
        L = math.hypot(p1[0] - p2[0], p1[1] - p2[1])
        if L > mx:
            mx, idx = L, i
    return idx


def expand_polygon(poly, target=TARGET_CORNERS):
    poly = copy.deepcopy(poly)
    while len(poly) < target:
        i = find_longest_edge(poly)
        p1, p2 = poly[i], poly[(i + 1) % len(poly)]
        mid = [int(round((p1[0] + p2[0]) / 2)), int(round((p1[1] + p2[1]) / 2))]
        poly.insert(i + 1, mid)
    return poly


def to_xyxy(bbox_px):
    x, y, w, h = bbox_px
    return [x, y, x + w, y + h]


def transform_factory(plan_bbox):
    """src px -> canvas float coords, fit centered with margin. Returns (fn, scale)."""
    bx, by, bw, bh = plan_bbox
    avail = CANVAS - 2 * MARGIN
    s = min(avail / bw, avail / bh) if bw > 0 and bh > 0 else 1.0
    ox = MARGIN + (avail - bw * s) / 2
    oy = MARGIN + (avail - bh * s) / 2

    def tf(px, py):
        return ox + (px - bx) * s, oy + (py - by) * s

    return tf, s


def orthogonalize(geom):
    th = ORTHO_BUFFER
    return geom.buffer(th, join_style=2).buffer(-th, join_style=2)


def _room_poly_src(r):
    pg = r.get("polygon")
    if pg and len(pg) >= 3:
        return gm.Polygon([(p[0], p[1]) for p in pg])
    x0, y0, x1, y1 = to_xyxy(r["bbox_px"])
    return gm.box(x0, y0, x1, y1)


def _largest_polygon(geom):
    if geom.geom_type == "Polygon":
        return geom
    if hasattr(geom, "geoms"):
        polys = [g for g in geom.geoms if g.geom_type == "Polygon"]
        if polys:
            return max(polys, key=lambda g: g.area)
    return None


# ----------------------------------------------------------------------------
# Core converter
# ----------------------------------------------------------------------------
def convert_plan(d, max_rooms=MAX_ROOMS, clean_only=True):
    rooms_dict = d["rooms"]
    roles = [r["role"] for r in rooms_dict.values()]

    # GATE-0: 품질 게이트 — 온전(사용) 데이터만 엔진에 넣는다(ADR-0007).
    # 단일 소스 plan_quality.classify가 현관≠1·발코니/기타과다·거실≠1·거실오라벨·
    # 침실<화장실을 판정. 보정필요는 알바 SVG 보정 큐로(여기서 스킵).
    if clean_only:
        try:
            from plan2graph.plan_quality import classify
        except Exception:
            import sys as _sys
            _here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            _sys.path.insert(0, os.path.join(_here, "src"))
            from plan2graph.plan_quality import classify
        clean, qreasons = classify(d)
        if not clean:
            return None, "fix:" + ",".join(qreasons)

    n_ent = roles.count(ENTRANCE_ROLE)
    if n_ent == 0:
        return None, "no_entrance"
    if n_ent > 1:
        return None, "multi_unit"  # merged multi-unit -> skip (split upstream)

    # collect entrance + mappable rooms
    entrance_room = None
    kept = []  # (orig_id_int, cat, room)
    for rid, r in rooms_dict.items():
        role = r["role"]
        if role == ENTRANCE_ROLE:
            entrance_room = r
            continue
        cat = ROLE2CAT.get(role, 12)  # unknown -> 기타
        kept.append((int(rid), cat, r))

    if entrance_room is None:
        return None, "no_entrance"
    if len(kept) < 2:
        return None, "too_few_rooms"
    if not any(c == 0 for _, c, _ in kept):
        return None, "no_living"

    # cap enforcement: drop lowest priority (never living) if over cap
    if len(kept) > max_rooms:
        def keyf(item):
            _, c, r = item
            return (DROP_PRIORITY.get(c, 4), r.get("area_px", 0))
        ks = sorted(kept, key=keyf)        # drop-first at the front
        living = [it for it in ks if it[1] == 0]
        others = [it for it in ks if it[1] != 0]
        keep_n = max_rooms - len(living)
        if keep_n <= 0:
            return None, "too_many_living"
        kept = living + others[len(others) - keep_n:]
        if len(kept) > max_rooms:
            return None, "still_over_cap"

    # transform from union bbox of kept rooms + entrance
    polys_src = [_room_poly_src(r) for _, _, r in kept]
    ex0, ey0, ex1, ey1 = to_xyxy(entrance_room["bbox_px"])
    ent_poly_src = gm.box(ex0, ey0, ex1, ey1)
    allgeom = unary_union([p.buffer(0) for p in polys_src] + [ent_poly_src])
    minx, miny, maxx, maxy = allgeom.bounds
    if maxx - minx <= 0 or maxy - miny <= 0:
        return None, "degenerate_plan"
    tf, scale = transform_factory((minx, miny, maxx - minx, maxy - miny))

    # build room records in canvas space
    rooms_out = []
    canvas_polys = []
    orig_to_new = {}
    for newid, (rid, cat, r) in enumerate(kept):
        pg = r.get("polygon")
        if pg and len(pg) >= 3:
            pts = [tf(p[0], p[1]) for p in pg]
        else:
            x0, y0, x1, y1 = to_xyxy(r["bbox_px"])
            pts = [tf(x0, y0), tf(x1, y0), tf(x1, y1), tf(x0, y1)]
        poly = gm.Polygon(pts)
        if not poly.is_valid:
            poly = _largest_polygon(make_valid(poly))
            if poly is None:
                return None, "bad_room_poly"
        cx0, cy0, cx1, cy1 = poly.bounds
        cx0i, cy0i = clip_pt(cx0, cy0)
        cx1i, cy1i = clip_pt(cx1, cy1)
        if cx1i - cx0i < 3 or cy1i - cy0i < 3:
            return None, "degenerate_room"
        box = [cx0i, cy0i, cx1i, cy1i]
        size = int((cx1i - cx0i) * (cy1i - cy0i))
        loc = [int((cx0i + cx1i) / 2), int((cy0i + cy1i) / 2)]

        # r_boundary: keep TRUE simplified room polygon (clipped ints, dedup)
        rpoly = poly.simplify(1.5, preserve_topology=True)
        rpoly = _largest_polygon(rpoly) or poly
        rx, ry = rpoly.exterior.xy
        rb = [list(clip_pt(x, y)) for x, y in zip(rx, ry)]
        # dedup consecutive + drop closing dup
        dd = []
        for p in rb:
            if not dd or dd[-1] != p:
                dd.append(p)
        if len(dd) > 1 and dd[0] == dd[-1]:
            dd = dd[:-1]
        if len(dd) < 4:  # fallback to axis box
            dd = [[cx0i, cy0i], [cx0i, cy1i], [cx1i, cy1i], [cx1i, cy0i]]
        if len(dd) > MAX_ROOM_POLY_PTS:
            g2 = gm.Polygon(dd).simplify(2.5, preserve_topology=True)
            g2 = _largest_polygon(g2)
            if g2 is not None:
                gx, gy = g2.exterior.xy
                dd = [list(clip_pt(x, y)) for x, y in zip(gx, gy)][:-1]

        rooms_out.append({
            "id": newid, "category": int(cat), "size": size,
            "location": loc, "box": box, "r_boundary": dd, "order": newid,
            "_area": size, "_role": r["role"],
        })
        orig_to_new[rid] = newid
        canvas_polys.append(gm.box(*box))

    if len(rooms_out) < 2 or len(rooms_out) > max_rooms:
        return None, "room_count"

    # paint order: largest first (valid 0-based permutation)
    rank = sorted(range(len(rooms_out)), key=lambda i: -rooms_out[i]["_area"])
    for paint_pos, ridx in enumerate(rank):
        rooms_out[ridx]["order"] = paint_pos

    # ---- boundary: outer outline from union of true room polys + entrance ----
    src_union = unary_union(
        [_room_poly_src(r).buffer(0) for _, _, r in kept] + [ent_poly_src]
    )
    src_union = _largest_polygon(src_union)
    if src_union is None:
        return None, "no_boundary"
    # to canvas space
    bx, by = src_union.exterior.xy
    cpts = [tf(x, y) for x, y in zip(bx, by)]
    merged = orthogonalize(gm.Polygon(cpts).buffer(0))
    merged = _largest_polygon(merged)
    if merged is None:
        return None, "no_boundary"
    try:
        merged = merged.simplify(2.0, preserve_topology=True)
        xs, ys = merged.exterior.xy
    except Exception:
        return None, "no_boundary"
    bcoords = [list(clip_pt(x, y)) for x, y in zip(xs, ys)]
    dd = []
    for p in bcoords:
        if not dd or dd[-1] != p:
            dd.append(p)
    if len(dd) > 1 and dd[0] == dd[-1]:
        dd = dd[:-1]
    bcoords = dd
    if len(bcoords) < 4:
        return None, "boundary_too_small"
    if len(bcoords) > TARGET_CORNERS:
        g2 = _largest_polygon(gm.Polygon(bcoords).simplify(4.0, preserve_topology=True))
        if g2 is None:
            return None, "boundary_simplify_fail"
        xs, ys = g2.exterior.xy
        bcoords = [list(clip_pt(x, y)) for x, y in zip(xs, ys)][:-1]
        if len(bcoords) > TARGET_CORNERS:
            return None, "boundary_too_many_corners"

    poly_b = gm.Polygon(bcoords)
    if not poly_b.is_valid or poly_b.area < 50:
        return None, "boundary_invalid"

    # ---- entrance edge: boundary edge nearest entrance center; rotate first ----
    ecx, ecy = tf((ex0 + ex1) / 2, (ey0 + ey1) / 2)
    n = len(bcoords)
    best_i, best_d = 0, 1e18
    for i in range(n):
        p1, p2 = bcoords[i], bcoords[(i + 1) % n]
        mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
        dd2 = (mx - ecx) ** 2 + (my - ecy) ** 2
        if dd2 < best_d:
            best_d, best_i = dd2, i
    rot = bcoords[best_i:] + bcoords[:best_i]
    p0, p1 = rot[0], rot[1]
    cenx = sum(p[0] for p in rot) / n
    ceny = sum(p[1] for p in rot) / n
    emx, emy = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
    if p0[0] == p1[0]:        # vertical edge
        ori = 3 if cenx > emx else 1
    else:                     # horizontal edge
        ori = 0 if ceny > emy else 2

    boundary4 = [[p[0], p[1], int(ori if k == 0 else 0), 0] for k, p in enumerate(rot)]

    # boundary_expand: expand rotated outline to 40 pts, all strictly inside canvas
    be = expand_polygon([[p[0], p[1]] for p in rot], TARGET_CORNERS)
    be = [list(clip_pt(x, y)) for x, y in be]
    if len(be) != TARGET_CORNERS:
        return None, "boundary_corner_count"
    if not all(0 < x < CANVAS and 0 < y < CANVAS for x, y in be):
        return None, "boundary_out_of_range"

    # entrance_expand: 4-pt box of entrance room
    epts = [tf(ex0, ey0), tf(ex1, ey0), tf(ex1, ey1), tf(ex0, ey1)]
    exs = [p[0] for p in epts]
    eys = [p[1] for p in epts]
    emnx, emny = clip_pt(min(exs), min(eys))
    emxx, emxy = clip_pt(max(exs), max(eys))
    entrance_expand = [[emnx, emny], [emxx, emny], [emxx, emxy], [emnx, emxy]]

    # ---- adjacencies (remap to new ids; drop entrance edges) ----
    adjacencies = []
    seen = set()
    for e in d.get("edges", []):
        a, b = e.get("from"), e.get("to")
        if a in orig_to_new and b in orig_to_new:
            na, nb = orig_to_new[a], orig_to_new[b]
            if na == nb:
                continue
            key = (min(na, nb), max(na, nb))
            if key in seen:
                continue
            seen.add(key)
            adjacencies.append([na, nb])
    if not adjacencies:
        return None, "no_adjacencies"

    # ---- GATE-1 inspection extras ----
    adj_mat = np.zeros((max_rooms, max_rooms), dtype=int)
    for na, nb in adjacencies:
        adj_mat[na, nb] = 1
        adj_mat[nb, na] = 1

    canvas_area = CANVAS * CANVAS
    rooms_normalized = []
    for r in rooms_out:
        rooms_normalized.append({
            "id": r["id"],
            "category": r["category"],
            "size": r["size"] / canvas_area * 2 - 1,
            "location": [r["location"][0] / CANVAS * 2 - 1,
                         r["location"][1] / CANVAS * 2 - 1],
            "box": [c / CANVAS * 2 - 1 for c in r["box"]],
        })

    role_names = [r.pop("_role") for r in rooms_out]
    for r in rooms_out:
        r.pop("_area", None)

    out = {
        "name": d["plan_id"],
        "max_rooms": max_rooms,
        "num_category": NUM_CATEGORY,
        "boundary": boundary4,
        "boundary_expand": be,
        "entrance_expand": entrance_expand,
        "rooms": rooms_out,
        "adjacencies": adjacencies,
        # inspection extras (ignored by engine loaders):
        "adjacency_matrix": adj_mat.tolist(),
        "rooms_normalized": rooms_normalized,
        "role_names": role_names,
    }
    return out, "ok"


# ----------------------------------------------------------------------------
# CLI / batch
# ----------------------------------------------------------------------------
def _iter_graph_files():
    try:
        from plan2graph import topoedit
        gdir = str(topoedit.GRAPHS_DIR)
    except Exception:
        gdir = os.path.expanduser("~/plan2graph/data/staging/corrected/graphs")
    for fn in sorted(os.listdir(gdir)):
        if fn.startswith("APT_") and fn.endswith(".json"):
            yield os.path.join(gdir, fn)


def main():
    import argparse
    from collections import Counter
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=2000,
                    help="max single-unit APT files to SCAN")
    ap.add_argument("--max-rooms", type=int, default=MAX_ROOMS)
    ap.add_argument("--all", dest="clean_only", action="store_false",
                    help="품질 게이트 끄고 전수 변환(기본=온전 데이터만)")
    ap.add_argument("--out", default="/tmp/korean_engine_dataset.json")
    ap.add_argument("--samples", type=int, default=5,
                    help="write N converted examples individually for inspection")
    ap.add_argument("--sample-dir", default="/tmp/korean_engine_samples")
    # ── 데이터셋 구성(variant) 옵션 — 같은 엔진을 다른 데이터로 학습/비교하기 위함 ──
    ap.add_argument("--provenance", default="",
                    help="provenance.source 필터(콤마): dual,spa_only,str_only,objocr (기본=전체)")
    ap.add_argument("--variant", default="",
                    help="구성 이름. 지정 시 <engine-root>/dataset_json_<variant>/에 "
                         "train/test/val 배치(엔진 학습 입력). 미지정 시 --out 단일파일.")
    ap.add_argument("--engine-root",
                    default=os.path.expanduser("~/diffplanner_work/dataset"),
                    help="--variant 배치 루트")
    ap.add_argument("--test-n", type=int, default=1000, help="--variant 시 test/val 세대수")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    prov_filter = set(s.strip() for s in args.provenance.split(",") if s.strip()) or None

    reasons = Counter()
    rc_hist = Counter()
    cat_cov = Counter()
    out = []
    scanned = 0
    for f in _iter_graph_files():
        if scanned >= args.limit:
            break
        scanned += 1
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            reasons["load_err"] += 1
            continue
        if prov_filter is not None:                      # 구성: provenance.source 필터
            src = (d.get("provenance") or {}).get("source")
            if src not in prov_filter:
                reasons["prov_skip"] += 1
                continue
        rec, why = convert_plan(d, max_rooms=args.max_rooms,
                                clean_only=args.clean_only)
        reasons[why] += 1
        if rec is not None:
            out.append(rec)
            rc_hist[len(rec["rooms"])] += 1
            for c in (r["category"] for r in rec["rooms"]):
                cat_cov[c] += 1

    print(f"scanned: {scanned}")
    print(f"converted(사용): {len(out)}  yield={100.0*len(out)/max(scanned,1):.1f}%")
    # 품질 게이트(보정필요)와 변환 실패를 분리 집계
    fix_reasons = Counter()
    n_fix = 0
    other = Counter()
    for why, c in reasons.items():
        if why.startswith("fix:"):
            n_fix += c
            for tag in why[4:].split(","):
                fix_reasons[tag] += c
        elif why != "ok":
            other[why] += c
    print(f"보정필요(품질 게이트): {n_fix}  "
          f"({100.0*n_fix/max(scanned,1):.1f}%)")
    print("  보정필요 사유(중복 포함):", dict(fix_reasons.most_common()))
    print("변환 실패(품질 통과 후):", dict(other.most_common()))
    print("room-count of converted (count -> #plans):",
          dict(sorted(rc_hist.items())))
    print("category coverage in converted (cat -> #rooms):")
    for c in sorted(cat_cov):
        print(f"   {c:2d} {CAT_NAME.get(c,'?'):8s} {cat_cov[c]}")

    if args.variant:
        # 구성 배치: 셔플 후 train/test/val 분할 → dataset_json_<variant>/ (엔진 학습 입력)
        import random as _rnd
        _rnd.Random(args.seed).shuffle(out)
        tn = min(args.test_n, len(out) // 5)
        test, train = out[:tn], out[tn:]
        vdir = os.path.join(args.engine_root, f"dataset_json_{args.variant}")
        os.makedirs(vdir, exist_ok=True)
        for nm, recs in (("train", train), ("test", test), ("val", test)):
            json.dump(recs, open(os.path.join(vdir, f"data_{nm}.json"), "w",
                                 encoding="utf-8"), ensure_ascii=False)
        # meta.json — 콤보박스가 싸게 읽는 카운트(데이터 수는 변하므로 변환 때마다 갱신)
        json.dump({"variant": args.variant, "provenance": args.provenance or "전체",
                   "clean_only": args.clean_only, "n_train": len(train),
                   "n_test": len(test), "n_total": len(out)},
                  open(os.path.join(vdir, "meta.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"[variant '{args.variant}'] prov={args.provenance or '전체'} "
              f"clean_only={args.clean_only} → train={len(train)} test={len(test)}  {vdir}")
    else:
        json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False)
        print("wrote", args.out, "(", len(out), "records )")
        os.makedirs(args.sample_dir, exist_ok=True)
        for rec in out[:args.samples]:
            p = os.path.join(args.sample_dir, rec["name"] + ".json")
            json.dump(rec, open(p, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
        print(f"wrote {min(args.samples, len(out))} samples to {args.sample_dir}")


if __name__ == "__main__":
    main()
