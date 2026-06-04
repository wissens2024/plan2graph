"""CubiCasa5k 도면 검수 — 정상분(그래프 변환)·제외분(사유)을 사람이 눈으로 확인.

CubiCasa 샘플(<sub>/<id>/{F1_scaled.png, model.svg})을 어댑터로 변환:
  converted : 방 폴리곤 추출 성공 → global_cubicasa 그래프化(정상분)
  excluded  : 방 0개/파싱 실패 → 제외분(사유)
오버레이: model.svg의 방=초록·문=빨강을 F1_scaled.png에 그림(SVG≈PNG 좌표, ~1:1).
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph import sources  # noqa: E402
from plan2graph.adapters import cubicasa as _cc  # noqa: E402
from plan2graph.adapters import common as _common  # noqa: E402

CC_ROOT = config.RAW_DIR / "cubicasa5k"
GRAPHS = sources.graphs_dir("cubicasa5k")   # staging/cubicasa5k/graphs 우선·없으면 레거시


def scan() -> dict:
    """CubiCasa 샘플 → {converted:[rec], excluded:[rec]}. graph_id=CC_<id> 기준 분류."""
    converted = {p.stem[3:] for p in GRAPHS.glob("*.json")} if GRAPHS.exists() else set()
    out = {"converted": [], "excluded": []}
    if not CC_ROOT.is_dir():
        return out
    for svg in sorted(CC_ROOT.glob("**/model.svg")):
        sid = svg.parent.name
        sub = svg.parent.parent.name
        png = svg.parent / "F1_scaled.png"
        rec = {"id": sid, "sub": sub, "svg": str(svg),
               "png": str(png) if png.exists() else None}
        (out["converted"] if sid in converted else out["excluded"]).append(rec)
    return out


def exclude_reason(rec: dict) -> str:
    """제외분 사유(재파싱)."""
    try:
        r = _cc.parse(rec["svg"])
    except Exception as e:  # noqa: BLE001
        return f"파싱 오류: {str(e)[:40]}"
    if r is None:
        return "SVG 파싱 실패(방 polygon 없음)"
    nroom = sum(1 for n in r["layout"]["nodes"] if isinstance(n["id"], int))
    return "방 0개" if nroom == 0 else f"방 {nroom}개(변환됨)"


def _svg_polys(svg_path: str):
    """SVG → [(kind, name, [(x,y)...])]. kind: room/door. (NON_ROOM 제외)"""
    try:
        root = ET.parse(svg_path).getroot()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for g in root.iter():
        cls = g.attrib.get("class", "")
        if "Space" in cls:
            label = (cls.replace("Space", "").strip().split()[0]
                     if cls.strip() != "Space" else "room")
            if label in _cc._NON_ROOM:
                continue
            for ch in g.iter():
                if ch.tag.split("}")[-1] in ("polygon", "path") and ch.attrib.get("points"):
                    pts = _cc._points(ch.attrib["points"])
                    if len(pts) >= 3:
                        out.append(("room", _common.map_type(label), pts))
                        break
        elif "Door" in cls:
            for ch in g.iter():
                if ch.attrib.get("points"):
                    pts = _cc._points(ch.attrib["points"])
                    if pts:
                        out.append(("door", "door", pts))
                        break
    return out


def render(rec: dict, overlay: bool = True):
    """F1_scaled.png + (overlay 시) 방=초록·문=빨강 SVG 폴리곤. PIL 이미지."""
    from PIL import Image, ImageDraw
    if not rec.get("png"):
        raise FileNotFoundError("F1_scaled.png 없음")
    img = Image.open(rec["png"]).convert("RGB")
    if overlay:
        w = max(2, max(img.size) // 400)
        dr = ImageDraw.Draw(img, "RGBA")
        for kind, name, pts in _svg_polys(rec["svg"]):
            c = (0, 170, 0) if kind == "room" else (220, 0, 0)
            if kind == "room":
                dr.polygon(pts, fill=c + (40,))
            dr.line(pts + [pts[0]], fill=c + (255,), width=w)
    return img


if __name__ == "__main__":
    s = scan()
    print(f"CubiCasa: 정상분(변환) {len(s['converted'])} · 제외분 {len(s['excluded'])}")
