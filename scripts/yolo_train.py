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
device = int(sys.argv[5]) if len(sys.argv) > 5 else 0   # 물리 GPU index(CUDA_VISIBLE_DEVICES 불신뢰)
model = sys.argv[6] if len(sys.argv) > 6 else "yolov8n-seg.pt"

print(f"[yolo_train] label={label} epochs={epochs} imgsz={imgsz} batch={batch} "
      f"device={device} model={model}", flush=True)
m = YOLO(model)
m.train(data=f"data/v2v/coco_{label}/data.yaml", epochs=epochs, imgsz=imgsz,
        batch=batch, workers=0, amp=False, device=device, project="v2v_runs",
        name=f"{label}_e{epochs}", exist_ok=True, plots=False, verbose=True)
print("[yolo_train] DONE", flush=True)
