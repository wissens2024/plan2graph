"""Task 1-2 (2/2) — COCO 주석 → shapely 기하 객체화 + 도면 조립.

- segmentation 폴리곤 → shapely.Polygon. 깨진 폴리곤은 buffer(0) 보정, 실패 시 격리.
- OBJ/OCR은 segmentation이 없으므로 bbox로 사각형 폴리곤 생성.
- 한 도면(같은 이미지)의 여러 라벨 CocoDoc(SPA/STR/OBJ/OCR)을 Drawing으로 합친다.
  ※ 라벨 간 연결고리는 9자리 ID가 아니라 PNG 내용 해시(unpack.link 참조)다.
  이 함수는 이미 '같은 도면'으로 묶인 CocoDoc 리스트를 받는다.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from shapely.geometry import Polygon, box
from shapely.validation import make_valid

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph.coco import Annotation, CocoDoc  # noqa: E402


@dataclass
class Element:
    kind: str                  # room / door / window / wall / object / text
    class_name: str
    subtype: str | None
    polygon: Polygon | None    # 보정된 유효 폴리곤(없으면 None=격리 대상)
    bbox: list[float]
    area_px: float             # 폴리곤 실제 면적(픽셀²). 폴리곤 없으면 bbox 면적.
    centroid: tuple[float, float] | None
    ocr_text: str | None = None
    broken: bool = False       # 폴리곤 보정 실패


@dataclass
class Drawing:
    image_path: Path | None
    width: int
    height: int
    scale: float | None        # pixel→m 변환계수. None이면 면적은 픽셀² 단위.
    rooms: list[Element] = field(default_factory=list)
    doors: list[Element] = field(default_factory=list)
    windows: list[Element] = field(default_factory=list)
    walls: list[Element] = field(default_factory=list)
    objects: list[Element] = field(default_factory=list)
    texts: list[Element] = field(default_factory=list)
    broken_count: int = 0

    def area_m2(self, area_px: float) -> float | None:
        """scale(=1픽셀당 m)이 있으면 ㎡로 변환, 없으면 None."""
        if self.scale is None:
            return None
        return area_px * (self.scale ** 2)


# 클래스명 → Drawing의 어느 버킷에 들어갈지
def _kind_of(class_name: str) -> str | None:
    if class_name in config.SPACE_CLASSES:
        return "room"
    if class_name == config.DOOR_CLASS:
        return "door"
    if class_name == config.WINDOW_CLASS:
        return "window"
    if class_name == config.WALL_CLASS:
        return "wall"
    if class_name in config.OBJECT_CLASSES:
        return "object"
    if class_name == config.TEXT_CLASS:
        return "text"
    return None  # background 등 무시


def _polygon_from_seg(seg: list[list[float]]) -> Polygon | None:
    """COCO 폴리곤(가장 큰 외곽 1개) → 유효 shapely Polygon. 실패 시 None."""
    best: Polygon | None = None
    best_area = -1.0
    for ring in seg:
        if len(ring) < 6:  # 점 3개 미만
            continue
        pts = list(zip(ring[0::2], ring[1::2]))
        try:
            poly = Polygon(pts)
        except (ValueError, Exception):
            continue
        if not poly.is_valid:
            poly = _repair(poly)
        if poly is None or poly.is_empty:
            continue
        if poly.area > best_area:
            best, best_area = poly, poly.area
    return best


def _repair(poly: Polygon) -> Polygon | None:
    """self-intersection 등 보정: buffer(0) → make_valid 순으로 시도."""
    for fn in (lambda p: p.buffer(0), make_valid):
        try:
            fixed = fn(poly)
        except Exception:
            continue
        if fixed is None or fixed.is_empty:
            continue
        # make_valid는 GeometryCollection을 줄 수 있다 → 최대 폴리곤만 취함
        if fixed.geom_type == "Polygon" and fixed.is_valid:
            return fixed
        if fixed.geom_type in ("MultiPolygon", "GeometryCollection"):
            polys = [g for g in fixed.geoms if g.geom_type == "Polygon" and g.is_valid]
            if polys:
                return max(polys, key=lambda g: g.area)
    return None


def _element_from_annotation(a: Annotation, kind: str) -> Element:
    poly = _polygon_from_seg(a.segmentation) if a.segmentation else None
    # segmentation이 없거나 보정 실패 → bbox로 사각형 대체(OBJ/OCR 또는 격리 후보)
    used_bbox = False
    if poly is None and a.bbox and len(a.bbox) == 4:
        x, y, w, h = a.bbox
        if w > 0 and h > 0:
            poly = box(x, y, x + w, y + h)
            used_bbox = True
    broken = (a.segmentation and poly is None) and not used_bbox
    if poly is not None and not poly.is_empty:
        area_px = poly.area
        c = poly.centroid
        centroid = (c.x, c.y)
    else:
        area_px = a.area
        centroid = None
    return Element(
        kind=kind, class_name=a.class_name, subtype=a.subtype,
        polygon=poly if (poly is not None and not poly.is_empty) else None,
        bbox=a.bbox, area_px=area_px, centroid=centroid,
        ocr_text=a.ocr_text, broken=bool(broken),
    )


def assemble_drawing(docs: list[CocoDoc], image_path: str | Path | None = None,
                     scale: float | None = config.DEFAULT_SCALE) -> Drawing:
    """같은 도면으로 묶인 CocoDoc들 → Drawing 중간표현."""
    width = max((d.width for d in docs), default=0)
    height = max((d.height for d in docs), default=0)
    dr = Drawing(image_path=Path(image_path) if image_path else None,
                 width=width, height=height, scale=scale)
    bucket = {"room": dr.rooms, "door": dr.doors, "window": dr.windows,
              "wall": dr.walls, "object": dr.objects, "text": dr.texts}
    for doc in docs:
        for a in doc.annotations:
            kind = _kind_of(a.class_name)
            if kind is None:
                continue
            el = _element_from_annotation(a, kind)
            if el.broken:
                dr.broken_count += 1
                continue
            bucket[kind].append(el)
    return dr


if __name__ == "__main__":
    from plan2graph.coco import load_coco
    docs = [load_coco(p) for p in sys.argv[1:]]
    dr = assemble_drawing(docs)
    print(f"Drawing {dr.width}x{dr.height} scale={dr.scale}")
    print(f"  rooms={len(dr.rooms)} doors={len(dr.doors)} windows={len(dr.windows)} "
          f"walls={len(dr.walls)} objects={len(dr.objects)} texts={len(dr.texts)} "
          f"broken={dr.broken_count}")
    for r in dr.rooms[:5]:
        print(f"    room {r.class_name:12} area_px={r.area_px:,.0f} centroid={r.centroid}")
