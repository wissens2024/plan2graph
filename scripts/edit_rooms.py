# -*- coding: utf-8 -*-
"""방 추가(폴리곤)·방 확장(사각형 합치기). 겹치면 중단. 백업 후 저장.

usage:
  edit_rooms.py add  <pid> <role> "x,y x,y x,y ..."   [--apply]
  edit_rooms.py grow <pid> <room_id> <x0> <y0> <x1> <y1> [--apply]
"""
import datetime
import json
import os
import shutil
import sys

sys.path.insert(0, "src")
from shapely.geometry import Polygon, box  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

from plan2graph import geomgraph  # noqa: E402

MODE, PID = sys.argv[1], sys.argv[2]
APPLY = "--apply" in sys.argv
P = os.path.join("data/staging/corrected/edits", PID + ".json")
g = json.load(open(P, encoding="utf-8"))
rooms = g["rooms"]
scale = g.get("scale_mm_per_px")


def put(r, q):
    pts = [[round(x, 1), round(y, 1)] for x, y in list(q.exterior.coords)[:-1]]
    r["polygon"] = pts
    b = q.bounds
    r["bbox_px"] = [round(b[0], 1), round(b[1], 1), round(b[2] - b[0], 1), round(b[3] - b[1], 1)]
    r["area_px"] = round(q.area, 1)
    c = q.centroid
    r["centroid"] = [round(c.x, 1), round(c.y, 1)]
    if scale:
        r["area_m2"] = round(q.area * (float(scale) / 1000.0) ** 2, 2)
    if "perimeter_px" in r:
        r["perimeter_px"] = round(q.length, 1)


if MODE == "add":
    ROLE = sys.argv[3]
    pts = [tuple(float(v) for v in p.split(",")) for p in sys.argv[4].split()]
    q = Polygon(pts)
    for rid, r in rooms.items():
        o = Polygon([(a, b) for a, b in r["polygon"]]).buffer(0)
        if q.intersection(o).area > 1.0:
            sys.exit("⚠ %s(%s) 와 %.0f px 겹침 — 중단" % (rid, r.get("role"), q.intersection(o).area))
    nid = str(max([int(k) for k in rooms if str(k).isdigit()] or [0]) + 1)
    r = {"base": ROLE, "role": ROLE, "door_ids": [], "window_ids": [],
         "fixture_ids": [], "fixtures": [], "has_window": False, "n_windows": 0,
         "is_connector": ROLE in geomgraph.CONNECTOR_ROLES,
         "privacy": geomgraph.PRIVACY.get(ROLE, "private"), "touches_exterior": True}
    put(r, q)
    print("추가 id=%s %s  %.0f x %.0f mm  %.2f m2 (점 %d)"
          % (nid, ROLE, (q.bounds[2] - q.bounds[0]) * (scale or 0),
             (q.bounds[3] - q.bounds[1]) * (scale or 0), r.get("area_m2") or 0, len(r["polygon"])))
    target = (nid, r)
else:
    RID = sys.argv[3]
    x0, y0, x1, y1 = (float(v) for v in sys.argv[4:8])
    cur = Polygon([(a, b) for a, b in rooms[RID]["polygon"]]).buffer(0)
    add = box(x0, y0, x1, y1)
    for rid, r in rooms.items():
        if rid == RID:
            continue
        o = Polygon([(a, b) for a, b in r["polygon"]]).buffer(0)
        if add.intersection(o).area > 1.0:
            sys.exit("⚠ %s(%s) 와 %.0f px 겹침 — 중단" % (rid, r.get("role"), add.intersection(o).area))
    q = unary_union([cur, add])
    if q.geom_type != "Polygon":
        sys.exit("⚠ 합치면 조각남 — 중단")
    a0 = rooms[RID].get("area_m2")
    put(rooms[RID], q)
    print("확장 id=%s %s  %.2f → %.2f m2  bbox %s"
          % (RID, rooms[RID].get("role"), a0 or 0, rooms[RID]["area_m2"],
             [int(v) for v in rooms[RID]["bbox_px"]]))
    target = None

if not APPLY:
    print("[드라이런] --apply 로 적용")
    sys.exit(0)

bk = os.path.join("data/staging/corrected/_manual_backup",
                  datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_editrooms")
os.makedirs(bk, exist_ok=True)
shutil.copy(P, os.path.join(bk, PID + ".json"))
if target:
    rooms[target[0]] = target[1]
g["n_rooms"] = len(rooms)
tmp = P + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(g, f, ensure_ascii=False)
os.replace(tmp, P)
print("백업 →", bk, "· 방", len(rooms))
