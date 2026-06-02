"""P2 v2 — Vision-to-Vector 학습셋 export (라벨 → YOLOv8-seg 포맷).

AI-Hub COCO 폴리곤 라벨이 곧 인스턴스 세그멘테이션 학습 정답. 이를 YOLO-seg 형식
(images/ + labels/*.txt + data.yaml)으로 내보내 115 서버에서 학습한다.

핵심 설계:
- **SPA(방 13클래스)·STR(문/창/벽 3클래스) 별도 데이터셋**. SPA만 있는 도면은 벽 라벨이
  없어 한 모델에 섞으면 벽이 '배경'으로 오학습 → 라벨종류별로 완전 라벨된 이미지만 사용.
- **내용 bbox 크롭 + 리사이즈**: 평면도가 큰 캔버스에 작게 그려진 경우(1.4% 등) 대비.
- 좌표는 크롭·리사이즈에 맞춰 변환 후 0~1 정규화.

GPU 불요(이미지 처리). 데이터(zip)가 있는 서버에서 전체 실행, 노트북은 --limit로 시험.
CLI: python src/plan2graph/v2v_export.py --label SPA --imgsz 1024 [--limit N]
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph import review  # noqa: E402
from plan2graph.coco import load_coco_bytes  # noqa: E402

CLASSES = {
    "SPA": list(config.SPACE_CLASSES),          # 13
    "STR": list(config.STRUCTURE_CLASSES),      # 3 (출입문·창호·벽체)
}


def _val(key: str) -> bool:
    """drawing key 해시로 train/val(10%) 결정(결정적)."""
    h = int(hashlib.md5(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return h >= 0.9


def _largest_ring(seg):
    best, ba = None, -1.0
    for ring in seg:
        if len(ring) < 6:
            continue
        pts = list(zip(ring[0::2], ring[1::2]))
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        a = (max(xs) - min(xs)) * (max(ys) - min(ys))
        if a > ba:
            best, ba = pts, a
    return best


def export(label_type: str, imgsz: int = 1024, pad: int = 40,
           limit: int | None = None, out_root: Path = None) -> dict:
    from PIL import Image
    import io
    cls_names = CLASSES[label_type]
    cls_id = {n: i for i, n in enumerate(cls_names)}
    out = out_root or (config.DATA_DIR / "v2v" / f"coco_{label_type.lower()}")
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    idx = review.build_indices(("Training", "Validation"))
    # 대상: 해당 라벨종류를 가진 FP 도면 (label_entry 키)
    targets = [(s, lab, k) for (s, lab, k) in idx.label_entry if lab == label_type]
    n_ok = n_skip = 0
    counts = {"train": 0, "val": 0}
    for (split, lab, key) in targets:
        if limit and n_ok >= limit:
            break
        lab_entry = idx.label_entry.get((split, lab, key))
        src_entry = idx.source_entry.get((split, lab, key))
        if not lab_entry or not src_entry:
            n_skip += 1
            continue
        try:
            doc = load_coco_bytes(review._read_zip(*lab_entry), source=lab_entry[1])
            img = Image.open(io.BytesIO(review._read_zip(*src_entry))).convert("L")
        except Exception:
            n_skip += 1
            continue
        ow, oh = img.size
        # 대상 클래스 인스턴스 + 내용 bbox
        insts = []
        bx0 = by0 = 1e18; bx1 = by1 = -1e18
        for a in doc.annotations:
            if a.class_name not in cls_id or not a.segmentation:
                continue
            ring = _largest_ring(a.segmentation)
            if not ring:
                continue
            xs = [p[0] for p in ring]; ys = [p[1] for p in ring]
            bx0 = min(bx0, min(xs)); by0 = min(by0, min(ys))
            bx1 = max(bx1, max(xs)); by1 = max(by1, max(ys))
            insts.append((cls_id[a.class_name], ring))
        if not insts:
            n_skip += 1
            continue
        # 크롭(내용+pad) → 리사이즈(긴변=imgsz)
        cx0 = max(0, int(bx0 - pad)); cy0 = max(0, int(by0 - pad))
        cx1 = min(ow, int(bx1 + pad)); cy1 = min(oh, int(by1 + pad))
        cw, ch = cx1 - cx0, cy1 - cy0
        if cw < 10 or ch < 10:
            n_skip += 1
            continue
        s = imgsz / max(cw, ch)
        nw, nh = max(1, int(cw * s)), max(1, int(ch * s))
        crop = img.crop((cx0, cy0, cx1, cy1)).resize((nw, nh))
        split_dir = "val" if _val(key) else "train"
        stem = f"{label_type}_{key}"
        crop.convert("RGB").save(out / "images" / split_dir / f"{stem}.png")
        # YOLO-seg 라벨: class x1 y1 ... (크롭·리사이즈·정규화)
        lines = []
        for cid, ring in insts:
            coords = []
            for (x, y) in ring:
                nx = (x - cx0) * s / nw
                ny = (y - cy0) * s / nh
                coords += [f"{min(max(nx,0),1):.5f}", f"{min(max(ny,0),1):.5f}"]
            if len(coords) >= 6:
                lines.append(str(cid) + " " + " ".join(coords))
        (out / "labels" / split_dir / f"{stem}.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
        counts[split_dir] += 1
        n_ok += 1
        if n_ok % 500 == 0:
            print(f"  ...{n_ok} 내보냄")

    # data.yaml
    yaml = [f"path: {out.as_posix()}", "train: images/train", "val: images/val",
            f"nc: {len(cls_names)}", "names:"]
    for i, n in enumerate(cls_names):
        yaml.append(f"  {i}: {n}")
    (out / "data.yaml").write_text("\n".join(yaml) + "\n", encoding="utf-8")
    return {"label": label_type, "exported": n_ok, "skipped": n_skip,
            "train": counts["train"], "val": counts["val"],
            "classes": len(cls_names), "out": str(out)}


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", choices=["SPA", "STR"], required=True)
    ap.add_argument("--imgsz", type=int, default=1024)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    print(f"V2V export: {a.label} (imgsz={a.imgsz})")
    print(json.dumps(export(a.label, a.imgsz, limit=a.limit), ensure_ascii=False, indent=2))
