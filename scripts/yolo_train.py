"""V2V YOLO-seg 학습 — ultralytics Python API(CLI 인자 모호성 제거).
사용: python scripts/yolo_train.py <spa|str> <epochs> <imgsz> <batch> [model]
  workers=0(detached multiprocessing 회피), amp=False, device=0(=CUDA_VISIBLE_DEVICES 지정 GPU).
"""
import sys
from ultralytics import YOLO

label = (sys.argv[1] if len(sys.argv) > 1 else "spa").lower()
epochs = int(sys.argv[2]) if len(sys.argv) > 2 else 5
imgsz = int(sys.argv[3]) if len(sys.argv) > 3 else 768
batch = int(sys.argv[4]) if len(sys.argv) > 4 else 4
_dev = sys.argv[5] if len(sys.argv) > 5 else "0"   # "0"/"1"=단일, "0,1"=DDP 2-GPU
device = _dev if "," in str(_dev) else int(_dev)
workers = int(sys.argv[6]) if len(sys.argv) > 6 else 0  # 0=안정(미배치), >0=병렬로딩 가속(검증됨)
model = sys.argv[7] if len(sys.argv) > 7 else "yolov8n-seg.pt"

import os as _os  # noqa: E402
_tag = f"{label}_{_os.path.splitext(_os.path.basename(model))[0]}_{imgsz}_e{epochs}"
print(f"[yolo_train] {_tag} batch={batch} device={device} workers={workers}", flush=True)
m = YOLO(model)
m.train(data=f"data/v2v/coco_{label}/data.yaml", epochs=epochs, imgsz=imgsz,
        batch=batch, workers=workers, amp=False, device=device, project="v2v_runs",
        name=_tag, exist_ok=True, plots=False, verbose=True)
print("[yolo_train] DONE", flush=True)
