#!/usr/bin/env python3
"""del_room — 방을 지운다. 세대 밖 공용부가 방으로 들어온 경우에 쓴다.

사용자(2026-11-14): "외부에 기타 역할 삭제" — 계단·엘리베이터 영역이 `기타` 방으로
들어와 있었다. 앞서 c6f3a6f8 u1 에서도 홀·ST 를 같은 이유로 지웠다.

지우는 것: rooms 의 그 항목, 그 방을 가리키는 문·창 레코드, 그 방이 든 엣지.
남는 것은 `rebuild_links` 가 다시 푼다.

지운 방은 `meta.deleted_rooms` 에 역할·bbox 와 함께 남긴다 — 나중에 "왜 방 수가
줄었나"를 물을 수 있어야 한다.

읽기 전용(기본). --apply 를 줘야 쓴다.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil

ROOT = "/home/ju/plan2graph"
EDITS = os.path.join(ROOT, "data", "staging", "corrected", "edits")
BK = os.path.join(ROOT, "data", "staging", "corrected", "_manual_backup")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("rooms", nargs="+", help="지울 방 id")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    p = os.path.join(EDITS, a.plan + ".json")
    g = json.load(open(p, encoding="utf-8"))
    R = {str(k): v for k, v in (g.get("rooms") or {}).items()}
    kill = [r for r in a.rooms if r in R]
    miss = [r for r in a.rooms if r not in R]
    if miss:
        print("없는 방: %s" % miss)
    if not kill:
        return

    def nm(r):
        return "%s(%s)" % ((R.get(str(r)) or {}).get("role"), r)

    for r in kill:
        print("지울 방 %-12s bbox %s" % (nm(r), [round(v) for v in (R[r].get("bbox_px") or [])]))
    ks = set(kill)
    d_out = [d for d in (g.get("doors") or [])
             if ks & {str(x) for x in (d.get("connects") or [])}]
    w_out = [w for w in (g.get("windows") or []) if str(w.get("belongs_to")) in ks]
    e_out = [e for e in (g.get("edges") or [])
             if str(e["from"]) in ks or str(e["to"]) in ks]
    print("   같이 지울 문 %d · 창 %d · 엣지 %d" % (len(d_out), len(w_out), len(e_out)))
    for d in d_out:
        print("      문 %-8s %s" % (d.get("id"), " + ".join(nm(x) for x in (d.get("connects") or []))))
    for e in e_out:
        print("      엣지 %s - %s (%s)" % (nm(e["from"]), nm(e["to"]), e.get("via")))

    if not a.apply:
        print("[드라이런] --apply 로 적용")
        return

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bk = os.path.join(BK, stamp + "_delroom")
    os.makedirs(bk, exist_ok=True)
    shutil.copy(p, os.path.join(bk, a.plan + ".json"))

    meta = g.setdefault("meta", {})
    gone = meta.setdefault("deleted_rooms", [])
    for r in kill:
        gone.append({"id": r, "role": R[r].get("role"),
                     "bbox_px": R[r].get("bbox_px"), "at": stamp,
                     "why": "세대 밖 공용부 — 사용자 지시"})
    for r in kill:
        g["rooms"].pop(r, None)
        g["rooms"].pop(int(r) if r.isdigit() else r, None)
    g["doors"] = [d for d in (g.get("doors") or []) if d not in d_out]
    g["windows"] = [w for w in (g.get("windows") or []) if w not in w_out]
    g["edges"] = [e for e in (g.get("edges") or []) if e not in e_out]
    g["n_rooms"] = len(g["rooms"])
    g["n_doors"] = len(g["doors"])
    g["n_windows"] = len(g["windows"])
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(g, f, ensure_ascii=False)
    os.replace(tmp, p)
    print("적용 · 방 %d 남음 · 백업 -> %s" % (len(g["rooms"]), bk))
    print("다음: rebuild_links 로 다시 풀어라.")


if __name__ == "__main__":
    main()
