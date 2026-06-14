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
PRED_IMG_DIR = config.DATA_DIR / "v2v" / "predicted_img"   # objocr 이미지-직접 예측(SPA/STR 라벨 없음)
SPACE_CLASSES = list(config.SPACE_CLASSES)
STRUCT_CLASSES = list(config.STRUCTURE_CLASSES)


def _weight_fingerprint(path: str) -> dict:
    """가중치 파일 핀: 절대경로 + sha256[:16] + 크기 + mtime. 예측물↔가중치 연결용."""
    import hashlib
    p = Path(path)
    info = {"path": str(p.resolve()) if p.exists() else str(path), "exists": p.exists()}
    if p.exists():
        b = p.read_bytes()
        info.update(sha256=hashlib.sha256(b).hexdigest()[:16], bytes=len(b),
                    mtime=__import__("datetime").datetime.fromtimestamp(
                        p.stat().st_mtime).isoformat(timespec="seconds"))
    return info


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
                    conf: float = 0.25, device=None) -> list[dict]:
    """크롭 영역에서 model로 예측 → 원본좌표 폴리곤 인스턴스 [{class_name,segmentation,bbox,score}]."""
    crop, cx0, cy0, cw, nw, ch, nh = _crop_resize(img.convert("L"), bbox, imgsz)
    res = model.predict(crop.convert("RGB"), imgsz=imgsz, conf=conf, verbose=False,
                        device=device)[0]
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
        imgsz: int = 1024, conf: float = 0.25, limit: int | None = None,
        device=None, only: str | None = None) -> dict:
    """단일라벨 FP 도면에 빠진 종류 예측 → predicted/ 에 COCO 저장.
    device: GPU index(2-GPU 분할용). only=SPA|STR: 그 라벨 가진 도면만(방향 분할)."""
    import io
    from ultralytics import YOLO
    from PIL import Image
    from plan2graph import review, unpack, experiments as exp
    from plan2graph.coco import load_coco_bytes

    models = {"SPA": YOLO(spa_weights), "STR": YOLO(str_weights)}
    cls_of = {"SPA": SPACE_CLASSES, "STR": STRUCT_CLASSES}
    idx = review.build_indices((split,))
    fmap = unpack.fingerprint_label_map(split=split)
    PRED_DIR.mkdir(parents=True, exist_ok=True)
    # ── provenance: 어느 가중치·conf·git으로 예측했나(예측물↔모델 연결, runs ledger 등재) ──
    wfp = {"SPA": _weight_fingerprint(spa_weights), "STR": _weight_fingerprint(str_weights)}
    git = exp.git_commit()
    stamp = {"spa": Path(spa_weights).name, "str": Path(str_weights).name,
             "conf": conf, "imgsz": imgsz, "git": git[:8]}
    n = 0
    stat = {"SPA->STR": 0, "STR->SPA": 0}
    for fp, labels in fmap.items():
        has_spa, has_str = "SPA" in labels, "STR" in labels
        if has_spa == has_str:        # 둘 다 or 둘 다 없음 → 이번 버전 스킵
            continue
        have = "SPA" if has_spa else "STR"
        miss = "STR" if has_spa else "SPA"
        if only and have != only:     # 방향 분할(2-GPU): 지정 라벨 가진 것만
            continue
        ie = idx.label_entry.get((split, have, labels[have]))
        se = idx.source_entry.get((split, have, labels[have]))
        if not ie or not se:
            continue
        doc = load_coco_bytes(review._read_zip(*ie), source=ie[1])
        bbox = content_bbox(doc)
        if bbox is None:
            continue
        img = Image.open(io.BytesIO(review._read_zip(*se)))
        preds = predict_missing(models[miss], img, bbox, cls_of[miss], imgsz, conf, device)
        if not preds:
            continue
        coco = to_coco(preds, img.size[0], img.size[1], labels[have], miss)
        coco["_v2v"] = {**stamp, "weights": stamp[miss.lower()], "direction": f"{have}->{miss}"}
        (PRED_DIR / f"{miss}_{labels[have]}.json").write_text(
            json.dumps(coco, ensure_ascii=False), encoding="utf-8")
        stat[f"{have}->{miss}"] += 1
        n += 1
        if limit and n >= limit:
            break
        if n % 200 == 0:
            print(f"  ...{n} 예측")
    # ── 디렉터리 provenance + runs 원장 등재(생성모델과 동일 ledger) ──
    prov = {"kind": "v2v_infer", "created": exp._now(), "git_commit": git,
            "env": exp.env_provenance(), "split": split, "imgsz": imgsz, "conf": conf,
            "device": device, "only": only, "limit": limit,
            "weights": wfp, "predicted": n, "detail": stat}
    (PRED_DIR / "_provenance.json").write_text(
        json.dumps(prov, ensure_ascii=False, indent=2), encoding="utf-8")
    exp.append_index({"kind": "v2v_infer", "git_commit": git, "split": split,
                      "conf": conf, "imgsz": imgsz, "only": only,
                      "spa_weights": wfp["SPA"].get("sha256"),
                      "str_weights": wfp["STR"].get("sha256"),
                      "predicted": n, **stat})
    return {"predicted": n, "detail": stat, "out": str(PRED_DIR),
            "provenance": str(PRED_DIR / "_provenance.json")}


def _cluster_units(boxes: list, gap_frac: float = 0.6) -> list[list[int]]:
    """방 bbox 리스트 [(x0,y0,x1,y1)] → 세대 군집(인덱스 리스트들). 방 bbox를 margin만큼
    확장해 겹치면 같은 세대(union-find). margin = gap_frac × 방 변길이 중앙값(적응적) —
    한 세대 안 방들은 인접/근접, 세대 사이는 큰 여백이라 자연 분리. 멀티세대 시트 → 세대 N개."""
    n = len(boxes)
    if n == 0:
        return []
    dims = sorted([b[2] - b[0] for b in boxes] + [b[3] - b[1] for b in boxes])
    med = dims[len(dims) // 2] if dims else 0.0
    m = gap_frac * med
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i in range(n):
        bi = boxes[i]
        for j in range(i + 1, n):
            bj = boxes[j]
            if not (bi[2] + m < bj[0] or bj[2] + m < bi[0]
                    or bi[3] + m < bj[1] or bj[3] + m < bi[1]):   # 확장 후 겹침
                parent[find(i)] = find(j)
    groups: dict = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _offset_pred(p: dict, ox: float, oy: float) -> dict:
    """예측 인스턴스의 좌표(세대 크롭 기준) → 시트 좌표로 평행이동(+ox,+oy)."""
    seg = [[(v + ox if k % 2 == 0 else v + oy) for k, v in enumerate(ring)]
           for ring in p["segmentation"]]
    b = p["bbox"]
    return {**p, "segmentation": seg, "bbox": [b[0] + ox, b[1] + oy, b[2], b[3]]}


def run_objocr(spa_weights: str, str_weights: str,
               split=("Training", "Validation"), imgsz: int = 1024,
               conf: float = 0.25, limit: int | None = None, device=None,
               spa_imgsz: int = 768, str_imgsz: int = 1024,
               min_unit_rooms: int = 2, targets: list | None = None) -> dict:
    """objocr 도면(OBJ/OCR 라벨만, SPA·STR 둘 다 없음) → **세대별로 쪼개 이미지 직접** SPA·STR 예측.

    **2-패스**(전체시트 1패스는 다세대 시트에서 방이 작게 보여 STR이 부실 → 세대 크롭으로 해결):
    - 패스1: 전체 시트에 SPA 검출 → 방 bbox를 `_cluster_units`로 **세대 군집**으로 분할.
    - 패스2: **세대별로 이미지 크롭** → 그 크롭에 SPA(768)·STR(1024) **재검출**(학습 분포에 맞는 크기)
      → 좌표를 시트로 역오프셋해 누적.
    결과는 predicted_img/{TYPE}_{sig}.json (시트 좌표, sig 키 — aihub_source.scan 이미지-anchor가
    조회, 빌드의 _states_from_dr가 다시 세대 분리). 방(SPA) 0이면 그래프 불가라 건너뜀.
    노이즈는 빌드에서 보정필요로 분류, 사람이 교정([[corrected-correction-not-verification]])."""
    import io
    import zipfile as _zip
    from ultralytics import YOLO
    from PIL import Image
    from plan2graph import inspect_excluded as ix, experiments as exp

    models = {"SPA": YOLO(spa_weights), "STR": YOLO(str_weights)}
    cls_of = {"SPA": SPACE_CLASSES, "STR": STRUCT_CLASSES}
    # targets 지정 시 그 레코드만(이미지직접 2-패스 구제용 — str_only 등 SPA 미예측 도면).
    # 미지정이면 기존 objocr-only 전량. 둘 다 predicted_img/{TYPE}_{sig}.json 저장(additive).
    recs = targets if targets is not None else ix.objocr_only_records(split)
    PRED_IMG_DIR.mkdir(parents=True, exist_ok=True)
    wfp = {"SPA": _weight_fingerprint(spa_weights), "STR": _weight_fingerprint(str_weights)}
    git = exp.git_commit()
    stamp = {"spa": Path(spa_weights).name, "str": Path(str_weights).name,
             "conf": conf, "spa_imgsz": spa_imgsz, "str_imgsz": str_imgsz, "git": git[:8]}
    n = 0
    stat = {"SPA": 0, "STR": 0, "units": 0, "skip_noroom": 0, "skip_png": 0}
    for rec in recs:
        sig = rec["sig"]
        try:
            with _zip.ZipFile(rec["zip"]) as zf:
                img = Image.open(io.BytesIO(zf.read(rec["entry"])))
                img.load()
        except Exception as e:  # noqa: BLE001
            print(f"  [skip] {sig}: PNG 로드 실패 {e}")
            stat["skip_png"] += 1
            continue
        W, H = img.size
        # ── 패스1: 전체 시트 SPA로 방 위치 파악 → 세대 군집 ──
        sheet_spa = predict_missing(models["SPA"], img, (0.0, 0.0, float(W), float(H)),
                                    cls_of["SPA"], imgsz, conf, device)
        if not sheet_spa:
            stat["skip_noroom"] += 1
            continue
        boxes = [(p["bbox"][0], p["bbox"][1], p["bbox"][0] + p["bbox"][2],
                  p["bbox"][1] + p["bbox"][3]) for p in sheet_spa]
        clusters = _cluster_units(boxes)
        # ── 패스2: 세대별 크롭 재검출 → 시트 좌표 누적 ──
        spa_all, str_all = [], []
        pad = 40
        for idxs in clusters:
            if len(idxs) < min_unit_rooms:                   # 1방짜리 잡음 군집 제외
                continue
            ux0 = max(0, int(min(boxes[i][0] for i in idxs) - pad))
            uy0 = max(0, int(min(boxes[i][1] for i in idxs) - pad))
            ux1 = min(W, int(max(boxes[i][2] for i in idxs) + pad))
            uy1 = min(H, int(max(boxes[i][3] for i in idxs) + pad))
            crop = img.crop((ux0, uy0, ux1, uy1))
            cw, ch = crop.size
            su = predict_missing(models["SPA"], crop, (0.0, 0.0, float(cw), float(ch)),
                                 cls_of["SPA"], spa_imgsz, conf, device)
            tu = predict_missing(models["STR"], crop, (0.0, 0.0, float(cw), float(ch)),
                                 cls_of["STR"], str_imgsz, conf, device)
            if not su:
                continue
            spa_all.extend(_offset_pred(p, ux0, uy0) for p in su)
            str_all.extend(_offset_pred(p, ux0, uy0) for p in tu)
            stat["units"] += 1
        if not spa_all:
            stat["skip_noroom"] += 1
            continue
        spa_coco = to_coco(spa_all, W, H, sig, "SPA")
        spa_coco["_v2v"] = {**stamp, "weights": stamp["spa"], "direction": "IMG->units->SPA"}
        (PRED_IMG_DIR / f"SPA_{sig}.json").write_text(
            json.dumps(spa_coco, ensure_ascii=False), encoding="utf-8")
        stat["SPA"] += 1
        if str_all:
            str_coco = to_coco(str_all, W, H, sig, "STR")
            str_coco["_v2v"] = {**stamp, "weights": stamp["str"], "direction": "IMG->units->STR"}
            (PRED_IMG_DIR / f"STR_{sig}.json").write_text(
                json.dumps(str_coco, ensure_ascii=False), encoding="utf-8")
            stat["STR"] += 1
        n += 1
        if limit and n >= limit:
            break
        if n % 200 == 0:
            print(f"  ...{n} objocr (세대 {stat['units']}/방시트 {stat['SPA']}/"
                  f"구조시트 {stat['STR']}/방없음 {stat['skip_noroom']})")
    sp_list = list(split) if not isinstance(split, str) else [split]
    prov = {"kind": "v2v_infer_objocr_units", "created": exp._now(), "git_commit": git,
            "env": exp.env_provenance(), "split": sp_list, "spa_imgsz": spa_imgsz,
            "str_imgsz": str_imgsz, "sheet_imgsz": imgsz, "conf": conf, "device": device,
            "limit": limit, "weights": wfp, "predicted": n, "detail": stat}
    _pf = "_provenance_rescue.json" if targets is not None else "_provenance.json"
    (PRED_IMG_DIR / _pf).write_text(
        json.dumps(prov, ensure_ascii=False, indent=2), encoding="utf-8")
    exp.append_index({"kind": "v2v_infer_objocr_units", "git_commit": git, "conf": conf,
                      "spa_imgsz": spa_imgsz, "str_imgsz": str_imgsz,
                      "spa_weights": wfp["SPA"].get("sha256"),
                      "str_weights": wfp["STR"].get("sha256"), "predicted": n, **stat})
    return {"predicted": n, "detail": stat, "out": str(PRED_IMG_DIR),
            "provenance": str(PRED_IMG_DIR / "_provenance.json")}


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
    ap.add_argument("cmd", nargs="?", default="run", choices=["run", "objocr"],
                    help="run=단일라벨 페어 예측 · objocr=OBJ/OCR만 도면 이미지직접 SPA·STR 예측")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--spa-weights", default="runs/segment/spa/weights/best.pt")
    ap.add_argument("--str-weights", default="runs/segment/str/weights/best.pt")
    ap.add_argument("--split", default="Training")
    ap.add_argument("--imgsz", type=int, default=1024)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--device", default=None, help="GPU index(2-GPU 분할용)")
    ap.add_argument("--only", default=None, choices=["SPA", "STR"],
                    help="그 라벨 가진 도면만(방향 분할, run 전용)")
    a = ap.parse_args()
    if a.self_test:
        sys.exit(0 if _self_test() else 1)
    dev = int(a.device) if a.device is not None else None
    if a.cmd == "objocr":
        # objocr는 Training/Validation 합쳐 한 풀로(포장 무관, [[inspect_excluded]] _norm_splits)
        sp = (a.split,) if a.split not in ("all", "ALL") else ("Training", "Validation")
        out = run_objocr(a.spa_weights, a.str_weights, sp, a.imgsz, a.conf, a.limit, dev)
    else:
        out = run(a.spa_weights, a.str_weights, a.split, a.imgsz, a.conf, a.limit, dev, a.only)
    print(json.dumps(out, ensure_ascii=False, indent=2))
