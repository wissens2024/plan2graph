"""샘플(개념증명): objocr 시트를 세대별로 쪼개 이미지 크롭 → 세대 크롭에서 SPA/STR 재검출.
전체시트 1패스 검출의 노이즈 대신, 세대 단위 이미지로 검출하면 깨끗해지는지 눈으로 확인.
세대 분리 = build_corrected_auto._states_from_dr(현관 가진 연결요소) 재사용. 폴더=data/v2v/unit_crops.
실행: PYTHONPATH=.:src python scripts/_sample_unit_crop.py [N]
"""
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from ultralytics import YOLO  # noqa: E402
from plan2graph import aihub_source as A, v2v_infer as V  # noqa: E402
import build_corrected_auto as B  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 3
DEV = 1
SPA_W = "runs/segment/v2v_runs/spa_yolov8n-seg_768_e100/weights/best.pt"
STR_W = "runs/segment/v2v_runs/str_yolov8n-seg_1024_e50/weights/best.pt"
OUT = config.DATA_DIR / "v2v" / "unit_crops"
OUT.mkdir(parents=True, exist_ok=True)

spa_model, str_model = YOLO(SPA_W), YOLO(STR_W)
SPA_C, STR_C = list(config.SPACE_CLASSES), list(config.STRUCTURE_CLASSES)


def _coords(poly):
    if poly is None:
        return []
    if hasattr(poly, "exterior"):                 # shapely
        return list(poly.exterior.coords)
    return list(poly)


# 전체시트 SPA 예측이 이미 있는 objocr만(load가 방을 조립할 수 있음)
pi = A._predicted_img_index()
spa_sigs = {k[1] for k in pi if k[0] == "SPA"}
recs = [r for r in A.scan(house="APT") if r["sig"] in spa_sigs][:N]
print(f"샘플 {len(recs)}장:", [r["plan_id"] for r in recs])

for r in recs:
    dr, png = A.load(r)
    img = Image.open(io.BytesIO(png)).convert("RGB")
    units = list(B._states_from_dr(dr, r["plan_id"], r["house"]))
    print(f"\n■ {r['plan_id']}: 전체시트 방 {len(dr.rooms)}개 → 세대 {len(units)}개  ({img.width}x{img.height})")
    for st in units:
        xs, ys = [], []
        for n in st.nodes.values():
            for pt in _coords(n.polygon):
                xs.append(pt[0]); ys.append(pt[1])
        if not xs:
            continue
        pad = 40
        bbox = (max(0, int(min(xs) - pad)), max(0, int(min(ys) - pad)),
                min(img.width, int(max(xs) + pad)), min(img.height, int(max(ys) + pad)))
        crop = img.crop(bbox)
        crop.save(OUT / f"{st.plan_id}.png")
        full = (0.0, 0.0, float(crop.width), float(crop.height))
        spa2 = V.predict_missing(spa_model, crop, full, SPA_C, 768, 0.25, DEV)
        str2 = V.predict_missing(str_model, crop, full, STR_C, 1024, 0.25, DEV)
        ov = crop.copy()
        d = ImageDraw.Draw(ov, "RGBA")
        for p in spa2:
            f = p["segmentation"][0]
            d.polygon(list(zip(f[0::2], f[1::2])), outline=(0, 170, 0, 255), width=3)
        for p in str2:
            f = p["segmentation"][0]
            d.polygon(list(zip(f[0::2], f[1::2])), outline=(0, 90, 255, 255), width=2)
        ov.save(OUT / f"{st.plan_id}_overlay.png")
        print(f"   {st.plan_id}: 분할 방 {len(st.nodes)} → 세대크롭 재검출 SPA {len(spa2)} / STR {len(str2)}"
              f"  크롭 {crop.width}x{crop.height}")

print(f"\n저장: {OUT}  (세대 크롭 + _overlay.png)")
