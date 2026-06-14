#!/usr/bin/env python3
"""
korean_convert.py  (runs on server: ~/diffplanner_work/korean_convert.py)

Convert Korean apartment plans (plan2graph corrected graphs) -> DiffPlanner
training-data JSON format (RPLAN-style).

DiffPlanner per-plan record (what the train dataloaders consume):
  {
    "name": str,
    "boundary": [...],                     # raw boundary (not used by loaders, kept for compat)
    "boundary_expand": [[x,y]*40],         # 40 points, ints in (0,256)
    "entrance_expand": [[x,y]*4],          # 4 points
    "rooms": [ {id, category(0..5), size, location[x,y], box[x0,y0,x1,y1],
                r_boundary[[x,y]...], order} ],   # <=8 rooms
    "adjacencies": [[id_a, id_b], ...]
  }

Constraints from the loaders / data_preparation.py:
  * max_num_rooms = 8, canvas_size = 256
  * boundary_expand must have exactly 40 points, all strictly inside (0,256)
  * room boxes / r_boundary must be strictly inside (0,256)
  * num_category = 6  (0 living,1 bedroom,2 kitchen,3 bathroom,4 balcony,5 storage)

Strategy (single-unit, <=8 mappable rooms):
  * Keep plans with exactly 1 현관 (entrance) and 1 거실 (living).
  * Map Korean roles -> 6 categories. Connector rooms (복도/전실) dropped.
  * If >8 rooms after mapping, drop lowest-priority small rooms (storage, balcony)
    until <=8; if still >8, skip the plan.
  * Boundary = orthogonal hull of union of room polygons, simplified+resampled to 40 pts.
  * Entrance = box of the 현관 room (clipped).
  * Coordinates: translate+scale all geometry so the plan bbox fits a centered
    box inside the 256 canvas with a margin.
"""
import json, glob, os, sys, copy, math
import numpy as np
from shapely import geometry as gm
from shapely.ops import unary_union
from shapely.validation import make_valid

CANVAS = 256
MAX_ROOMS = 8
MARGIN = 14          # px margin inside canvas
TARGET_CORNERS = 40

# Korean role -> DiffPlanner category (0..5) ; None => drop room (connector)
ROLE2CAT = {
    "거실": 0,
    "안방": 1, "침실": 1,
    "주방": 2,
    "화장실": 3, "욕실": 3, "전용욕실": 3, "파우더룸": 3,
    "발코니": 4,
    "드레스룸": 5, "실외기실": 5, "다목적공간": 5, "창고": 5, "기타": 5,
    # connectors -> drop
    "복도": None, "전실": None, "현관": "ENTRANCE",
}
# drop priority when over 8 rooms (drop these first)  category -> priority(lower=drop first)
DROP_PRIORITY = {5: 0, 4: 1, 3: 2, 2: 3, 1: 4, 0: 5}


def clip_pt(x, y):
    x = min(CANVAS - 2, max(2, int(round(x))))
    y = min(CANVAS - 2, max(2, int(round(y))))
    return x, y


def find_longest_edge(poly):
    mx = 0; idx = -1
    n = len(poly)
    for i in range(n):
        p1 = poly[i]; p2 = poly[(i + 1) % n]
        L = math.hypot(p1[0] - p2[0], p1[1] - p2[1])
        if L > mx:
            mx = L; idx = i
    return idx


def expand_polygon(poly, target=TARGET_CORNERS):
    poly = copy.deepcopy(poly)
    while len(poly) < target:
        i = find_longest_edge(poly)
        p1 = poly[i]; p2 = poly[(i + 1) % len(poly)]
        mid = [int(round((p1[0] + p2[0]) / 2)), int(round((p1[1] + p2[1]) / 2))]
        poly.insert(i + 1, mid)
    return poly


def to_xyxy(bbox_px):
    x, y, w, h = bbox_px
    return [x, y, x + w, y + h]


def transform_factory(plan_bbox):
    """Return fn mapping source px -> canvas int coords, fit centered w/ margin."""
    bx, by, bw, bh = plan_bbox
    avail = CANVAS - 2 * MARGIN
    s = min(avail / bw, avail / bh) if bw > 0 and bh > 0 else 1.0
    # center
    ox = MARGIN + (avail - bw * s) / 2
    oy = MARGIN + (avail - bh * s) / 2

    def tf(px, py):
        x = ox + (px - bx) * s
        y = oy + (py - by) * s
        return x, y
    return tf, s


def orthogonalize(geom, big=False):
    th = 4
    g = geom.buffer(th, join_style=2).buffer(-th, join_style=2)
    return g


def convert_plan(d):
    rooms_dict = d["rooms"]
    roles = [r["role"] for r in rooms_dict.values()]
    # single-unit gate
    if roles.count("현관") != 1 or roles.count("거실") != 1:
        return None, "not_single_unit"

    # gather mappable rooms (exclude entrance & connectors); track entrance room
    entrance_room = None
    kept = []   # (orig_id_str, cat, room)
    for rid, r in rooms_dict.items():
        cat = ROLE2CAT.get(r["role"], 5)  # unknown roles -> storage
        if cat == "ENTRANCE":
            entrance_room = r
            continue
        if cat is None:
            continue
        kept.append((rid, cat, r))

    if entrance_room is None:
        return None, "no_entrance"
    if len(kept) < 2:
        return None, "too_few_rooms"

    # ensure exactly one living present
    if not any(c == 0 for _, c, _ in kept):
        return None, "no_living"

    # if > MAX_ROOMS, drop lowest priority (but never drop living)
    if len(kept) > MAX_ROOMS:
        # sort by (drop_priority asc, area asc) so smallest low-prio dropped first
        def keyf(item):
            _, c, r = item
            return (DROP_PRIORITY[c], r.get("area_px", 0))
        kept_sorted = sorted(kept, key=keyf)
        # keep living always
        living = [it for it in kept_sorted if it[1] == 0]
        others = [it for it in kept_sorted if it[1] != 0]
        keep_n = MAX_ROOMS - len(living)
        kept = living + others[len(others) - keep_n:] if keep_n > 0 else living
        if len(kept) > MAX_ROOMS:
            return None, "still_over_8"

    # build transform from union bbox of kept rooms + entrance (use polygons)
    polys_src = []
    for rid, cat, r in kept:
        pg = r.get("polygon")
        if pg and len(pg) >= 3:
            polys_src.append(gm.Polygon([(p[0], p[1]) for p in pg]))
        else:
            x0, y0, x1, y1 = to_xyxy(r["bbox_px"])
            polys_src.append(gm.box(x0, y0, x1, y1))
    # entrance poly
    ex0, ey0, ex1, ey1 = to_xyxy(entrance_room["bbox_px"])
    ent_poly_src = gm.box(ex0, ey0, ex1, ey1)

    allgeom = unary_union([p.buffer(0) for p in polys_src] + [ent_poly_src])
    minx, miny, maxx, maxy = allgeom.bounds
    tf, scale = transform_factory((minx, miny, maxx - minx, maxy - miny))

    # transform room polys -> canvas, build boxes & r_boundary
    rooms_out = []
    canvas_polys = []
    orig_id_to_newid = {}
    for newid, (rid, cat, r) in enumerate(kept):
        pg = r.get("polygon")
        if pg and len(pg) >= 3:
            pts = [tf(p[0], p[1]) for p in pg]
        else:
            x0, y0, x1, y1 = to_xyxy(r["bbox_px"])
            pts = [tf(x0, y0), tf(x1, y0), tf(x1, y1), tf(x0, y1)]
        poly = gm.Polygon(pts)
        if not poly.is_valid:
            poly = make_valid(poly)
            if poly.geom_type != "Polygon":
                # take largest
                if hasattr(poly, "geoms"):
                    poly = max([g for g in poly.geoms if g.geom_type == "Polygon"],
                               key=lambda g: g.area, default=None)
                if poly is None:
                    return None, "bad_room_poly"
        cx0, cy0, cx1, cy1 = poly.bounds
        cx0i, cy0i = clip_pt(cx0, cy0); cx1i, cy1i = clip_pt(cx1, cy1)
        if cx1i - cx0i < 3 or cy1i - cy0i < 3:
            return None, "degenerate_room"
        box = [cx0i, cy0i, cx1i, cy1i]
        size = int((cx1i - cx0i) * (cy1i - cy0i))
        loc = [int((cx0i + cx1i) / 2), int((cy0i + cy1i) / 2)]
        # r_boundary: clipped integer exterior coords (axis-aligned box fallback)
        rb = [[cx0i, cy0i], [cx0i, cy1i], [cx1i, cy1i], [cx1i, cy0i]]
        rooms_out.append({
            "id": newid, "category": int(cat), "size": size,
            "location": loc, "box": box, "r_boundary": rb,
            "order": newid,  # 0-based permutation index (viz reorders rooms by this)
            "_area": size,
        })
        orig_id_to_newid[int(rid)] = newid
        canvas_polys.append(gm.box(*box))

    if len(rooms_out) < 2 or len(rooms_out) > MAX_ROOMS:
        return None, "room_count"

    # assign a valid 0-based permutation for "order": largest rooms painted first
    rank = sorted(range(len(rooms_out)), key=lambda i: -rooms_out[i]["_area"])
    for paint_pos, ridx in enumerate(rank):
        rooms_out[ridx]["order"] = paint_pos
    for r in rooms_out:
        r.pop("_area", None)

    # boundary from union of room boxes -> orthogonal hull -> real corner polygon
    merged = unary_union(canvas_polys)
    merged = orthogonalize(merged)
    if merged.geom_type == "MultiPolygon":
        merged = max(merged.geoms, key=lambda g: g.area)
    try:
        merged = merged.simplify(2.0, preserve_topology=True)
        xs, ys = merged.exterior.xy
    except Exception:
        return None, "no_boundary"
    bcoords = [[int(round(x)), int(round(y))] for x, y in zip(xs, ys)][:-1]
    bcoords = [list(clip_pt(x, y)) for x, y in bcoords]
    # dedup consecutive
    dd = []
    for p in bcoords:
        if not dd or dd[-1] != p:
            dd.append(p)
    if dd and dd[0] == dd[-1]:
        dd = dd[:-1]
    bcoords = dd
    if len(bcoords) < 4:
        return None, "boundary_too_small"
    if len(bcoords) > TARGET_CORNERS:
        g2 = gm.Polygon(bcoords).simplify(4.0, preserve_topology=True)
        xs, ys = g2.exterior.xy
        bcoords = [list(clip_pt(x, y)) for x, y in zip(xs, ys)][:-1]
        if len(bcoords) > TARGET_CORNERS:
            return None, "boundary_too_many_corners"

    poly_b = gm.Polygon(bcoords)
    if not poly_b.is_valid or poly_b.area < 50:
        return None, "boundary_invalid"
    # ensure CCW orientation is consistent: use shapely orient -> CW? RPLAN order
    # We'll keep as-is; alignment only needs door edge first + isNew flags.

    # --- entrance edge: boundary edge nearest the entrance room center ---
    ecx, ecy = tf((ex0 + ex1) / 2, (ey0 + ey1) / 2)
    n = len(bcoords)
    best_i, best_d = 0, 1e18
    for i in range(n):
        p1 = bcoords[i]; p2 = bcoords[(i + 1) % n]
        mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
        dd2 = (mx - ecx) ** 2 + (my - ecy) ** 2
        if dd2 < best_d:
            best_d = dd2; best_i = i
    # rotate so entrance edge is first (points 0->1)
    rot = bcoords[best_i:] + bcoords[:best_i]
    p0 = rot[0]; p1 = rot[1]
    # determine door orientation: which way is interior relative to the edge
    cenx = sum(p[0] for p in rot) / n
    ceny = sum(p[1] for p in rot) / n
    emx, emy = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
    if p0[0] == p1[0]:  # vertical edge
        # interior is to +x (ori 3) or -x (ori 1)
        ori = 3 if cenx > emx else 1
    else:  # horizontal edge
        # interior is +y (ori 0) or -y (ori 2)
        ori = 0 if ceny > emy else 2

    # build 4-column boundary: [x, y, oriCode, isNew]; isNew=0 for all real corners
    boundary4 = []
    for k, p in enumerate(rot):
        code = ori if k == 0 else 0
        boundary4.append([p[0], p[1], int(code), 0])

    # boundary_expand: expand the (rotated) real polygon to 40 pts, with isNew flags
    rot_xy = [[p[0], p[1]] for p in rot]
    be = expand_polygon(copy.deepcopy(rot_xy), TARGET_CORNERS)
    be = [list(clip_pt(x, y)) for x, y in be]
    if len(be) != TARGET_CORNERS:
        return None, "boundary_corner_count"
    if not all(0 < x < CANVAS and 0 < y < CANVAS for x, y in be):
        return None, "boundary_out_of_range"
    boundary_expand = be

    # entrance_expand: 4-pt box of entrance room transformed
    epts = [tf(ex0, ey0), tf(ex1, ey0), tf(ex1, ey1), tf(ex0, ey1)]
    exs = [p[0] for p in epts]; eys = [p[1] for p in epts]
    emnx, emny = clip_pt(min(exs), min(eys))
    emxx, emxy = clip_pt(max(exs), max(eys))
    entrance_expand = [[emnx, emny], [emxx, emny], [emxx, emxy], [emnx, emxy]]

    # adjacencies (remap to new ids; entrance edges dropped since entrance not a node)
    adjacencies = []
    seen = set()
    for e in d.get("edges", []):
        a = e.get("from"); b = e.get("to")
        if a in orig_id_to_newid and b in orig_id_to_newid:
            na, nb = orig_id_to_newid[a], orig_id_to_newid[b]
            if na == nb:
                continue
            key = (min(na, nb), max(na, nb))
            if key in seen:
                continue
            seen.add(key)
            adjacencies.append([na, nb])
    if not adjacencies:
        return None, "no_adjacencies"

    out = {
        "name": d["plan_id"],
        "boundary": boundary4,
        "boundary_expand": boundary_expand,
        "entrance_expand": entrance_expand,
        "rooms": rooms_out,
        "adjacencies": adjacencies,
    }
    return out, "ok"


def main():
    src_glob = os.path.expanduser("~/plan2graph/data/staging/corrected/graphs/APT_*.json")
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
    out_path = sys.argv[2] if len(sys.argv) > 2 else "dataset/dataset_json/data_korean.json"

    files = sorted(glob.glob(src_glob))
    print(f"found {len(files)} APT files; scanning for up to {limit} convertible single-unit plans")
    from collections import Counter
    reasons = Counter()
    out = []
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            reasons["load_err"] += 1
            continue
        rec, why = convert_plan(d)
        reasons[why] += 1
        if rec is not None:
            out.append(rec)
            if len(out) >= limit:
                break
    print("reasons:", dict(reasons.most_common()))
    print("converted:", len(out))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False)
    print("wrote", out_path)


if __name__ == "__main__":
    main()
