"""RPLAN 어댑터 — RPLAN raster(다채널) → 공통 스키마 레코드.

RPLAN(~80k): 256×256 4채널 PNG. 채널에 방 카테고리·인스턴스가 인코딩됨.
방 인스턴스별 마스크→폴리곤, 카테고리→우리 클래스, 인접/문→엣지 → common.to_record.

채널 레이아웃(공식 RPLAN-Toolbox 기준, zzilch/RPLAN-Toolbox):
   0=boundary, 1=category, 2=instance, 3=inside  → CAT_CH=1, INST_CH=2.
카테고리 코드 0~12=방, 13=External, 14=ExteriorWall, 15=FrontDoor,
   16=InteriorWall, 17=InteriorDoor (15·17=문).
GPU 불요(이미지 처리). 서버(데이터 위치)에서 실행.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from plan2graph.adapters import common  # noqa: E402

# RPLAN 카테고리 index → 영문 라벨(common.map_type로 우리 클래스 매핑)
RPLAN_CATEGORIES = {
    0: "livingroom", 1: "masterroom", 2: "kitchen", 3: "bathroom", 4: "diningroom",
    5: "childroom", 6: "studyroom", 7: "secondroom", 8: "guestroom", 9: "balcony",
    10: "entrance", 11: "storage", 12: "walkin",
    13: "external", 14: "wall", 15: "frontdoor", 16: "wall", 17: "interiordoor",
}
ROOM_CAT_MAX = 12               # 0~12만 '방'. 13(External)·14~17(벽·문)은 노드 아님.
CAT_CH, INST_CH = 1, 2          # boundary,category,instance,inside → cat=1, inst=2
DOOR_CATS = {15, 17}            # FrontDoor/InteriorDoor 카테고리
ADJ_DILATE = 3                  # 인접 판정 팽창(px)


def _rooms_edges(cat, inst, door_mask):
    import numpy as np
    import cv2
    rooms, idmap = [], {}
    for iid in [i for i in np.unique(inst) if i != 0]:
        m = (inst == iid).astype("uint8")
        if m.sum() < 20:
            continue
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue
        c = max(cnts, key=cv2.contourArea)
        catid = int(np.bincount(cat[m == 1].ravel()).argmax())
        if catid > ROOM_CAT_MAX:        # External·벽·문 인스턴스는 방 아님 → 제외
            continue
        M = cv2.moments(c)
        cx = M["m10"] / M["m00"] if M["m00"] else float(c[:, 0, 0].mean())
        cy = M["m01"] / M["m00"] if M["m00"] else float(c[:, 0, 1].mean())
        idmap[int(iid)] = len(rooms)
        rooms.append({"type": common.map_type(RPLAN_CATEGORIES.get(catid, "room")),
                      "centroid": [round(cx, 1), round(cy, 1)],
                      "area_px": float(m.sum()), "_mask": m})
    # 인접: 팽창 마스크 겹침 → 그 경계에 문픽셀 있으면 door, 없으면 open
    edges = []
    import numpy as np
    k = np.ones((ADJ_DILATE * 2 + 1,) * 2, "uint8")
    dil = [cv2.dilate(r["_mask"], k) for r in rooms]
    for a in range(len(rooms)):
        for b in range(a + 1, len(rooms)):
            overlap = (dil[a] & dil[b])
            if overlap.sum() < 4:
                continue
            via = "door" if (door_mask is not None and (overlap & door_mask).sum() > 0) else "open"
            edges.append((a, b, via))
    for r in rooms:
        r.pop("_mask", None)
    return rooms, edges


def parse(image_path: str) -> dict | None:
    """RPLAN 이미지 1장 → 공통 레코드."""
    import numpy as np
    from PIL import Image
    arr = np.array(Image.open(image_path))
    if arr.ndim != 3 or arr.shape[2] <= max(CAT_CH, INST_CH):
        return None
    cat, inst = arr[:, :, CAT_CH], arr[:, :, INST_CH]
    door_mask = np.isin(cat, list(DOOR_CATS)).astype("uint8")
    rooms, edges = _rooms_edges(cat, inst, door_mask)
    if not rooms:
        return None
    gid = "RPLAN_" + Path(image_path).stem
    h, w = arr.shape[:2]
    return common.to_record(gid, "rplan", rooms, edges, w, h)


def _self_test() -> bool:
    """합성 2채널(카테고리·인스턴스) 배열 → 파싱 → 레코드(실파일 불요)."""
    import numpy as np
    cat = np.zeros((64, 64), "uint8"); inst = np.zeros((64, 64), "uint8")
    # 거실(0) 인스턴스1, 침실(1=masterroom) 인스턴스2, 현관(10) 인스턴스3
    cat[10:40, 10:40] = 0; inst[10:40, 10:40] = 1
    cat[10:40, 42:60] = 1; inst[10:40, 42:60] = 2
    cat[42:55, 10:25] = 10; inst[42:55, 10:25] = 3
    # 채널 순서 boundary,category,instance → [0,cat,inst]
    arr = np.stack([np.zeros_like(cat), cat, inst], axis=2)
    from PIL import Image
    tmp = ROOT / "data" / "v2v" / "_rplan_test.png"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(tmp)
    rec = parse(str(tmp))
    tmp.unlink(missing_ok=True)
    ok = rec is not None and rec["meta"]["source"] == "rplan" and \
        len(rec["layout"]["nodes"]) >= 3
    print(f"RPLAN self-test: program={rec['constraints']['program'] if rec else None} "
          f"→ {'OK' if ok else 'FAIL'}")
    return ok


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--src", help="RPLAN 이미지 디렉터리")
    ap.add_argument("--out", default=str(ROOT / "data" / "releases" / "global_rplan"))
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    if a.self_test:
        sys.exit(0 if _self_test() else 1)
    import glob
    import json
    out = Path(a.out) / "graphs"; out.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in sorted(glob.glob(str(Path(a.src) / "**" / "*.png"), recursive=True)):
        rec = parse(p)
        if rec:
            (out / f"{rec['graph_id']}.json").write_text(
                json.dumps(rec, ensure_ascii=False), encoding="utf-8")
            n += 1
        if a.limit and n >= a.limit:
            break
    print(f"RPLAN 변환 {n}건 → {out}")
