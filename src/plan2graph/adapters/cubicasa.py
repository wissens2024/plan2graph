"""CubiCasa5k 어댑터 — SVG 벡터 주석 → 공통 스키마 레코드.

CubiCasa5k(5k): model.svg 에 방(<g class="Space TYPE">…<polygon>), 문/창 아이콘이 벡터로
주석됨. 방 폴리곤→타입·중심·면적, 문 위치→인접 방 연결 → common.to_record.

⚠️ CubiCasa SVG 클래스 명명은 샘플마다 약간 다름(Space/FixedFurniture 등). 실파일로
   클래스·polygon 추출을 점검할 것. 서버(데이터 위치)에서 실행.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from plan2graph.adapters import common  # noqa: E402

_NUM = re.compile(r"-?\d+\.?\d*")


def _points(poly_str: str):
    v = [float(x) for x in _NUM.findall(poly_str or "")]
    return list(zip(v[0::2], v[1::2]))


def _poly_stats(pts):
    if len(pts) < 3:
        return None
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    area = abs(sum(xs[i] * ys[(i + 1) % len(pts)] - xs[(i + 1) % len(pts)] * ys[i]
                   for i in range(len(pts)))) / 2.0
    return ([round(sum(xs) / len(xs), 1), round(sum(ys) / len(ys), 1)], area, pts)


def parse(svg_path: str) -> dict | None:
    """CubiCasa model.svg 1장 → 공통 레코드."""
    import math
    try:
        root = ET.parse(svg_path).getroot()
    except Exception:
        return None
    ns = {"s": "http://www.w3.org/2000/svg"}
    rooms, room_polys, doors = [], [], []
    for g in root.iter():
        cls = g.attrib.get("class", "")
        tag = g.tag.split("}")[-1]
        if "Space" in cls:                 # 방
            label = cls.replace("Space", "").strip().split()[0] if cls.strip() != "Space" else "room"
            poly = None
            for child in g.iter():
                if child.tag.split("}")[-1] in ("polygon", "path") and child.attrib.get("points"):
                    poly = _points(child.attrib["points"]); break
            st = _poly_stats(poly) if poly else None
            if st:
                rooms.append({"type": common.map_type(label), "centroid": st[0],
                              "area_px": st[1]})
                room_polys.append(st[2])
        elif "Door" in cls:                # 문(중심점)
            for child in g.iter():
                if child.attrib.get("points"):
                    p = _points(child.attrib["points"])
                    if p:
                        cx = sum(x for x, _ in p) / len(p); cy = sum(y for _, y in p) / len(p)
                        doors.append((cx, cy)); break
    if not rooms:
        return None
    # 문 → 가장 가까운 두 방 연결(door). 문 없는 인접 방은 open(경계 근접).
    def _near(pt, poly):
        return min(((pt[0] - x) ** 2 + (pt[1] - y) ** 2) ** 0.5 for x, y in poly)
    edges, doored = [], set()
    for dpt in doors:
        d = sorted(range(len(rooms)), key=lambda i: _near(dpt, room_polys[i]))[:2]
        if len(d) == 2:
            edges.append((d[0], d[1], "door")); doored.add(tuple(sorted(d)))
    # 인접(경계 근접) 방쌍 중 문 없는 것 → open
    for a in range(len(rooms)):
        for b in range(a + 1, len(rooms)):
            if tuple(sorted((a, b))) in doored:
                continue
            mind = min(((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5
                       for x1, y1 in room_polys[a] for x2, y2 in room_polys[b])
            if mind < 15:
                edges.append((a, b, "open"))
    gid = "CC_" + Path(svg_path).parent.name
    allx = [x for poly in room_polys for x, _ in poly]
    ally = [y for poly in room_polys for _, y in poly]
    w = int(max(allx) + 10) if allx else 256
    h = int(max(ally) + 10) if ally else 256
    return common.to_record(gid, "cubicasa5k", rooms, edges, w, h)


def _self_test() -> bool:
    """합성 SVG(방 3개 + 문 1개) → 파싱 → 레코드."""
    svg = '''<svg xmlns="http://www.w3.org/2000/svg">
      <g class="Space LivingRoom"><polygon points="10,10 100,10 100,100 10,100"/></g>
      <g class="Space Bedroom"><polygon points="100,10 180,10 180,90 100,90"/></g>
      <g class="Space Kitchen"><polygon points="10,100 100,100 100,180 10,180"/></g>
      <g class="Door"><polygon points="98,40 104,40 104,55 98,55"/></g>
    </svg>'''
    tmp = ROOT / "data" / "v2v" / "_cc_test.svg"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(svg, encoding="utf-8")
    rec = parse(str(tmp))
    tmp.unlink(missing_ok=True)
    ok = rec is not None and rec["meta"]["source"] == "cubicasa5k" and \
        rec["constraints"]["program"].get("거실") == 1
    print(f"CubiCasa self-test: program={rec['constraints']['program'] if rec else None} "
          f"edges={[(e['source'],e['target'],e['via']) for e in rec['layout']['edges']] if rec else None} "
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
    ap.add_argument("--src", help="CubiCasa5k 루트(각 샘플 디렉터리에 model.svg)")
    ap.add_argument("--out", default=str(ROOT / "data" / "releases" / "global_cubicasa"))
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    if a.self_test:
        sys.exit(0 if _self_test() else 1)
    import glob
    import json
    out = Path(a.out) / "graphs"; out.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in sorted(glob.glob(str(Path(a.src) / "**" / "model.svg"), recursive=True)):
        rec = parse(p)
        if rec:
            (out / f"{rec['graph_id']}.json").write_text(
                json.dumps(rec, ensure_ascii=False), encoding="utf-8")
            n += 1
        if a.limit and n >= a.limit:
            break
    print(f"CubiCasa 변환 {n}건 → {out}")
