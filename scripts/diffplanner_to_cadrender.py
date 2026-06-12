"""diffplanner_to_cadrender — 한국형 엔진 출력(샘플) → 도면(이미지) + DXF.

ADR-0006/0007 비교 파이프라인의 마지막 단. 엔진(DiffPlanner 3-stage)의 최종 출력은
  방 = {category(0~12), location, box[x0,y0,x1,y1]}  (canvas-256 정수)
  + boundary(외곽) + entrance_expand(현관) + adjacencies
까지다 — **문·창·치수·기구·척도는 없다**(SOTA 공통 한계, [[sovereign-engine-decision]]).
그 공백을 neuro-symbolic으로 채워(ADR-0006) 우리 통일그래프(g-0.3)로 만든 뒤
cadrender로 **이미지 + DXF 두 산출물**([[render-two-outputs]])을 낸다:

  엔진 박스+역할  →  geomgraph dict  →  cadrender.from_geomgraph → autocorrect
     │  + 문(인접에서 유도)                                  → render_fig(PNG)
     │  + 창(외곽벽에)                                       → render_dxf(DXF)
     │  + 척도(가정폭) + 기구(역할 카탈로그, semantic_fill 재사용)

T·G·ARM 무관 공용(엔진 출력 포맷만 같으면 됨). 학습이 끝나면 샘플 JSON을 받아
바로 그림이 나오게 하는 게 목적.

실행:
  PYTHONPATH=src python scripts/diffplanner_to_cadrender.py \
      --engine-json /tmp/korean_engine_out.json --n 6 --out /tmp/p2g_render
  (--engine-json = partitioning stage 최종 출력. 단일 record dict 또는 record list.)
"""
from __future__ import annotations

import argparse
import json
import math
import os

from plan2graph import cadrender

# 카테고리 id → 한국 역할 (korean_to_engine.ROLE2CAT의 역; 현관은 entrance_expand로 별도)
CAT_NAME = {
    0: "거실", 1: "안방", 2: "침실", 3: "주방", 4: "화장실", 5: "욕실",
    6: "발코니", 7: "드레스룸", 8: "전실", 9: "복도", 10: "실외기실",
    11: "다목적공간", 12: "기타",
}
ENTRANCE_ROLE = "현관"
ASSUMED_PLAN_WIDTH_MM = 12000.0   # 외곽 가로 12m 가정(절대척도는 사람 길이입력이 정답, ADR-0006)
TOUCH_TOL = 4.0                    # 두 방 박스가 '맞닿음'으로 볼 픽셀 허용오차
WINDOW_ROLES = ("거실", "안방", "침실", "주방", "다목적공간")


def _box_poly(b):
    x0, y0, x1, y1 = b
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _shared_segment(a, b, tol=TOUCH_TOL):
    """두 축정렬 박스 a,b의 맞닿은 경계 선분 → ((x1,y1),(x2,y2)) 또는 None.
    인접(문 위치)·내벽 추출용. a,b = [x0,y0,x1,y1]."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    # 세로 경계(좌우로 맞닿음): a오른쪽≈b왼쪽 또는 그 반대
    for xa, xb in ((ax1, bx0), (bx1, ax0)):
        if abs(xa - xb) <= tol:
            lo, hi = max(ay0, by0), min(ay1, by1)
            if hi - lo > tol:
                x = (xa + xb) / 2.0
                return ((x, lo), (x, hi))
    # 가로 경계(상하로 맞닿음)
    for ya, yb in ((ay1, by0), (by1, ay0)):
        if abs(ya - yb) <= tol:
            lo, hi = max(ax0, bx0), min(ax1, bx1)
            if hi - lo > tol:
                y = (ya + yb) / 2.0
                return ((lo, y), (hi, y))
    return None


def _seg_mid_len(seg):
    (x1, y1), (x2, y2) = seg
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0), math.hypot(x2 - x1, y2 - y1)


def engine_record_to_geomgraph(rec: dict) -> dict:
    """엔진 출력 record → g-0.3 geomgraph dict (문·창·척도 neuro-symbolic 채움)."""
    name = rec.get("name", "engine")
    rooms_in = rec.get("rooms", [])

    # ── 방 노드: 박스 → 폴리곤·역할. id는 엔진 i 그대로(현관은 음수 id로 추가) ──
    boxes = {}        # id -> [x0,y0,x1,y1]
    rooms = {}
    for r in rooms_in:
        rid = int(r["id"])
        x0, y0, x1, y1 = r["box"]
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        if x1 - x0 < 1 or y1 - y0 < 1:
            continue
        boxes[rid] = [x0, y0, x1, y1]
        rooms[str(rid)] = {
            "role": CAT_NAME.get(int(r.get("category", 12)), "기타"),
            "polygon": _box_poly([x0, y0, x1, y1]),
            "centroid": [(x0 + x1) / 2.0, (y0 + y1) / 2.0],
            "area_px": (x1 - x0) * (y1 - y0),
            "fixtures": [],
            "door_ids": [],
        }

    # 현관(entrance_expand 4점 박스)
    ent = rec.get("entrance_expand")
    ENT_ID = -1
    if ent:
        exs = [p[0] for p in ent]
        eys = [p[1] for p in ent]
        eb = [min(exs), min(eys), max(exs), max(eys)]
        if eb[2] - eb[0] > 1 and eb[3] - eb[1] > 1:
            boxes[ENT_ID] = eb
            rooms[str(ENT_ID)] = {
                "role": ENTRANCE_ROLE, "polygon": _box_poly(eb),
                "centroid": [(eb[0] + eb[2]) / 2.0, (eb[1] + eb[3]) / 2.0],
                "area_px": (eb[2] - eb[0]) * (eb[3] - eb[1]),
                "fixtures": [], "door_ids": [],
            }

    # ── 문: 인접쌍의 맞닿은 경계 중점에 배치(neuro-symbolic) ──
    doors = []
    adj = rec.get("adjacencies", [])
    # 현관도 가장 가까운 방과 연결(현관문)
    if ENT_ID in boxes and rooms_in:
        ec = rooms[str(ENT_ID)]["centroid"]
        nearest = min(boxes, key=lambda k: 1e18 if k == ENT_ID else
                      (rooms[str(k)]["centroid"][0] - ec[0]) ** 2
                      + (rooms[str(k)]["centroid"][1] - ec[1]) ** 2)
        adj = list(adj) + [[ENT_ID, nearest]]
    did = 0
    for pair in adj:
        i, j = int(pair[0]), int(pair[1])
        if i not in boxes or j not in boxes:
            continue
        seg = _shared_segment(boxes[i], boxes[j])
        if seg is None:
            continue
        mid, L = _seg_mid_len(seg)
        wpx = min(max(L * 0.5, 16.0), 40.0)         # 문폭 ~표준 실내문 범위
        is_ent = (i == ENT_ID or j == ENT_ID)
        doors.append({"id": did, "position": list(mid), "width_px": wpx,
                      "is_entrance": is_ent, "connects": [i, j]})
        rooms[str(i)]["door_ids"].append(did)
        rooms[str(j)]["door_ids"].append(did)
        did += 1

    # ── 벽: 내벽(인접 경계) + 외곽(boundary) ──
    walls = []
    seen_seg = set()
    for pair in adj:
        i, j = int(pair[0]), int(pair[1])
        if i in boxes and j in boxes:
            seg = _shared_segment(boxes[i], boxes[j])
            if seg:
                walls.append({"segment": [list(seg[0]), list(seg[1])], "type": "interior"})
    # 외곽 폴리곤(boundary: [[x,y,ori,isnew],...]) → 외벽
    bnd = rec.get("boundary") or rec.get("boundary_expand")
    bpts = [(p[0], p[1]) for p in bnd] if bnd else []
    for k in range(len(bpts)):
        a, b = bpts[k], bpts[(k + 1) % len(bpts)]
        walls.append({"segment": [list(a), list(b)], "type": "exterior"})

    # ── 창: 거주방의 외곽 접한 가장 긴 변에 1개(neuro-symbolic) ──
    windows = []
    bxs = [p[0] for p in bpts] or [0, 256]
    bys = [p[1] for p in bpts] or [0, 256]
    bminx, bmaxx, bminy, bmaxy = min(bxs), max(bxs), min(bys), max(bys)

    def on_outer(seg, tol=6.0):
        (x1, y1), (x2, y2) = seg
        mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        return (abs(mx - bminx) < tol or abs(mx - bmaxx) < tol
                or abs(my - bminy) < tol or abs(my - bmaxy) < tol)

    for rid, b in boxes.items():
        if rid == ENT_ID or rooms[str(rid)]["role"] not in WINDOW_ROLES:
            continue
        edges = [((b[0], b[1]), (b[2], b[1])), ((b[2], b[1]), (b[2], b[3])),
                 ((b[2], b[3]), (b[0], b[3])), ((b[0], b[3]), (b[0], b[1]))]
        outer = [e for e in edges if on_outer(e)]
        if not outer:
            continue
        best = max(outer, key=lambda e: _seg_mid_len(e)[1])
        mid, L = _seg_mid_len(best)
        windows.append({"position": list(mid), "width_px": min(max(L * 0.6, 20.0), 80.0),
                        "orientation_deg": 0.0 if abs(best[0][1] - best[1][1]) < 1 else 90.0})

    allx = [c for b in boxes.values() for c in (b[0], b[2])] + bxs
    ally = [c for b in boxes.values() for c in (b[1], b[3])] + bys
    bbox = [min(allx), min(ally), max(allx) - min(allx), max(ally) - min(ally)]
    plan_w_px = max(bbox[2], 1.0)

    return {
        "plan_id": name, "house": "APT", "rooms": rooms,
        "walls": walls, "doors": doors, "windows": windows, "bbox_px": bbox,
        "scale_mm_per_px": ASSUMED_PLAN_WIDTH_MM / plan_w_px,
    }


def render_record(rec: dict, out_prefix: str) -> dict:
    """엔진 record → PNG + DXF. 반환=요약 dict."""
    import semantic_fill as sf  # 기구 배치 규칙 재사용

    g = engine_record_to_geomgraph(rec)
    scale = g["scale_mm_per_px"]
    geom = cadrender.from_geomgraph(g)
    geom.scale_mm_per_px = scale

    # 면적(가정 척도) + 역할 카탈로그 기구 주입(semantic_fill 규칙 재사용)
    cadrender.FIX_KO.update(sf.FIX_LABEL)
    rooms_by_id = {r.id: r for r in geom.rooms}
    for rid_s, r in g["rooms"].items():
        rg = rooms_by_id.get(int(rid_s))
        if rg is None:
            continue
        rg.area_m2 = sf.room_area_m2(r["area_px"], scale)
        rg.fixtures = sf.place_fixtures_for_room(r, g, scale)

    geom = cadrender.autocorrect(geom)

    fig = cadrender.render_fig(geom)
    png = f"{out_prefix}.png"
    fig.savefig(png, dpi=160, bbox_inches="tight")
    import matplotlib.pyplot as plt
    plt.close(fig)

    dxf_ok = False
    try:
        dxf_bytes = cadrender.render_dxf(geom)
        with open(f"{out_prefix}.dxf", "wb") as fh:
            fh.write(dxf_bytes)
        dxf_ok = True
    except Exception as e:
        print(f"  [DXF] 실패: {e}")

    nfix = sum(len(r.fixtures) for r in geom.rooms)
    return {"plan_id": geom.plan_id, "rooms": len(geom.rooms), "doors": len(geom.doors),
            "windows": len(geom.windows), "fixtures": nfix,
            "issues": len(geom.issues), "png": png, "dxf": dxf_ok}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine-json", required=True,
                    help="엔진 최종 출력(record dict 또는 record list) JSON")
    ap.add_argument("--n", type=int, default=6, help="렌더할 샘플 수")
    ap.add_argument("--out", default="/tmp/p2g_render", help="출력 prefix")
    args = ap.parse_args()

    data = json.load(open(args.engine_json, encoding="utf-8"))
    recs = data if isinstance(data, list) else [data]
    recs = recs[:args.n]
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    print(f"렌더 {len(recs)}개 → {args.out}_*")
    for k, rec in enumerate(recs):
        try:
            s = render_record(rec, f"{args.out}_{k}")
            print(f"  [{k}] {s['plan_id']}: 방{s['rooms']} 문{s['doors']} 창{s['windows']} "
                  f"기구{s['fixtures']} 잔여이슈{s['issues']} PNG✓ DXF{'✓' if s['dxf'] else '✗'}")
        except Exception as e:
            import traceback
            print(f"  [{k}] 실패: {e}")
            traceback.print_exc()


if __name__ == "__main__":
    main()
