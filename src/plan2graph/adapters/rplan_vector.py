"""RPLAN 벡터 어댑터 — Graph2Plan 변환 데이터(.mat/.pkl) → 공통 스키마 레코드 (Tier1, 정확).

렌더(snapshot_train/*.png)를 생성한 **벡터 구조 데이터**(data_{train,test}_converted.pkl)를
직접 파싱한다. 비전ML·색파싱 불요. 구조체(mat_struct) 필드:
  box      (R,5): [x0,y0,x1,y1, roomType]  — roomType=RPLAN 카테고리 0~12
  edge     (E,3): [room_i, room_j, dir]    — dir=공간방향(0~8), 문/개방 구분 아님
  boundary (B,4): [x,y,?,doorFlag]         — doorFlag==1 = 외곽선 정문 세그먼트
  rBoundary(R,) : 방별 폴리곤
  name          : 렌더 파일명(snapshot_train/<name>.png)과 매칭

현관: RPLAN은 현관을 '방'으로 라벨하지 않고 외곽선 문으로만 인코딩 → boundary의 문
   세그먼트에 가장 가까운 방을 **기능적 진입실(is_entrance)** 로 표시해 EXTERIOR 연결.
   (R4 현관규칙을 어댑터 국소로 충족, 규칙엔진 불변.)
※ 방-방 edge는 via='open'(인접/개방). 문/개방 정밀 구분은 후속(boundary·rBoundary 기하).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from plan2graph.adapters import common  # noqa: E402
from plan2graph.adapters.rplan import RPLAN_CATEGORIES  # noqa: E402

ROOM_CAT_MAX = 12   # 0~12만 방(13=External 등은 box에 없지만 방어)


def _entrance_idx(boundary, boxes):
    """외곽선 정문 세그먼트(doorFlag==1) 중심에 가장 가까운 방 인덱스. 없으면 None."""
    import numpy as np
    b = np.asarray(boundary)
    if b.ndim != 2 or b.shape[1] < 4:
        return None
    door = b[b[:, 3] == 1][:, :2]
    if len(door) == 0:
        return None
    dx, dy = float(door[:, 0].mean()), float(door[:, 1].mean())
    best, bd = None, 1e18
    for i, r in enumerate(boxes):
        x0, y0, x1, y1 = (float(r[0]), float(r[1]), float(r[2]), float(r[3]))
        qx = max(x0 - dx, 0.0, dx - x1)      # 점-사각형 거리
        qy = max(y0 - dy, 0.0, dy - y1)
        d = (qx * qx + qy * qy) ** 0.5
        if d < bd:
            bd, best = d, i
    return best


def _extract(s):
    """포맷별 (types, boxes(N,4), edge) 추출.
    Interface pkl : box=[x0,y0,x1,y1,type] + edge
    Network data.mat : rType + gtBoxNew(또는 gtBox) + rEdge  ← 전체 80,788."""
    import numpy as np
    if getattr(s, "box", None) is not None:               # Interface(부분집합 74,995)
        box = np.asarray(s.box)
        if box.ndim != 2 or box.shape[0] == 0:
            return None
        return box[:, 4], box[:, :4], getattr(s, "edge", None)
    if getattr(s, "rType", None) is not None:             # Network(전체 80,788)
        boxes = getattr(s, "gtBoxNew", None)
        if boxes is None:
            boxes = getattr(s, "gtBox", None)
        if boxes is None:
            return None
        boxes = np.asarray(boxes)
        if boxes.ndim != 2 or boxes.shape[0] == 0:
            return None
        return np.asarray(s.rType).ravel(), boxes[:, :4], getattr(s, "rEdge", None)
    return None


def parse_struct(s) -> dict | None:
    """mat_struct 1개(한 플랜) → 공통 레코드. 두 포맷(Interface/Network) 모두 처리."""
    import numpy as np
    gid = "RPLAN_" + str(getattr(s, "name", "unknown"))
    ext = _extract(s)
    if ext is None:
        return common.empty_record(gid, "rplan", "empty_layout")
    types, boxes, edge = ext
    n = min(len(types), len(boxes))
    rooms = []
    for i in range(n):
        t = int(types[i])
        if t > ROOM_CAT_MAX:
            continue
        x0, y0, x1, y1 = (int(boxes[i][0]), int(boxes[i][1]),
                          int(boxes[i][2]), int(boxes[i][3]))
        rooms.append({"type": common.map_type(RPLAN_CATEGORIES.get(t, "room")),
                      "centroid": [round((x0 + x1) / 2, 1), round((y0 + y1) / 2, 1)],
                      "area_px": float(abs((x1 - x0) * (y1 - y0))),
                      "is_entrance": False})
    if not rooms:
        return common.empty_record(gid, "rplan", "empty_layout")
    # 외곽선 문 → 진입실 표시(현관 규칙 충족)
    ei = _entrance_idx(getattr(s, "boundary", []), boxes)
    if ei is not None and 0 <= ei < len(rooms):
        rooms[ei]["is_entrance"] = True
    # 방-방 인접(via open). edge 3번째 열은 방향코드라 무시.
    edges = []
    if edge is not None:
        for e in np.atleast_2d(np.asarray(edge)):
            if len(e) < 2:
                continue
            u, v = int(e[0]), int(e[1])
            if u != v and 0 <= u < len(rooms) and 0 <= v < len(rooms):
                edges.append((u, v, "open"))
    w = int(np.asarray(boxes)[:, :4].max()) + 1
    return common.to_record(gid, "rplan", rooms, edges, w, w)


def iter_structs(path: str):
    """RPLAN 구조 데이터 → (name, struct) 제너레이터.
    .mat(Network/data.mat=전체 80,788) → scipy.io.loadmat,
    .pkl(Interface 부분집합) → pickle. 이름은 struct.name 우선, 없으면 nameList류."""
    if str(path).endswith(".mat"):
        import scipy.io as sio
        d = sio.loadmat(path, struct_as_record=False, squeeze_me=True)
    else:
        import pickle
        with open(path, "rb") as f:
            d = pickle.load(f)
    data = d["data"]
    names = None
    for k in ("nameList", "testNameList", "trainNameList"):
        v = d.get(k) if hasattr(d, "get") else None
        if v is not None and len(v) == len(data):
            names = v
            break
    for i in range(len(data)):
        nm = str(names[i]) if names is not None else str(getattr(data[i], "name", i))
        yield nm, data[i]


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import argparse
    import json
    from plan2graph import sources
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="data_*_converted.pkl (train/test) 1개 이상",
                    nargs="+")
    ap.add_argument("--out", default=None, help="기본 staging/rplan/graphs")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    out = Path(a.out) if a.out else (sources.staging_root("rplan") / "graphs")
    out.mkdir(parents=True, exist_ok=True)
    n, fail = 0, 0
    for pkl in a.src:
        for name, s in iter_structs(pkl):
            try:
                rec = parse_struct(s)
            except Exception:  # noqa: BLE001
                rec = None
            if rec is None:
                fail += 1
                continue
            (out / f"{rec['graph_id']}.json").write_text(
                json.dumps(rec, ensure_ascii=False), encoding="utf-8")
            n += 1
            if a.limit and n >= a.limit:
                break
        if a.limit and n >= a.limit:
            break
    print(f"RPLAN 벡터 변환 {n}건 (실패 {fail}) → {out}")
