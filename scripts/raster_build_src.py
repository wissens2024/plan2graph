"""P0 한 컷 — 원본 SPA 주석(dr.rooms 폴리곤)에서 직접 raster 생성.

geomgraph(recover_corridors·역할병합을 거친 *가공물*) 대신, 그래프 추상화 *전*의
raw SPA 폴리곤을 그대로 렌더한다. 기존 raster_build.render를 그대로 재사용 —
방 소스만 geomgraph → 원본주석으로 교체(apples-to-apples 비교).

목적: "지난 raster 결함이 데이터(주석)발인가, 그래프단계발인가?"를 눈으로 가린다.
  - 원본 렌더가 더 깨끗(복도 살아있음) → 그래프단계가 범인 → P0 진행 정당
  - 원본도 똑같이 흡수돼 있음 → 주석 자체 한계 → 다른 처방 필요

출력: data/raster_kr_src.npy (N,H,W,3) + docs/runs/raster_src_sample.png
비교군: docs/runs/raster_gt_sample.png (geomgraph 렌더, 기존)

실행(서버 115, raw zip 보유):
  PYTHONPATH=src python scripts/raster_build_src.py --house APT --size 64 --limit 2000
"""
import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "src"), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_corrected_auto as B   # noqa: E402  (_iter_plans, _states_from_dr 재사용)
from raster_build import render    # noqa: E402  (동일 렌더 — 소스만 교체)


def state_to_g(st):
    """State(원본 raw 폴리곤 보유) → render가 먹는 {"rooms":[...]} 형태.
    Node.polygon은 shapely Polygon → 외곽 좌표열로 변환."""
    rooms = []
    for n in st.nodes.values():
        poly = n.polygon
        if poly is None:
            continue
        try:
            coords = list(poly.exterior.coords)
        except Exception:
            continue
        if len(coords) < 3:
            continue
        rooms.append({
            "polygon": [[float(x), float(y)] for x, y in coords],
            "role": n.base or "",
        })
    return {"rooms": rooms} if rooms else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--house", default="APT")
    ap.add_argument("--size", type=int, default=64)
    ap.add_argument("--limit", type=int, default=2000)
    ap.add_argument("--out", default="data/raster_kr_src.npy")
    args = ap.parse_args()

    imgs = []
    n_plans = 0
    for plan_id, house, dr, _prov in B._iter_plans("aihub", None, args.house):
        n_plans += 1
        try:
            states = list(B._states_from_dr(dr, plan_id, house))
        except Exception as e:  # noqa: BLE001
            print(f"  [skip] {plan_id}: {e}")
            continue
        for st in states:
            g = state_to_g(st)
            if g is None:
                continue
            im = render(g, args.size)
            if im is not None:
                imgs.append(im)
        if len(imgs) >= args.limit:
            break

    arr = np.stack(imgs[:args.limit])
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    np.save(args.out, arr)
    print(f"saved {args.out}  shape {arr.shape}  (plans scanned={n_plans})")

    # 미리보기 그리드 (raster_build 동일 양식)
    from PIL import Image as I
    n, cols, cell = 64, 8, args.size
    grid = I.new("RGB", (cols * cell, (n // cols) * cell), (255, 255, 255))
    for k in range(min(n, len(arr))):
        grid.paste(I.fromarray(arr[k]), ((k % cols) * cell, (k // cols) * cell))
    grid.save("docs/runs/raster_src_sample.png")
    print("preview: docs/runs/raster_src_sample.png  (비교: docs/runs/raster_gt_sample.png)")


if __name__ == "__main__":
    main()
