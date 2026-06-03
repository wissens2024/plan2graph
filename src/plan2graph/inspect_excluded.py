"""배제 도면 검수 — 그래프 대상에서 빠진 평면도를 '눈으로' 확인하는 데이터 레이어.

목적: 정부과제 데이터셋의 *모든 배제에 시각적 증거*. "왜 이 도면이 제외/V2V대상인가"를
      실제 PNG + 라벨 오버레이로 검증(데이터 작업의 핵심 신뢰성).

고유 평면도(FP)를 내용지문(CRC32+크기)으로 묶어 라벨조합으로 분류:
  dual     : SPA(방)+STR(문·벽) 둘 다  → v0 그래프화 대상(참고)
  spa_only : 방 라벨만, 문·벽 없음      → V2V로 STR 예측해 복구
  str_only : 구조 라벨만, 방 없음        → V2V로 SPA 예측해 복구
라벨 오버레이: 방=초록·문=빨강·창=주황·벽=파랑 → "무엇이 라벨됐고 무엇이 빠졌나"가 한눈에.

주의: OBJ/OCR-만 있는 도면은 SPA/STR 원천 zip에 없어 여기 안 보임(별도 zip 필요).
"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph.unpack import discover_zips, iter_zipinfos, parse_name  # noqa: E402

CATEGORIES = {
    "spa_only": "방 라벨만(문·벽 없음) → V2V로 STR 예측 대상",
    "str_only": "구조 라벨만(방 없음) → V2V로 SPA 예측 대상",
    "dual": "SPA+STR 둘 다(v0 그래프화 대상, 참고)",
}


def build_index(split: str = "Training") -> dict:
    """원천 SPA/STR zip 스캔 → fingerprint별 {label: {zip, entry, key, house}} (FP만)."""
    zips = [z for z in discover_zips()
            if z["content"] == "원천" and z["split"] == split]
    groups: dict = defaultdict(dict)
    for z, info in iter_zipinfos(zips):
        meta = parse_name(Path(info.filename).stem)
        if meta is None or meta["drawing"] != config.TARGET_DRAWING_TYPE:
            continue
        sig = f"{info.CRC:08x}_{info.file_size}"
        groups[sig][meta["label"]] = {"zip": str(z["path"]), "entry": info.filename,
                                      "key": meta["key"], "house": meta["house"]}
    return dict(groups)


def label_index(split: str = "Training") -> dict:
    """라벨 zip(TL/VL_SPA·STR) → {label: {key: (zip, entry)}} (FP만). 오버레이용."""
    zips = [z for z in discover_zips()
            if z["content"] == "라벨" and z["split"] == split]
    idx: dict = {"SPA": {}, "STR": {}}
    for z, info in iter_zipinfos(zips):
        meta = parse_name(Path(info.filename).stem)
        if meta and meta["label"] in idx and meta["drawing"] == config.TARGET_DRAWING_TYPE:
            idx[meta["label"]][meta["key"]] = (str(z["path"]), info.filename)
    return idx


def categorize(groups: dict) -> dict:
    """fingerprint 그룹 → {category: [record,...]}. record엔 대표 원천 PNG + 라벨별 키(det)."""
    out: dict = {"dual": [], "spa_only": [], "str_only": []}
    for sig, d in groups.items():
        has_spa, has_str = "SPA" in d, "STR" in d
        rep = d.get("SPA") or d.get("STR")
        rec = {"sig": sig, "labels": sorted(d.keys()), "house": rep["house"],
               "key": rep["key"], "zip": rep["zip"], "entry": rep["entry"], "det": d}
        if has_spa and has_str:
            out["dual"].append(rec)
        elif has_spa:
            out["spa_only"].append(rec)
        elif has_str:
            out["str_only"].append(rec)
    for k in out:
        out[k].sort(key=lambda r: (r["house"], r["key"]))
    return out


def get_png(rec: dict) -> bytes:
    """record의 원천 PNG 1장을 zip에서 추출(표시용)."""
    with zipfile.ZipFile(rec["zip"]) as zf:
        return zf.read(rec["entry"])


def load_polys(zip_path: str, entry: str):
    """라벨 JSON → [(class_name, [(x,y),...]), ...] (픽셀 폴리곤)."""
    with zipfile.ZipFile(zip_path) as zf:
        coco = json.loads(zf.read(entry))
    id2name = {c["id"]: c["name"] for c in coco.get("categories", [])}
    polys = []
    for a in coco.get("annotations", []):
        name = id2name.get(a.get("category_id"), "?")
        for seg in (a.get("segmentation") or []):
            if isinstance(seg, list) and len(seg) >= 6:
                polys.append((name, list(zip(seg[0::2], seg[1::2]))))
    return polys


def _color(name: str):
    if name.startswith("공간_"):
        return (0, 170, 0)        # 방 = 초록
    if "출입문" in name:
        return (220, 0, 0)        # 문 = 빨강
    if "창" in name:
        return (255, 140, 0)      # 창 = 주황
    if "벽" in name:
        return (0, 90, 255)       # 벽 = 파랑
    if name.startswith("객체_"):
        return (160, 0, 200)      # 객체 = 보라
    return (120, 120, 120)


def render(rec: dict, lblidx: dict, overlay: bool = True):
    """원천 PNG + (overlay 시) 라벨 폴리곤을 그린 PIL 이미지. 라벨 빠진 종류는 안 그려짐(=증거)."""
    from PIL import Image, ImageDraw
    img = Image.open(io.BytesIO(get_png(rec))).convert("RGB")
    if overlay:
        w = max(6, max(img.size) // 250)   # 썸네일 후에도 보이게 두꺼운 선
        dr = ImageDraw.Draw(img, "RGBA")
        for lt in ("SPA", "STR"):
            info = rec["det"].get(lt)
            if not info:
                continue
            loc = lblidx.get(lt, {}).get(info["key"])
            if not loc:
                continue
            for name, pts in load_polys(*loc):
                if len(pts) < 2:
                    continue
                c = _color(name)
                dr.polygon(pts, fill=c + (45,))
                dr.line(pts + [pts[0]], fill=c + (255,), width=w)
    return img


def summary(split: str = "Training") -> dict:
    """카테고리별 개수(거주형태별 포함) — 검수 전 분포 확인용."""
    from collections import Counter
    cats = categorize(build_index(split))
    return {k: {"total": len(v), "by_house": dict(Counter(r["house"] for r in v))}
            for k, v in cats.items()}


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sp = sys.argv[1] if len(sys.argv) > 1 else "Training"
    print(f"=== 배제 도면 분류 ({sp}) ===")
    print(json.dumps(summary(sp), ensure_ascii=False, indent=2))
