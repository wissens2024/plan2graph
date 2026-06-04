"""프로토타입 — 도면 raster에서 scale(mm/px) 역산.

AI-Hub OCR 라벨엔 치수가 없다(방 이름만). 그러나 원본 PNG에는 치수선·축척비율이
선명히 있고 PNG 메타에 DPI가 있다. 두 독립 방법으로 scale을 구해 교차검증한다.

방법 A (DPI + 축척비율):
  PNG가 DPI(예 144) 보유 + 도면에 "축척 = 1/N" 인쇄. paper-px↔real 관계로:
    scale_mm_per_px = N * 25.4 / DPI
방법 B (치수 숫자 기하):
  한 치수선의 인접 숫자 i, i+1은 각 구간 중점에 놓인다. 두 숫자 픽셀 중심거리 d_px는
  (value_i + value_{i+1}) / 2 픽셀에 해당 → scale = (value_i+value_{i+1}) / (2*d_px).

의존성: rapidocr-onnxruntime(경량, torch 불필요), Pillow.
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_NUM = re.compile(r"^\d{2,5}$")          # 치수 숫자(2~5자리, mm)
_RATIO = re.compile(r"1\s*/\s*(\d{2,4})")  # 축척 1/N

_OCR = None


def _ocr():
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR = RapidOCR()
    return _OCR


def _box_center(box):
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return (sum(xs) / 4.0, sum(ys) / 4.0)


def _run_ocr_region(img, x0, y0, x1, y1, ox, oy):
    """이미지 영역 OCR → [(cx,cy,text,conf)] (좌표는 원본 기준)."""
    import numpy as np
    crop = img.crop((x0, y0, x1, y1))
    res, _ = _ocr()(np.array(crop))
    out = []
    if res:
        for box, txt, conf in res:
            cx, cy = _box_center(box)
            out.append((cx + x0, cy + y0, txt.strip(), float(conf)))
    return out


def estimate_scale(sheet) -> dict:
    """Sheet → scale 추정(방법 A/B + 교차검증). dict 반환."""
    from PIL import Image
    img = Image.open(io.BytesIO(sheet.png_bytes))
    dpi = img.info.get("dpi", (None, None))[0]
    img = img.convert("L")
    ow, oh = img.size

    # 방 영역 bbox로 마진(치수선 영역) 한정
    xs, ys = [], []
    for r in sheet.dr.rooms:
        if r.polygon is not None:
            a, b, c, d = r.polygon.bounds
            xs += [a, c]; ys += [b, d]
    if not xs:
        return {"error": "no rooms"}
    bx0, by0, bx1, by1 = min(xs), min(ys), max(xs), max(ys)
    pad = 320

    tokens = []
    # 하단·상단 가로 치수열 + 타이틀(축척) 영역
    regions = [
        (int(bx0) - 60, int(by1) - 30, int(bx1) + 60, min(oh, int(by1) + pad)),   # 하단
        (int(bx0) - 60, max(0, int(by0) - pad), int(bx1) + 60, int(by0) + 30),    # 상단
    ]
    for (x0, y0, x1, y1) in regions:
        tokens += _run_ocr_region(img, x0, y0, x1, y1, 0, 0)

    # 축척 비율 N
    ratio_n = None
    for cx, cy, txt, conf in tokens:
        m = _RATIO.search(txt.replace(" ", ""))
        if m:
            ratio_n = int(m.group(1))
            break

    # 치수 숫자(값+위치). 상세치수·문폭(작은값)·과대값 잡음 배제.
    nums = [(cx, cy, int(txt)) for cx, cy, txt, conf in tokens
            if _NUM.match(txt) and conf >= 0.8 and 200 <= int(txt) <= 12000]

    # ── 주력: 치수 숫자 기하 역산 ──
    # 같은 가로 치수선(y 근접)에서 인접 숫자 i,i+1은 각 구간 중점에 놓임 →
    # 중심거리 d_px = (v_i+v_{i+1})/2 픽셀 → scale = (v_i+v_{i+1})/(2*d_px).
    samples = []
    rows: dict = {}
    for cx, cy, v in nums:
        rows.setdefault(round(cy / 35), []).append((cx, v))
    for row in rows.values():
        if len(row) < 3:          # 실제 치수열(숫자 3+)만 — 흩어진 라벨 잡음 배제
            continue
        row.sort()
        for (x1_, v1), (x2_, v2) in zip(row, row[1:]):
            d = abs(x2_ - x1_)
            if d > 10:
                samples.append((v1 + v2) / (2.0 * d))
    scale = None
    if samples:
        samples.sort()
        scale = samples[len(samples) // 2]  # 중앙값(견고)

    # 신뢰 게이트: 침실 중앙값이 표준 범위(6~16㎡)면 채택, 아니면 격리.
    bed = sorted(r.area_px for r in sheet.dr.rooms
                 if r.class_name == "공간_침실" and r.polygon)
    bed_m2 = (bed[len(bed) // 2] * scale ** 2 / 1e6) if (bed and scale) else None
    if scale is None or bed_m2 is None:
        conf = "none"
    elif 6.0 <= bed_m2 <= 16.0 and len(samples) >= 4:
        conf = "ok"
    else:
        conf = "low"

    scale_dpi = (ratio_n * 25.4 / dpi) if (ratio_n and dpi) else None
    return {
        "scale_mm_per_px": round(scale, 4) if scale else None,
        "confidence": conf,
        "bedroom_med_m2": round(bed_m2, 1) if bed_m2 else None,
        "n_dim_numbers": len(nums), "n_samples": len(samples),
        "ratio_n": ratio_n, "dpi": round(dpi, 1) if dpi else None,
        "scale_dpi_ratio_unreliable": round(scale_dpi, 4) if scale_dpi else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 전체 적용: scale.csv 생성(병렬) → "ok"만 그래프에 ㎡ 적용 → 불확실분 격리
# ─────────────────────────────────────────────────────────────────────────────
_IDX = None


def _idx():
    global _IDX
    if _IDX is None:
        from plan2graph import review
        _IDX = review.build_indices(("Training", "Validation"))  # 통합 풀
    return _IDX


def _scale_one(sid: str) -> dict:
    from plan2graph import review
    try:
        sh = review.load_sheet(sid, _idx())
        if sh is None:
            return {"sheet_id": sid, "confidence": "none", "scale_mm_per_px": None}
        r = estimate_scale(sh)
        r["sheet_id"] = sid
        return r
    except Exception as e:  # noqa: BLE001
        return {"sheet_id": sid, "confidence": "none", "scale_mm_per_px": None,
                "error": str(e)[:50]}


def scale_pass(jobs: int = 6) -> Path:
    """채택 그래프의 고유 시트 전체에 scale OCR → data/interim/scale.csv."""
    import csv  # noqa: F401
    import glob
    import config  # noqa: F401
    from plan2graph import sources
    gdir = sources.graphs_dir("aihub")   # staging/aihub/graphs (processed 은퇴)
    sids = []
    seen = set()
    for fp in glob.glob(str(gdir / "*.json")):
        s = Path(fp).stem.rsplit("_u", 1)[0]
        if s and s not in seen:
            seen.add(s); sids.append(s)
    # 증분: 이미 scale.csv에 신뢰결과(ok/low/none) 있는 시트는 건너뜀
    done = {s: r for s, r in load_scale_csv().items()
            if r.get("confidence") in ("ok", "low", "none", "quarantined")}
    todo = [s for s in sids if s not in done]
    print(f"고유 시트 {len(sids):,}개 중 신규 {len(todo):,}개 scale OCR (jobs={jobs})...")
    sids = todo
    if not sids:
        print("  신규 없음 — 기존 scale.csv 유지")
        return config.INTERIM_DIR / "scale.csv"
    if jobs > 1:
        from joblib import Parallel, delayed
        results = Parallel(n_jobs=jobs, prefer="processes")(
            delayed(_scale_one)(s) for s in sids)
    else:
        results = [_scale_one(s) for s in sids]
    out = config.INTERIM_DIR / "scale.csv"
    cols = ["sheet_id", "confidence", "scale_mm_per_px", "bedroom_med_m2",
            "n_samples", "ratio_n", "dpi", "source"]
    # 기존(done) + 신규(results) 병합 — 증분이므로 기존 보존(수동보정 포함)
    merged = dict(done)
    for r in results:
        merged[r["sheet_id"]] = r
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in merged.values():
            w.writerow({k: r.get(k, "") for k in cols})
    import collections
    c = collections.Counter(r.get("confidence") for r in results)
    print(f"  신규 결과: {dict(c)} · 전체 {len(merged):,}행 → {out}")
    return out


def apply_scale() -> None:
    """scale.csv의 'ok'만 그래프 JSON에 ㎡ 적용. 불확실분은 scale_quarantine.csv."""
    import csv
    import glob
    import json
    import config
    scale_map = {}
    with open(config.INTERIM_DIR / "scale.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            scale_map[row["sheet_id"]] = row
    from plan2graph import sources
    applied = 0
    quarantined = set()
    for fp in glob.glob(str(sources.graphs_dir("aihub") / "*.json")):
        rec = json.loads(Path(fp).read_text(encoding="utf-8"))
        sid = rec["graph_id"].rsplit("_u", 1)[0]
        info = scale_map.get(sid)
        if not info or info["confidence"] != "ok":
            rec["meta"]["scale"] = None
            rec["meta"]["scale_confidence"] = info["confidence"] if info else "none"
            if info:
                quarantined.add(sid)
            Path(fp).write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
            continue
        s = float(info["scale_mm_per_px"])
        rec["meta"]["scale"] = s
        rec["meta"]["scale_confidence"] = "ok"
        rec["meta"]["area_unit"] = "m2"
        tot = 0.0
        for nd in rec["layout"]["nodes"]:
            if nd.get("area_px2"):
                m2 = round(nd["area_px2"] * s ** 2 / 1e6, 2)
                nd["area_m2"] = m2
                tot += m2
        rec["meta"]["floor_area_m2"] = round(tot, 1)
        Path(fp).write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        applied += 1
    # 격리 목록
    qp = config.INTERIM_DIR / "scale_quarantine.csv"
    with open(qp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sheet_id", "confidence", "scale_mm_per_px", "bedroom_med_m2"])
        for sid in sorted(quarantined):
            i = scale_map[sid]
            w.writerow([sid, i["confidence"], i["scale_mm_per_px"], i["bedroom_med_m2"]])
    print(f"scale 적용: {applied:,} 그래프(㎡) · 격리 시트 {len(quarantined):,} → {qp}")


# ─────────────────────────────────────────────────────────────────────────────
# 콘솔 보정용: scale.csv 단건 갱신 + 해당 시트 그래프에 ㎡ 적용
# ─────────────────────────────────────────────────────────────────────────────
def load_scale_csv() -> dict:
    import csv
    import config
    p = config.INTERIM_DIR / "scale.csv"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return {r["sheet_id"]: r for r in csv.DictReader(f)}


def update_scale_row(sheet_id: str, scale: float | None, confidence: str,
                     source: str = "manual") -> None:
    """scale.csv의 한 시트 행을 갱신(없으면 추가)."""
    import csv
    import config
    rows = load_scale_csv()
    rows[sheet_id] = {
        "sheet_id": sheet_id, "confidence": confidence,
        "scale_mm_per_px": round(scale, 4) if scale else "",
        "bedroom_med_m2": rows.get(sheet_id, {}).get("bedroom_med_m2", ""),
        "n_samples": rows.get(sheet_id, {}).get("n_samples", ""),
        "ratio_n": rows.get(sheet_id, {}).get("ratio_n", ""),
        "dpi": rows.get(sheet_id, {}).get("dpi", ""), "source": source,
    }
    cols = ["sheet_id", "confidence", "scale_mm_per_px", "bedroom_med_m2",
            "n_samples", "ratio_n", "dpi", "source"]
    with open(config.INTERIM_DIR / "scale.csv", "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows.values():
            w.writerow({k: r.get(k, "") for k in cols})


def apply_scale_one_sheet(sheet_id: str, scale: float | None) -> int:
    """한 시트의 그래프 JSON들에 scale 적용(㎡). scale=None이면 해제."""
    import glob
    import json
    import config  # noqa: F401
    from plan2graph import sources
    n = 0
    for fp in glob.glob(str(sources.graphs_dir("aihub") / f"{sheet_id}_u*.json")):
        rec = json.loads(Path(fp).read_text(encoding="utf-8"))
        if scale:
            rec["meta"]["scale"] = round(scale, 4)
            rec["meta"]["scale_confidence"] = "ok"
            rec["meta"]["area_unit"] = "m2"
            tot = 0.0
            for nd in rec["layout"]["nodes"]:
                if nd.get("area_px2"):
                    m2 = round(nd["area_px2"] * scale ** 2 / 1e6, 2)
                    nd["area_m2"] = m2
                    tot += m2
            rec["meta"]["floor_area_m2"] = round(tot, 1)
        else:
            rec["meta"]["scale"] = None
            rec["meta"]["scale_confidence"] = "quarantined"
        Path(fp).write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        n += 1
    return n


def bedroom_med_m2_at(sheet, scale: float) -> float | None:
    bed = sorted(r.area_px for r in sheet.dr.rooms
                 if r.class_name == "공간_침실" and r.polygon)
    return round(bed[len(bed) // 2] * scale ** 2 / 1e6, 1) if bed else None


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import json
    if len(sys.argv) > 1 and sys.argv[1] == "pass":
        scale_pass(jobs=int(sys.argv[2]) if len(sys.argv) > 2 else 6)
        sys.exit()
    if len(sys.argv) > 1 and sys.argv[1] == "apply":
        apply_scale()
        sys.exit()
    from plan2graph import review
    sid = sys.argv[1] if len(sys.argv) > 1 else None
    idx = review.build_indices(("Training",))
    if sid is None:
        import glob
        rec = json.loads(Path(sorted(glob.glob(
            str(ROOT / "data/processed/graphs/*.json")))[0]).read_text(encoding="utf-8"))
        sid = rec["graph_id"].rsplit("_u", 1)[0]
    sheet = review.load_sheet(sid, idx, "Training")
    print(f"도면: {sid}")
    res = estimate_scale(sheet)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    s = res.get("scale_mm_per_px")
    if s:
        # 검증: 침실 중앙값(표준 ~10㎡), 거실(개방형이라 클 수 있음)
        def med_m2(cls):
            a = sorted(r.area_px for r in sheet.dr.rooms
                       if r.class_name == cls and r.polygon)
            return a[len(a) // 2] * (s ** 2) / 1e6 if a else None
        bm = med_m2("공간_침실")
        print(f"  검증(scale={s} mm/px): 침실 중앙값 {bm:.1f}㎡ "
              f"(표준 8~12㎡면 OK), 거실 중앙값 {med_m2('공간_거실'):.1f}㎡")
