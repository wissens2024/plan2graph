"""위상편집용 AI-Hub 코퍼스 데이터 소스 — zip(원천 PNG + 라벨 COCO)에서 직접 로드.

샘플(data/raw/linked_demo, 1건) 대신 **실제 AI-Hub 코퍼스(43k)** 를 편집 대상으로.
검수(inspect_excluded)의 인덱스를 재사용해 fingerprint별 PNG·라벨 zip 엔트리를 찾고,
coco.load_coco_bytes + geometry.assemble_drawing 으로 Drawing을 조립한다(파일 추출 불필요).
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import config  # noqa: E402

from . import inspect_excluded as _ix
from .coco import load_coco_bytes
from .geometry import assemble_drawing

PRED_DIR = config.DATA_DIR / "v2v" / "predicted"        # V2V 예측 라벨(보유라벨 페어링) COCO
PRED_IMG_DIR = config.DATA_DIR / "v2v" / "predicted_img"  # objocr 이미지-직접 예측(sig 키) COCO


def _predicted_index() -> dict:
    """V2V 예측 라벨 색인 {(label_type, have_key): path}. 파일명 `{MISS}_{have_key}.json`
    (build_predicted 페어링 규약 — 빠진 종류를 보유라벨 key로 예측 저장)."""
    idx: dict = {}
    if PRED_DIR.exists():
        for f in PRED_DIR.glob("*.json"):
            parts = f.stem.split("_", 1)            # "SPA_000213273" → ["SPA","000213273"]
            if len(parts) == 2 and parts[0] in ("SPA", "STR"):
                idx[(parts[0], parts[1])] = str(f)
    return idx


def _predicted_img_index() -> dict:
    """objocr 이미지-직접 예측 색인 {(label_type, sig): path}. 파일명 `{TYPE}_{sig}.json`
    (v2v_infer.run_objocr 규약 — SPA/STR 라벨 없는 도면을 PNG에서 직접 예측, sig로 키).
    sig는 'crc_size'라 언더바를 포함하므로 split('_', 1)로 TYPE만 분리."""
    idx: dict = {}
    if PRED_IMG_DIR.exists():
        for f in PRED_IMG_DIR.glob("*.json"):
            if f.stem.startswith("_"):              # _provenance.json 등 메타 제외
                continue
            parts = f.stem.split("_", 1)            # "SPA_c6c049b6_17442034" → ["SPA","c6c049b6_17442034"]
            if len(parts) == 2 and parts[0] in ("SPA", "STR"):
                idx[(parts[0], parts[1])] = str(f)
    return idx


def _extra_label_index(split, labels=("OBJ", "OCR")) -> dict:
    """OBJ/OCR 라벨 zip 색인 {OBJ/OCR: {key: (zip, entry)}}.
    `inspect_excluded.label_index`는 SPA/STR만 담고(그 함수는 GUI 오버레이가 공유하므로
    OBJ/OCR 추가 시 렌더가 바뀜) → 여기서 동일 기계로 OBJ/OCR만 따로 색인(격리·무영향).
    dual 도면 안에서 OBJ ~90%·OCR ~91% 링크(기구·역할명 신호 복원)."""
    from pathlib import Path as _P
    import config
    sp = _ix._norm_splits(split)
    zips = [z for z in _ix.discover_zips()
            if z["content"] == "라벨" and z["split"] in sp]
    idx: dict = {lt: {} for lt in labels}
    for z, info in _ix.iter_zipinfos(zips):
        m = _ix.parse_name(_P(info.filename).stem)
        if m and m["label"] in idx and m["drawing"] == config.TARGET_DRAWING_TYPE:
            idx[m["label"]][m["key"]] = (str(z["path"]), info.filename)
    return idx


def scan(split=("Training", "Validation"), house: str | None = None,
         with_obj_ocr: bool = True, with_predicted: bool = True) -> list[dict]:
    """편집/SVG 변환 대상 목록. 반환 [{plan_id, house, sig, png, labels}].
    labels = {라벨종류: (zip,entry)} 또는 ("__file__", path)(V2V 예측). png = (zip,entry).
    with_predicted=True면 **V2V 예측 라벨을 union**(실라벨 우선, 빠진 종류만 채움) →
    SPA-only는 STR(문) 예측 보강, STR-only는 SPA(방) 예측으로 방 확보(SVG 가능).
    objocr(OBJ/OCR만, SPA·STR 라벨 없음)는 PNG에서 직접 예측한 SPA(sig 키)가 있으면
    이미지-anchor로 대상화 — `_predicted_img_index`([[v2v_infer]] run_objocr)."""
    idx = _ix.build_index(split)         # sig -> {label: {zip, entry, key, house}} (원천 PNG)
    lbl = _ix.label_index(split)         # {SPA/STR: {key: (zip, entry)}}          (라벨 COCO)
    extra = _extra_label_index(split) if with_obj_ocr else {}   # {OBJ/OCR: {key: ...}}
    pred = _predicted_index() if with_predicted else {}         # {(type, have_key): path}
    pred_img = _predicted_img_index() if with_predicted else {}  # {(type, sig): path} objocr 직접예측
    out = []
    for sig, d in idx.items():
        anchor = d.get("SPA") or d.get("STR")    # 방 우선, 없으면 구조(이미지·house 기준)
        if anchor is None:                       # objocr — SPA/STR 라벨 없음. PNG를 anchor로
            anchor = d.get("OBJ") or d.get("OCR")
            if anchor is None:
                continue
        h = anchor["house"]
        if house and h != house:
            continue
        labels = {lt: lbl[lt][d[lt]["key"]]
                  for lt in ("SPA", "STR")
                  if lt in d and d[lt]["key"] in lbl.get(lt, {})}
        for lt in ("OBJ", "OCR"):        # 지문(sig)일치 + 라벨 COCO 존재 시만 병합
            if lt in d and d[lt]["key"] in extra.get(lt, {}):
                labels[lt] = extra[lt][d[lt]["key"]]
        if with_predicted:               # 빠진 종류만 예측으로 채움(union not replace)
            for have, miss in (("STR", "SPA"), ("SPA", "STR")):  # 보유라벨 bbox 페어 예측
                if miss not in labels and have in d:
                    p = pred.get((miss, d[have]["key"]))
                    if p:
                        labels[miss] = ("__file__", p)
            for lt in ("SPA", "STR"):    # 이미지-직접 예측(objocr — sig 키, 라벨 없을 때만)
                if lt not in labels:
                    p = pred_img.get((lt, sig))
                    if p:
                        labels[lt] = ("__file__", p)
        if "SPA" not in labels:          # 방(실 or 예측) 없으면 SVG 불가
            continue
        out.append({"plan_id": f"{h}_FP_{sig}", "house": h, "sig": sig,
                    "png": (anchor["zip"], anchor["entry"]), "labels": labels})
    return sorted(out, key=lambda r: r["plan_id"])


def load(rec: dict, scale=None):
    """rec(scan 산출) → (Drawing, png_bytes). 예측 라벨(("__file__", path))은 파일서 직접 읽음."""
    docs = []
    for lt, (zp, entry) in rec["labels"].items():
        if zp == "__file__":                         # V2V 예측 라벨(COCO 파일)
            data = Path(entry).read_bytes()
        else:
            with zipfile.ZipFile(zp) as zf:
                data = zf.read(entry)
        docs.append(load_coco_bytes(data, source=f"{lt}:{entry}"))
    sc = scale if scale is not None else config.DEFAULT_SCALE
    dr = assemble_drawing(docs, image_path=None, scale=sc)
    zp, entry = rec["png"]
    with zipfile.ZipFile(zp) as zf:
        png = zf.read(entry)
    return dr, png


if __name__ == "__main__":   # 서버에서: PLAN2GRAPH_RAW 설정 후 실행
    import sys
    from pathlib import Path as _P
    _root = _P(__file__).resolve().parents[2]
    for _p in (str(_root), str(_root / "src")):
        if _p not in sys.path:
            sys.path.insert(0, _p)
    recs = scan(house="APT")
    print(f"AI-Hub 편집대상(APT, SPA보유): {len(recs):,}건")
    if recs:
        r = recs[0]
        dr, png = load(r)
        print(f"  {r['plan_id']}  labels={list(r['labels'])}  png={len(png):,}B")
        print(f"  rooms={len(dr.rooms)} doors={len(dr.doors)} windows={len(dr.windows)} "
              f"walls={len(dr.walls)} objects={len(dr.objects)} {dr.width}x{dr.height}")
