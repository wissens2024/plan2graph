"""위상편집용 AI-Hub 코퍼스 데이터 소스 — zip(원천 PNG + 라벨 COCO)에서 직접 로드.

샘플(data/raw/linked_demo, 1건) 대신 **실제 AI-Hub 코퍼스(43k)** 를 편집 대상으로.
검수(inspect_excluded)의 인덱스를 재사용해 fingerprint별 PNG·라벨 zip 엔트리를 찾고,
coco.load_coco_bytes + geometry.assemble_drawing 으로 Drawing을 조립한다(파일 추출 불필요).
"""
from __future__ import annotations

import zipfile

from . import inspect_excluded as _ix
from .coco import load_coco_bytes
from .geometry import assemble_drawing


def scan(split=("Training", "Validation"), house: str | None = None) -> list[dict]:
    """편집 대상 목록(SPA 있는 도면만). 반환 [{plan_id, house, sig, png, labels}].
    labels = {라벨종류: (zip, entry)} (COCO). png = (zip, entry) (원천)."""
    idx = _ix.build_index(split)         # sig -> {label: {zip, entry, key, house}} (원천 PNG)
    lbl = _ix.label_index(split)         # {SPA/STR: {key: (zip, entry)}}          (라벨 COCO)
    out = []
    for sig, d in idx.items():
        if "SPA" not in d:               # 방(SPA) 없으면 편집 불가
            continue
        h = d["SPA"]["house"]
        if house and h != house:
            continue
        labels = {lt: lbl[lt][d[lt]["key"]]
                  for lt in ("SPA", "STR")
                  if lt in d and d[lt]["key"] in lbl.get(lt, {})}
        if "SPA" not in labels:          # SPA COCO 없으면 스킵
            continue
        out.append({"plan_id": f"{h}_FP_{sig}", "house": h, "sig": sig,
                    "png": (d["SPA"]["zip"], d["SPA"]["entry"]), "labels": labels})
    return sorted(out, key=lambda r: r["plan_id"])


def load(rec: dict, scale=None):
    """rec(scan 산출) → (Drawing, png_bytes). 위상편집 load_plan과 동일 형태."""
    import config
    docs = []
    for lt, (zp, entry) in rec["labels"].items():
        with zipfile.ZipFile(zp) as zf:
            docs.append(load_coco_bytes(zf.read(entry), source=f"{lt}:{entry}"))
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
