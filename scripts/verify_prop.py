#!/usr/bin/env python3
"""전파가 제대로 됐나 — 개수가 아니라 **내용**을 대조한다.

개수만 같고 내용이 다를 수 있다. 방 역할 집합, 문이 잇는 방 쌍, 엣지 쌍과 종류,
창이 속한 방, 그리고 좌표까지 본다. 좌표는 사본마다 평행이동/미러가 걸릴 수 있으므로
**원본 대비 일정한 변환**인지를 본다(제각각이면 틀린 것이다).
"""
import json, os, sys, collections
ROOT = "/home/ju/plan2graph"
E = os.path.join(ROOT, "data", "staging", "corrected", "edits")


def load(p):
    return json.load(open(os.path.join(E, p + ".json"), encoding="utf-8"))


def sig(g):
    R = {str(k): v for k, v in (g.get("rooms") or {}).items()}
    roles = collections.Counter(v.get("role") for v in R.values())
    def nm(r):
        return (R.get(str(r)) or {}).get("role") or ("외부" if r is None else "?")
    doors = sorted(tuple(sorted(nm(x) for x in (d.get("connects") or []))) or ("외부",)
                   for d in (g.get("doors") or []))
    ext = sorted(nm((d.get("connects") or [None])[0])
                 for d in (g.get("doors") or []) if d.get("to_outside"))
    edges = sorted((tuple(sorted((nm(e["from"]), nm(e["to"])))), e.get("via"))
                   for e in (g.get("edges") or []))
    wins = sorted(nm(w.get("belongs_to")) for w in (g.get("windows") or []))
    return roles, doors, ext, edges, wins


def coords(g):
    out = []
    for d in sorted((g.get("doors") or []), key=lambda d: str(d.get("id"))):
        p = d.get("position")
        if p:
            out.append((str(d.get("id")), p[0], p[1]))
    return out


log = json.load(open(os.path.join(ROOT, "data", "propagated_plans.json"), encoding="utf-8"))
seen, bad, n = {}, 0, 0
for j in log:
    seen[j["sig"]] = j
print("그룹 %d개 · 전파본 %d장 대조\n" % (len(seen), sum(len(j["targets"]) for j in seen.values())))
for j in seen.values():
    src = j["source"]
    a = sig(load(src))
    ca = coords(load(src))
    for t in j["targets"]:
        n += 1
        try:
            gt = load(t)
        except Exception as e:
            print("  !! %s 읽기 실패 %s" % (t, e)); bad += 1; continue
        b = sig(gt)
        names = ["역할", "문쌍", "외부문", "엣지", "창"]
        diff = [names[i] for i in range(5) if a[i] != b[i]]
        meta = gt.get("meta") or {}
        if not meta.get("propagated"):
            diff.append("meta.propagated 없음")
        if meta.get("propagated_from") != src:
            diff.append("propagated_from=%s" % meta.get("propagated_from"))
        cb = coords(gt)
        if len(ca) == len(cb) and ca:
            dx = {round(y[1] - x[1], 1) for x, y in zip(ca, cb)}
            dy = {round(y[2] - x[2], 1) for x, y in zip(ca, cb)}
            ids = [x[0] for x in ca] == [y[0] for y in cb]
            if not ids:
                diff.append("문 id 순서 다름")
            elif len(dx) > 1 or len(dy) > 1:
                diff.append("좌표 변환 제각각 dx=%s dy=%s" % (sorted(dx)[:3], sorted(dy)[:3]))
        else:
            diff.append("문 좌표 개수 %d vs %d" % (len(ca), len(cb)))
        if diff:
            bad += 1
            print("  X %-34s  %s" % (t, " · ".join(diff)))
print("\n대조 %d장 · 어긋남 %d장" % (n, bad))
