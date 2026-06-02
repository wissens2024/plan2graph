"""Task 1-2 (1/2) — COCO 라벨 JSON 파서 (단일 파일).

실데이터 사실:
- UTF-8. 키: categories / images(len=1) / annotations.
- category_id → name 은 파일별 정의이므로 각 JSON의 categories에서 읽는다.
- 세부유형은 annotation.attributes 안에 있다(키: 구조_출입문/구조_창호/창호/구조_벽체).
- SPA/STR annotation은 segmentation 폴리곤 보유, OBJ/OCR은 bbox만(segmentation=[]).
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import config  # noqa: E402


@dataclass
class Annotation:
    ann_id: int
    class_name: str                 # category 이름 (예: '공간_거실', '구조_출입문')
    subtype: str | None             # attributes에서 추출한 세부유형 (예: '여닫이문')
    segmentation: list[list[float]]  # [[x1,y1,...], ...] (없으면 [])
    bbox: list[float]               # [x, y, w, h]
    area: float                     # COCO area(=bbox 면적, 픽셀²)
    attributes: dict = field(default_factory=dict)
    ocr_text: str | None = None     # OCR 라벨의 인식 텍스트


@dataclass
class CocoDoc:
    path: Path
    image_file: str
    width: int
    height: int
    annotations: list[Annotation]

    def by_class(self, *names: str) -> list[Annotation]:
        s = set(names)
        return [a for a in self.annotations if a.class_name in s]


def _extract_subtype(attrs: dict) -> str | None:
    """attributes에서 세부유형 값을 찾는다(여닫이문/미닫이창/철근콘크리트벽 등)."""
    for key in config.SUBTYPE_ATTR_KEYS:
        if key in attrs:
            return attrs[key]
    return None


def _parse_doc(doc: dict, source: Path) -> CocoDoc:
    """파싱된 dict → CocoDoc. load_coco / load_coco_bytes 공용 코어."""
    id2name = {c["id"]: c["name"] for c in doc.get("categories", [])}
    img = (doc.get("images") or [{}])[0]

    anns: list[Annotation] = []
    for a in doc.get("annotations", []):
        attrs = a.get("attributes", {}) or {}
        cls = id2name.get(a.get("category_id"), str(a.get("category_id")))
        seg = a.get("segmentation") or []
        # COCO 폴리곤은 [[x1,y1,...]] (RLE 아님). 방어적으로 리스트 확인.
        if seg and not isinstance(seg[0], list):
            seg = [seg]
        anns.append(Annotation(
            ann_id=a.get("id", -1),
            class_name=cls,
            subtype=_extract_subtype(attrs),
            segmentation=seg,
            bbox=a.get("bbox", []),
            area=float(a.get("area", 0.0)),
            attributes=attrs,
            ocr_text=attrs.get(config.OCR_ATTR_KEY),
        ))

    return CocoDoc(
        path=source,
        image_file=img.get("file_name", source.stem + ".PNG"),
        width=int(img.get("width", 0)),
        height=int(img.get("height", 0)),
        annotations=anns,
    )


def load_coco(path: str | Path) -> CocoDoc:
    """COCO JSON 한 개를 파일에서 파싱한다(UTF-8 고정)."""
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    return _parse_doc(doc, path)


def load_coco_bytes(data: bytes, source: str = "<bytes>") -> CocoDoc:
    """COCO JSON을 메모리 bytes에서 파싱한다(ZIP zf.read 결과용, UTF-8)."""
    doc = json.loads(data.decode("utf-8"))
    return _parse_doc(doc, Path(source))


if __name__ == "__main__":
    # 간단 점검: 인자로 받은 JSON의 클래스 분포·세부유형 출력
    import collections
    for p in sys.argv[1:]:
        d = load_coco(p)
        print(f"\n{Path(p).name}  ({d.width}x{d.height}, ann={len(d.annotations)})")
        cnt = collections.Counter(a.class_name for a in d.annotations)
        for name, n in cnt.most_common():
            subs = collections.Counter(
                a.subtype for a in d.annotations if a.class_name == name and a.subtype)
            extra = f"  세부:{dict(subs)}" if subs else ""
            print(f"  {n:4d}  {name}{extra}")
