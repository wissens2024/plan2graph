#!/usr/bin/env python3
"""measure_geom — 보정본의 기하·연결 품질을 **같은 자로** 잰다(읽기 전용).

전/후를 비교하려고 만든 측정기. 아무것도 쓰지 않는다.
--shard I/N 으로 나눠 돌리고, --merge 로 조각 결과를 합쳐 표로 찍는다.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import statistics
import sys

from shapely.geometry import Polygon
from shapely.ops import unary_union

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDITS = os.path.join(ROOT, "data", "staging", "corrected", "edits")

BEDROOMS = ("안방", "침실", "침실1", "침실2", "침실3", "침실4")


def poly_of(r):
    p = r.get("polygon") or []
    if len(p) < 3:
        return None
    try:
        q = Polygon([(float(x), float(y)) for x, y in p])
        if not q.is_valid:
            q = q.buffer(0)
    except Exception:
        return None
    if q.is_empty or q.geom_type != "Polygon":
        return None
    return q


def measure(g):
    """한 도면 → dict"""
    out = {}
    rooms = {str(k): v for k, v in (g.get("rooms") or {}).items()}
    sc = g.get("scale_mm_per_px")
    try:
        sc = float(sc) if sc else None
    except Exception:
        sc = None
    m2 = (sc / 1000.0) ** 2 if sc else None

    vtx = []
    fill = []
    polys = []
    for rid, r in rooms.items():
        p = r.get("polygon") or []
        if len(p) >= 3:
            vtx.append(len(p))
        q = poly_of(r)
        if q is None:
            continue
        polys.append(q)
        b = q.bounds
        ba = (b[2] - b[0]) * (b[3] - b[1])
        if ba > 0:
            fill.append(q.area / ba)
    out["vtx"] = vtx
    out["fill"] = [round(f, 4) for f in fill]
    out["n_rooms"] = len(rooms)

    # 겹침 · 구멍
    if polys and m2:
        try:
            U = unary_union(polys)
            ov = (sum(q.area for q in polys) - U.area) * m2
            out["overlap_m2"] = round(max(0.0, ov), 4)
        except Exception:
            U = None
            out["overlap_m2"] = None
        if U is not None and sc:
            try:
                d = 200.0 / sc            # 200mm 를 px 로
                V = U.buffer(d).buffer(-d)
                geoms = [V] if V.geom_type == "Polygon" else list(getattr(V, "geoms", []))
                h = 0.0
                for gg in geoms:
                    if gg.geom_type != "Polygon":
                        continue
                    for ring in gg.interiors:
                        a = Polygon(ring).area * m2
                        if a > 0.5:
                            h += a
                out["hole_m2"] = round(h, 4)
            except Exception:
                out["hole_m2"] = None
    else:
        out["overlap_m2"] = None
        out["hole_m2"] = None

    # 연결
    edges = g.get("edges") or []
    via = collections.Counter()
    inferred = 0
    bedbed = 0
    for e in edges:
        v = e.get("via") or "?"
        via[v] += 1
        if e.get("open_from") in ("entrance_rule", "fallback_rule", "component_merge"):
            inferred += 1
        if v == "door":
            ra = (rooms.get(str(e.get("from"))) or {}).get("role")
            rb = (rooms.get(str(e.get("to"))) or {}).get("role")
            if ra in BEDROOMS and rb in BEDROOMS:
                bedbed += 1
    out["via"] = dict(via)
    out["n_edges"] = len(edges)
    out["inferred"] = inferred
    out["bedbed_door"] = bedbed

    deg = collections.defaultdict(int)
    adj = collections.defaultdict(set)
    for e in edges:
        a, b = str(e.get("from")), str(e.get("to"))
        adj[a].add(b)
        adj[b].add(a)
        deg[a] += 1
        deg[b] += 1
    iso = [rid for rid in rooms if not adj.get(rid)]
    out["isolated"] = len(iso)
    start = [rid for rid, r in rooms.items() if r.get("role") == "현관"]
    if start:
        seen = set(start)
        st = list(start)
        while st:
            c = st.pop()
            for nb in adj.get(c, ()):
                if nb not in seen:
                    seen.add(nb)
                    st.append(nb)
        out["unreach"] = sum(1 for rid in rooms if rid not in seen)
        out["has_entrance"] = 1
    else:
        out["unreach"] = 0
        out["has_entrance"] = 0

    # 개구부 폭
    ow = {}
    for kind, key in (("door", "doors"), ("win", "windows")):
        n = 0
        filled = []
        for o in (g.get(key) or []):
            n += 1
            w = o.get("width_m")
            if w is not None:
                try:
                    filled.append(round(float(w), 2))
                except Exception:
                    pass
        ow[kind] = (n, filled)
    out["open_w"] = ow
    return out


def accumulate(acc, m):
    acc["plans"] += 1
    acc["rooms"] += m["n_rooms"]
    for v in m["vtx"]:
        acc["vtx_hist"][min(v, 200)] += 1
        acc["vtx_total"] += v
    for f in m["fill"]:
        if f >= 0.98:
            acc["fill_b"]["ge98"] += 1
        elif f >= 0.95:
            acc["fill_b"]["95_98"] += 1
        elif f >= 0.85:
            acc["fill_b"]["85_95"] += 1
        else:
            acc["fill_b"]["lt85"] += 1
    if m["overlap_m2"] is not None:
        acc["ov"].append(m["overlap_m2"])
    if m["hole_m2"] is not None:
        acc["ho"].append(m["hole_m2"])
    for k, v in m["via"].items():
        acc["via"][k] += v
    acc["edges"] += m["n_edges"]
    acc["inferred"] += m["inferred"]
    acc["bedbed"] += m["bedbed_door"]
    acc["iso"] += m["isolated"]
    if m["isolated"]:
        acc["plans_iso"] += 1
    acc["unreach"] += m["unreach"]
    if m["unreach"]:
        acc["plans_unreach"] += 1
    acc["has_entrance"] += m["has_entrance"]
    for kind in ("door", "win"):
        n, filled = m["open_w"][kind]
        acc["ow_n"][kind] += n
        acc["ow_f"][kind] += len(filled)
        for w in filled:
            acc["ow_h"][kind][w] += 1


def new_acc():
    return {"plans": 0, "rooms": 0, "vtx_total": 0,
            "vtx_hist": collections.Counter(),
            "fill_b": collections.Counter(),
            "ov": [], "ho": [],
            "via": collections.Counter(), "edges": 0, "inferred": 0,
            "bedbed": 0, "iso": 0, "plans_iso": 0, "unreach": 0,
            "plans_unreach": 0, "has_entrance": 0,
            "ow_n": collections.Counter(), "ow_f": collections.Counter(),
            "ow_h": {"door": collections.Counter(), "win": collections.Counter()},
            "failed": []}


def acc_json(a):
    return {"plans": a["plans"], "rooms": a["rooms"], "vtx_total": a["vtx_total"],
            "vtx_hist": {str(k): v for k, v in a["vtx_hist"].items()},
            "fill_b": dict(a["fill_b"]), "ov": a["ov"], "ho": a["ho"],
            "via": dict(a["via"]), "edges": a["edges"], "inferred": a["inferred"],
            "bedbed": a["bedbed"], "iso": a["iso"], "plans_iso": a["plans_iso"],
            "unreach": a["unreach"], "plans_unreach": a["plans_unreach"],
            "has_entrance": a["has_entrance"],
            "ow_n": dict(a["ow_n"]), "ow_f": dict(a["ow_f"]),
            "ow_h": {k: {str(x): y for x, y in v.items()} for k, v in a["ow_h"].items()},
            "failed": a["failed"]}


def merge(files):
    a = new_acc()
    for f in files:
        d = json.load(open(f, encoding="utf-8"))
        a["plans"] += d["plans"]
        a["rooms"] += d["rooms"]
        a["vtx_total"] += d["vtx_total"]
        for k, v in d["vtx_hist"].items():
            a["vtx_hist"][int(k)] += v
        for k, v in d["fill_b"].items():
            a["fill_b"][k] += v
        a["ov"] += d["ov"]
        a["ho"] += d["ho"]
        for k, v in d["via"].items():
            a["via"][k] += v
        for k in ("edges", "inferred", "bedbed", "iso", "plans_iso",
                  "unreach", "plans_unreach", "has_entrance"):
            a[k] += d[k]
        for k, v in d["ow_n"].items():
            a["ow_n"][k] += v
        for k, v in d["ow_f"].items():
            a["ow_f"][k] += v
        for kind, h in d["ow_h"].items():
            for x, y in h.items():
                a["ow_h"][kind][float(x)] += y
        a["failed"] += d["failed"]
    return a


def hist_median(c):
    n = sum(c.values())
    if not n:
        return None
    half = n / 2.0
    s = 0
    for k in sorted(c):
        s += c[k]
        if s >= half:
            return k
    return None


def pct(vals, p):
    if not vals:
        return None
    v = sorted(vals)
    i = min(len(v) - 1, int(round(p / 100.0 * (len(v) - 1))))
    return v[i]


def report(a, label):
    P = print
    P("## %s" % label)
    P("도면 %d · 방 %d · 측정 실패 %d" % (a["plans"], a["rooms"], len(a["failed"])))
    nv = sum(a["vtx_hist"].values())
    P("꼭짓점 합계 %d · 방당 평균 %.2f · 방당 중앙값 %s"
      % (a["vtx_total"], a["vtx_total"] / max(nv, 1), hist_median(a["vtx_hist"])))
    tb = sum(a["fill_b"].values()) or 1
    P("bbox 채움률  >=98%% %.1f%% · 95~98 %.1f%% · 85~95 %.1f%% · <85 %.1f%%"
      % (100.0 * a["fill_b"]["ge98"] / tb, 100.0 * a["fill_b"]["95_98"] / tb,
         100.0 * a["fill_b"]["85_95"] / tb, 100.0 * a["fill_b"]["lt85"] / tb))
    ov, ho = a["ov"], a["ho"]
    if ov:
        P("겹침 m2  중앙 %.3f · p90 %.3f · >1m2 %.1f%% (n=%d)"
          % (statistics.median(ov), pct(ov, 90),
             100.0 * sum(1 for v in ov if v > 1.0) / len(ov), len(ov)))
    if ho:
        P("구멍 m2  중앙 %.3f · p90 %.3f · >3m2 %.1f%% (n=%d)"
          % (statistics.median(ho), pct(ho, 90),
             100.0 * sum(1 for v in ho if v > 3.0) / len(ho), len(ho)))
    tv = sum(a["via"].values()) or 1
    P("엣지 %d · via " % a["edges"]
      + " · ".join("%s %d(%.1f%%)" % (k, v, 100.0 * v / tv)
                   for k, v in sorted(a["via"].items(), key=lambda x: -x[1])))
    P("추론 엣지 %d (%.1f%%) · 침실<->침실 문 %d"
      % (a["inferred"], 100.0 * a["inferred"] / max(a["edges"], 1), a["bedbed"]))
    P("고립 방 %d (도면 %d) · 현관 미도달 방 %d (도면 %d) · 현관있는 도면 %d"
      % (a["iso"], a["plans_iso"], a["unreach"], a["plans_unreach"], a["has_entrance"]))
    for kind, nm in (("door", "문"), ("win", "창")):
        n, f = a["ow_n"][kind], a["ow_f"][kind]
        P("%s %d개 · width_m 채움 %.1f%% · 중앙값 %s m"
          % (nm, n, 100.0 * f / max(n, 1), hist_median(a["ow_h"][kind])))
    P("")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--exclude-file", default=None)
    ap.add_argument("--plans-file", default=None)
    ap.add_argument("--dir", default=None,
                    help="edits 대신 이 폴더를 잰다(백업 폴더 전/후 비교용)")
    ap.add_argument("--merge", nargs="*", default=None)
    ap.add_argument("--label", default="측정")
    ap.add_argument("--progress", type=int, default=0)
    args = ap.parse_args()

    if args.merge:
        report(merge(args.merge), args.label)
        return

    ex = set()
    if args.exclude_file and os.path.exists(args.exclude_file):
        ex = {l.strip() for l in open(args.exclude_file, encoding="utf-8") if l.strip()}
    src = args.dir or EDITS
    if args.plans_file:
        pool = sorted({l.strip() for l in open(args.plans_file, encoding="utf-8")
                       if l.strip()})
    else:
        pool = sorted(f[:-5] for f in os.listdir(src) if f.endswith(".json"))
    pool = [p for p in pool if p not in ex]
    if args.shard:
        si, sn = (int(x) for x in args.shard.split("/"))
        pool = [q for k, q in enumerate(pool) if k % sn == si]
    a = new_acc()
    for i, pid in enumerate(pool, 1):
        if args.progress and i % args.progress == 0:
            print("[진행] %d/%d" % (i, len(pool)), file=sys.stderr, flush=True)
        p = os.path.join(src, pid + ".json")
        try:
            g = json.load(open(p, encoding="utf-8"))
            m = measure(g)
        except Exception as exc:
            a["failed"].append([pid, type(exc).__name__, str(exc)[:160]])
            continue
        accumulate(a, m)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(acc_json(a), f, ensure_ascii=False)
    else:
        report(a, args.label)


if __name__ == "__main__":
    main()
