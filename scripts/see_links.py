#!/usr/bin/env python3
"""기하연결을 사람이 보는 것과 같게 그려서 PNG 로 떨군다 — 내가 눈으로 확인하려고."""
import json, os, sys, math
sys.path.insert(0, "/home/ju/plan2graph")
sys.path.insert(0, "/home/ju/plan2graph/src")
from PIL import Image, ImageDraw
import scripts.edit_server as es

pid = sys.argv[1]
out = sys.argv[2]
g = json.load(open(os.path.join(es.EDITS, pid + ".json"), encoding="utf-8"))
R = {str(k): v for k, v in (g.get("rooms") or {}).items()}

es._build_png_index()
b = es._png_bytes(pid)
import io
im = Image.open(io.BytesIO(b)).convert("RGB") if b else None

xs, ys = [], []
for r in R.values():
    for x, y in (r.get("polygon") or []):
        xs.append(x); ys.append(y)
M = 60
x0, y0, x1, y1 = int(min(xs)) - M, int(min(ys)) - M, int(max(xs)) + M, int(max(ys)) + M
if im is None:
    im = Image.new("RGB", (x1 - x0, y1 - y0), "white"); off = (x0, y0)
else:
    im = im.crop((x0, y0, x1, y1))
S = 2
im = im.resize((im.width * S, im.height * S), Image.LANCZOS)
d = ImageDraw.Draw(im, "RGBA")
def T(p): return ((p[0] - x0) * S, (p[1] - y0) * S)

for rid, r in R.items():
    pol = [T(p) for p in (r.get("polygon") or [])]
    if len(pol) > 2:
        d.polygon(pol, outline=(90, 90, 90, 255))
    c = r.get("centroid")
    if c:
        d.text(T(c), "%s %s" % (r.get("role"), rid), fill=(0, 0, 200, 255))

def ctr(rid):
    r = R.get(str(rid))
    return T(r["centroid"]) if r and r.get("centroid") else None

TAIL = 26 * S
for e in (g.get("edges") or []):
    if e.get("via") != "open": continue
    a, b2 = ctr(e["from"]), ctr(e["to"])
    if not a or not b2: continue
    m = ((a[0] + b2[0]) / 2, (a[1] + b2[1]) / 2)
    for c in (a, b2):
        dx, dy = c[0] - m[0], c[1] - m[1]; L = math.hypot(dx, dy) or 1
        d.line([m, (m[0] + dx / L * TAIL, m[1] + dy / L * TAIL)], fill=(37, 99, 235, 255), width=3)
    d.ellipse([m[0] - 6, m[1] - 6, m[0] + 6, m[1] + 6], outline=(37, 99, 235, 255), width=3)

for dr in (g.get("doors") or []):
    p = dr.get("position")
    if not p: continue
    q = T(p)
    for rid in (dr.get("connects") or []):
        c = ctr(rid)
        if not c: continue
        dx, dy = c[0] - q[0], c[1] - q[1]; L = math.hypot(dx, dy) or 1
        d.line([q, (q[0] + dx / L * TAIL, q[1] + dy / L * TAIL)], fill=(22, 163, 74, 255), width=4)
    if dr.get("to_outside"):
        d.line([q, (q[0], q[1] - TAIL)], fill=(100, 116, 139, 255), width=3)
        d.text((q[0] + 4, q[1] - TAIL - 14), "외부", fill=(100, 116, 139, 255))
    d.rectangle([q[0] - 7, q[1] - 7, q[0] + 7, q[1] + 7], outline=(22, 163, 74, 255), width=3)
    d.text((q[0] + 9, q[1] + 6), dr.get("id") or "", fill=(22, 163, 74, 255))

for w in (g.get("windows") or []):
    p = w.get("position")
    if not p: continue
    q = T(p)
    d.rectangle([q[0] - 5, q[1] - 5, q[0] + 5, q[1] + 5], outline=(147, 51, 234, 255), width=3)

im.save(out)
print(out, im.size, "crop", (x0, y0, x1, y1))
