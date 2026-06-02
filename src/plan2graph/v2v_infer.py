"""P2 v2 — Vision-to-Vector 추론: 학습된 모델로 '빠진 라벨'을 예측.

전략(똑똑한 타겟팅): 도면이 한 라벨종류만 가지면 그 영역에서 빠진 종류를 예측한다.
- SPA만 보유 → 방 bbox에 크롭 → **STR 모델**로 벽/문/창 예측
- STR만 보유 → 구조 bbox에 크롭 → **SPA 모델**로 방 예측
예측 결과를 **AI-Hub와 동일한 COCO 형식**으로 저장 → 기존 coco/geometry/topology
파이프라인이 그대로 소비(예측 출처 태그). 품질은 무결성·규제 게이트가 거른다.

GPU(ultralytics YOLO)는 서버에서. 좌표 역변환·COCO 출력은 노트북서 단위검증 가능.
CLI: python src/plan2graph/v2v_infer.py run --spa-weights ... --str-weights ...
     python src/plan2graph/v2v_infer.py --self-test   # YOLO 없이 좌표변환 검증
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402

PRED_DIR = config.DATA_DIR / "v2v" / "predicted"
SPACE_CLASSES = list(config.SPACE_CLASSES)
STRUCT_CLASSES = list(config.STRUCTURE_CLASSES)


# ── 좌표 역변환 (export의 크롭+리사이즈를 되돌림) — 단위검증 대상 ──
def reverse_poly(xy, cx0: float, cy0: float, cw: float, nw: float,
                 ch: float, nh: float):
    """리사이즈된 크롭 좌표(xy: [(x,y)..], nw×nh) → 원본 이미지 좌표."""
    sx, sy = cw / nw, ch / nh
    return [(x * sx + cx0, y * sy + cy0) for (x, y) in xy]


def content_bbox(doc, pad: float = 40.0):
    """COCO doc의 폴리곤/ bbox 합집합 → (cx0,cy0,cx1,cy1)."""
    xs, ys = [], []
    for a in doc.annotations:
        if a.segmentation:
            for ring in a.segmentation:
                xs += ring[0::2]; ys += ring[1::2]
        elif a.bbox and len(a.bbox) == 4:
            x, y, w, h = a.bbox
            xs += [x, x + w]; ys += [y, y + h]
    if not xs:
        return None
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def _crop_resize(img, bbox, imgsz: int):
    """(crop_img, cx0, cy0, cw, nw, ch, nh)."""
    ow, oh = img.size
    cx0 = max(0, int(bbox[0])); cy0 = max(0, int(bbox[1]))
    cx1 = min(ow, int(bbox[2])); cy1 = min(oh, int(bbox[3]))
    cw, ch = cx1 - cx0, cy1 - cy0
    s = imgsz / max(cw, ch)
    nw, nh = max(1, int(cw * s)), max(1, int(ch * s))
    return img.crop((cx0, cy0, cx1, cy1)).resize((nw, nh)), cx0, cy0, cw, nw, ch, nh


def predict_missing(model, img, bbox, class_names: list[str], imgsz: int = 1024,
                    conf: float = 0.25) -> list[dict]:
    """크롭 영역에서 model로 예측 → 원본좌표 폴리곤 인스턴스 [{class_name,segmentation,bbox,score}]."""
    crop, cx0, cy0, cw, nw, ch, nh = _crop_resize(img.convert("L"), bbox, imgsz)
    res = model.predict(crop.convert("RGB"), imgsz=imgsz, conf=conf, verbose=False)[0]
    out = []
    if res.masks is None:
        return out
    for poly, cls, score in zip(res.masks.xy, res.boxes.cls.tolist(),
                                res.boxes.conf.tolist()):
        name = class_names[int(cls)]
        orig = reverse_poly([(float(x), float(y)) for x, y in poly],
                            cx0, cy0, cw, nw, ch, nh)
        flat = [v for xy in orig for v in xy]
        xx = [p[0] for p in orig]; yy = [p[1] for p in orig]
        out.append({"class_name": name, "segmentation": [flat],
                    "bbox": [min(xx), min(yy), max(xx) - min(xx), max(yy) - min(yy)],
                    "score": round(float(score), 3)})
    return out


def to_coco(preds: list[dict], width: int, height: int, key: str,
            label_type: str) -> dict:
    """예측 인스턴스 → AI-Hub와 동일한 COCO dict(예측 출처 태그)."""
    classes = SPACE_CLASSES if label_type == "SPA" else STRUCT_CLASSES
    cats = [{"id": i + 1, "name": n} for i, n in enumerate(classes)]
    cid = {n: i + 1 for i, n in enumerate(classes)}
    anns = []
    for j, p in enumerate(preds):
        anns.append({"id": j, "category_id": cid.get(p["class_name"], 0),
                     "segmentation": p["segmentation"], "bbox": p["bbox"],
                     "area": p["bbox"][2] * p["bbox"][3],
                     "attributes": {"_pred": True, "_score": p["score"]}})
    return {"_source": "v2v_pred", "categories": cats,
            "images": [{"file_name": f"FP_{label_type}_{key}.PNG",
                        "width": width, "height": height}],
            "annotations": anns}


def run(spa_weights: str, str_weights: str, split: str = "Training",
        imgsz: int = 1024, conf: float = 0.25, limit: int | None = None) -> dict:
    """단일라벨 FP 도면에 빠진 종류 예측 → predicted/ 에 COCO 저장."""
    import io
    from ultralytics import YOLO
    from PIL import Image
    from plan2graph import review, unpack
    from plan2graph.coco import load_coco_bytes

    models = {"SPA": YOLO(spa_weights), "STR": YOLO(str_weights)}
    cls_of = {"SPA": SPACE_CLASSES, "STR": STRUCT_CLASSES}
    idx = review.build_indices((split,))
    fmap = unpack.fingerprint_label_map(split=split)
    PRED_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    stat = {"SPA->STR": 0, "STR->SPA": 0}
    for fp, labels in fmap.items():
        has_spa, has_str = "SPA" in labels, "STR" in labels
        if has_spa == has_str:        # 둘 다 or 둘 다 없음 → 이번 버전 스킵
            continue
        have = "SPA" if has_spa else "STR"
        miss = "STR" if has_spa else "SPA"
        ie = idx.label_entry.get((split, have, labels[have]))
        se = idx.source_entry.get((split, have, labels[have]))
        if not ie or not se:
            continue
        doc = load_coco_bytes(review._read_zip(*ie), source=ie[1])
        bbox = content_bbox(doc)
        if bbox is None:
            continue
        img = Image.open(io.BytesIO(review._read_zip(*se)))
        preds = predict_missing(models[miss], img, bbox, cls_of[miss], imgsz, conf)
        if not preds:
            continue
        coco = to_coco(preds, img.size[0], img.size[1], labels[have], miss)
        (PRED_DIR / f"{miss}_{labels[have]}.json").write_text(
            json.dumps(coco, ensure_ascii=False), encoding="utf-8")
        stat[f"{have}->{miss}"] += 1
        n += 1
        if limit and n >= limit:
            break
        if n % 200 == 0:
            print(f"  ...{n} 예측")
    return {"predicted": n, "detail": stat, "out": str(PRED_DIR)}


def _self_test():
    """좌표 역변환 검증(YOLO 불요): 정변환→역변환 왕복 일치."""
    cx0, cy0, cw, ch, imgsz = 100.0, 200.0, 800.0, 600.0, 1024
    s = imgsz / max(cw, ch); nw, nh = cw * s, ch * s
    orig_pts = [(150.0, 250.0), (700.0, 500.0), (880.0, 780.0)]
    fwd = [((x - cx0) * (nw / cw), (y - cy0) * (nh / ch)) for x, y in orig_pts]
    back = reverse_poly(fwd, cx0, cy0, cw, nw, ch, nh)
    err = max(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(orig_pts, back))
    print(f"좌표 왕복 오차: {err:.6f} px → {'OK' if err < 1e-6 else 'FAIL'}")
    return err < 1e-6


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="run", choices=["run"])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--spa-weights", default="runs/segment/spa/weights/best.pt")
    ap.add_argument("--str-weights", default="runs/segment/str/weights/best.pt")
    ap.add_argument("--split", default="Training")
    ap.add_argument("--imgsz", type=int, default=1024)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    if a.self_test:
        sys.exit(0 if _self_test() else 1)
    print(json.dumps(run(a.spa_weights, a.str_weights, a.split, a.imgsz, a.conf,
                         a.limit), ensure_ascii=False, indent=2))
