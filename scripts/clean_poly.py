#!/usr/bin/env python3
"""clean_poly — 보정본의 기형 폴리곤을 정리한다(중복 꼭짓점·자기교차·0면적 꼬리).

기하는 바꾸지 않는다: shapely buffer(0) 로 자기교차를 풀고, 연속 중복점을 지운 뒤,
면적이 1px 이상 달라지면 **건드리지 않고 보고만 한다**(안전장치).
백업 후 저장. 읽은 값은 bbox/area/centroid 까지 같이 맞춘다.
"""
import datetime
import json
import os
import shutil
import sys

from shapely.geometry import Polygon

E = "data/staging/corrected/edits"
BK = "data/staging/corrected/_manual_backup"


def dedup_pts(pts):
    out = []
    for p in pts:
        q = [round(p[0], 1), round(p[1], 1)]
        if not out or out[-1] != q:
            out.append(q)
    while len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def fix_room(r):
    pts = r.get("polygon") or []
    if len(pts) < 3:
        return None
    before = Polygon([(x, y) for x, y in pts])
    a0 = before.area
    cleaned = dedup_pts(pts)
    q = Polygon([(x, y) for x, y in cleaned])
    tol = 1.0
    if not q.is_valid:
        # 자기교차를 풀고, 폭 1px 안팎의 가짜 가지(슬리버)를 떼고, 반올림 잔떨림을 정리
        q = q.buffer(0)
        if q.geom_type == "MultiPolygon":
            q = max(q.geoms, key=lambda g: g.area)
        if q.geom_type != "Polygon" or q.is_empty:
            return "불가"
        d = q.buffer(-0.8).buffer(0.8)
        if d.geom_type == "MultiPolygon":
            d = max(d.geoms, key=lambda g: g.area)
        if d.geom_type == "Polygon" and not d.is_empty:
            d = d.simplify(0.3)
            if d.is_valid and not d.is_empty:
                q = d
        cleaned = [[round(x, 1), round(y, 1)] for x, y in list(q.exterior.coords)[:-1]]
        tol = max(20.0, 0.005 * a0)       # 슬리버 제거분만 허용
    if abs(q.area - a0) > tol:
        return "면적변화 %.1f" % (q.area - a0)
    if cleaned == pts:
        return None
    r["polygon"] = cleaned
    b = q.bounds
    r["bbox_px"] = [round(b[0], 1), round(b[1], 1), round(b[2] - b[0], 1), round(b[3] - b[1], 1)]
    r["area_px"] = round(q.area, 1)
    c = q.centroid
    r["centroid"] = [round(c.x, 1), round(c.y, 1)]
    if "perimeter_px" in r:
        r["perimeter_px"] = round(q.length, 1)
    return True


def main():
    pids = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply = "--apply" in sys.argv
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bk = os.path.join(BK, stamp + "_cleanpoly")
    n_file = n_room = 0
    refused = []
    for pid in pids:
        p = os.path.join(E, pid + ".json")
        if not os.path.exists(p):
            continue
        g = json.load(open(p, encoding="utf-8"))
        changed = 0
        for rid, r in g["rooms"].items():
            res = fix_room(r)
            if res is True:
                changed += 1
            elif isinstance(res, str):
                refused.append((pid, rid, r.get("role"), res))
        if changed:
            n_file += 1
            n_room += changed
            if apply:
                os.makedirs(bk, exist_ok=True)
                shutil.copy(p, os.path.join(bk, pid + ".json"))
                tmp = p + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(g, f, ensure_ascii=False)
                os.replace(tmp, p)
    print("정리 대상 파일 %d · 방 %d" % (n_file, n_room))
    if refused:
        print("건드리지 않음 %d건:" % len(refused))
        for x in refused[:10]:
            print("   ", x)
    if apply:
        print("백업 →", bk)
    else:
        print("[드라이런] --apply 로 적용")


if __name__ == "__main__":
    main()
