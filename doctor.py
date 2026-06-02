"""서버 preflight 점검 — 실행 전 환경/데이터/의존성을 한 번에 확인.

목적: 115 서버에서 긴 작업을 돌리기 전에 누락(torch/CUDA·Java·데이터경로·deps)을
미리 잡아 '실행 중 오류 → 로그회신 → 수정 → 재배포' 왕복을 줄인다.
사용: python doctor.py   (서버 셋업 직후, 데이터 적재 후 1회)
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys
from pathlib import Path

OK, WARN, BAD = "✅", "⚠️ ", "❌"


def _check(label, fn):
    try:
        ok, msg = fn()
    except Exception as e:  # noqa: BLE001
        ok, msg = False, f"{type(e).__name__}: {e}"
    print(f"  {OK if ok else BAD} {label}: {msg}")
    return ok


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(f"=== Plan2Graph preflight ({sys.platform}, py{sys.version_info.major}.{sys.version_info.minor}) ===")
    fails = 0

    # 1) 코어 의존성(노트북 공통)
    print("[코어 의존성]")
    for m in ("shapely", "networkx", "pandas", "matplotlib", "PIL", "owlready2",
              "rapidocr_onnxruntime", "cv2", "numpy"):
        fails += not _check(m, lambda m=m: (importlib.import_module(m) and True,
                                            "import OK"))

    # 2) GPU/딥러닝(서버)
    print("[GPU/딥러닝 — 서버]")

    def _torch():
        import torch
        return (True, f"torch {torch.__version__}, CUDA {torch.cuda.is_available()}, "
                      f"GPU {torch.cuda.device_count()}개")
    has_torch = _check("torch", _torch)
    if has_torch:
        _check("ultralytics(YOLOv8-seg)",
               lambda: (importlib.import_module("ultralytics") and True, "OK"))
        _check("torch_geometric",
               lambda: (importlib.import_module("torch_geometric") and True, "OK"))
    else:
        print("     (torch 없음 → V2V·신경망 학습 불가. 서버서 설치 필요)")

    # 3) Java(법규 추론기 HermiT)
    print("[법규 추론기]")
    _check("java", lambda: (bool(shutil.which("java")),
                            shutil.which("java") or "없음(rules_swrl 추론 불가)"))

    # 4) 데이터 경로
    print("[데이터/경로]")
    import config
    raw = config.RAW_SOURCE_ROOT
    fails += not _check("원본 RAW(PLAN2GRAPH_RAW)",
                        lambda: (raw.is_dir(), f"{raw} {'있음' if raw.is_dir() else '없음'}"))
    if raw.is_dir():
        zips = list(raw.rglob("*.zip"))
        _check("원본 ZIP", lambda: (len(zips) > 0, f"{len(zips)}개"))
    data = config.DATA_DIR
    _check("DATA_DIR 쓰기가능", lambda: (os.access(data.parent, os.W_OK)
                                     or data.exists(), str(data)))
    _check("법령 API OC", lambda: (bool(config.LAW_API_OC), config.LAW_API_OC))

    # 5) 튜닝 임계(env 오버라이드 현황)
    print("[튜닝 임계(P2G_* env)]")
    print(f"     OPEN_MIN_RATIO={config.OPEN_MIN_RATIO} MIN_ETC_AREA_PX={config.MIN_ETC_AREA_PX} "
          f"BEDROOM_MIN={config.LEGAL_BEDROOM_MIN_M2}㎡ DAYLIGHT={config.LEGAL_DAYLIGHT_RATIO}")

    print(f"\n=== 결과: {'준비 완료 ✅' if fails == 0 else f'필수 {fails}건 누락 ❌ — 위 항목 해결 후 재실행'} ===")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    ROOT = Path(__file__).resolve().parent
    sys.path.insert(0, str(ROOT))
    sys.exit(main())
