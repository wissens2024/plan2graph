"""P1 (CPU 준비) — 멀티채널 raster 빌더. 원본 SPA 방(RGB) + STR 벽(채널) .

P0가 데이터로 가린 것:
  - 원본 SPA = 방 모양 깨끗(직사각형) but 방 사이 틈(=벽 자리)이 빔.
  - 그 틈은 버그가 아니라 *벽이 들어갈 자리* → 명시적 벽 채널로 모델링(ADR-0010 벽-first).

여기서 만드는 것: (N, H, W, 4) uint8
  - 채널 0~2 (RGB) = SPA 방 폴리곤을 role 색으로 채움(외곽선 없이 = 순수 의미 세그먼트)
  - 채널 3 (wall) = dr.walls(STR) 폴리곤을 같은 변환으로 채운 마스크(0/255)
방 채널과 벽 채널은 *동일 scale/offset*으로 렌더 → 픽셀 정렬 보장.

학습(DDPM)은 GPU 차례(B→A→C) 올 때. 지금은 데이터셋만 ready. [[gpu-priority-b-a-c]]

출력: data/raster_kr_mc.npy + docs/runs/raster_mc_sample.png(방+벽 합성 미리보기)

실행(서버 115):
  PYTHONPATH=src python scripts/raster_build_mc.py --house APT --size 64 --limit 2000
"""
import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "src"), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_corrected_auto as B   # noqa: E402  (_iter_plans, _states_from_dr)
from raster_build import role_color, _area   # noqa: E402  (동일 팔레트·면적정렬 재사용)


def _coords(poly):
    """shapely Polygon → 외곽 좌표 ndarray (Nx2) 또는 None."""
    if poly is None:
        return None
    try:
        cs = list(poly.exterior.coords)
    except Exception:
        return None
    if len(cs) < 3:
        return None
    return np.array([[float(x), float(y)] for x, y in cs], float)


def _transform(room_polys, size, margin):
    """방 폴리곤 bbox → (sc, off, mn, mx). render()와 동일 규약(가운데 정렬)."""
    allp = np.concatenate(room_polys, 0)
    mn, mx = allp.min(0), allp.max(0)
    span = (mx - mn).max() or 1.0
    sc = (size - 2 * margin) / span
    off = margin - mn * sc + (size - 2 * margin - (mx - mn) * sc) / 2
    return sc, off, mn, mx


def render_mc(st, dr, size=64, margin=3):
    """State(원본 방) + dr.walls(STR) → (H,W,4) uint8 [R,G,B,wall]."""
    nodes = [n for n in st.nodes.values() if n.polygon is not None]
    room_polys, room_nodes = [], []
    for n in nodes:
        c = _coords(n.polygon)
        if c is not None:
            room_polys.append(c)
            room_nodes.append(n)
    if not room_polys:
        return None

    sc, off, mn, mx = _transform(room_polys, size, margin)

    # --- 방 채널(RGB): 큰 방 먼저(작은 방이 위에 보이게), 외곽선 없음 ---
    rgb = Image.new("RGB", (size, size), (255, 255, 255))     # 흰=외부
    drw = ImageDraw.Draw(rgb)
    order = sorted(range(len(room_polys)), key=lambda i: -_area(room_polys[i]))
    for i in order:
        pts = [tuple(p) for p in (room_polys[i] * sc + off)]
        col = role_color({"role": room_nodes[i].base or ""})
        drw.polygon(pts, fill=col)                            # outline 없음 = 순수 세그먼트

    # --- 벽 채널: 이 유닛 방들에 *실제로 닿는* STR 벽만(옆 세대 벽 누수 차단) ---
    wall = Image.new("L", (size, size), 0)
    drw_w = ImageDraw.Draw(wall)
    buf = (mx - mn).max() * 0.01 + 1.0                        # 벽두께만큼 여유(인접 판정)
    try:
        unit_geom = unary_union([n.polygon for n in room_nodes]).buffer(buf)
    except Exception:
        unit_geom = None
    for w in getattr(dr, "walls", []) or []:
        if w.polygon is None:
            continue
        if unit_geom is not None and not w.polygon.intersects(unit_geom):
            continue                                          # 유닛 방에 안 닿는 벽=옆 세대 → 버림
        wc = _coords(w.polygon)
        if wc is None:
            continue
        pts = [tuple(p) for p in (wc * sc + off)]
        drw_w.polygon(pts, fill=255)

    return np.dstack([np.asarray(rgb, np.uint8), np.asarray(wall, np.uint8)])


def _composite(mc):
    """(H,W,4) → 미리보기 RGB: 방색 위에 벽을 검정으로 얹음(벽-first 룩)."""
    rgb = mc[:, :, :3].copy()
    w = mc[:, :, 3] > 127
    rgb[w] = (25, 25, 25)
    return rgb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--house", default="APT")
    ap.add_argument("--size", type=int, default=64)
    ap.add_argument("--limit", type=int, default=2000)
    ap.add_argument("--out", default="data/raster_kr_mc.npy")
    args = ap.parse_args()

    mcs = []
    n_plans = n_wallempty = 0
    for plan_id, house, dr, _prov in B._iter_plans("aihub", None, args.house):
        n_plans += 1
        try:
            states = list(B._states_from_dr(dr, plan_id, house))
        except Exception as e:  # noqa: BLE001
            print(f"  [skip] {plan_id}: {e}")
            continue
        for st in states:
            mc = render_mc(st, dr, args.size)
            if mc is None:
                continue
            if mc[:, :, 3].max() == 0:
                n_wallempty += 1            # STR 벽 없음(spa_only/예측) — 방채널만
            mcs.append(mc)
        if len(mcs) >= args.limit:
            break

    arr = np.stack(mcs[:args.limit])
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    np.save(args.out, arr)
    wall_cov = 100.0 * (1 - n_wallempty / max(len(arr), 1))
    print(f"saved {args.out}  shape {arr.shape}  (plans={n_plans}, 벽보유={wall_cov:.0f}%)")

    # 미리보기 그리드(방+벽 합성)
    n, cols, cell = 64, 8, args.size
    grid = Image.new("RGB", (cols * cell, (n // cols) * cell), (255, 255, 255))
    for k in range(min(n, len(arr))):
        grid.paste(Image.fromarray(_composite(arr[k])), ((k % cols) * cell, (k // cols) * cell))
    grid.save("docs/runs/raster_mc_sample.png")
    print("preview: docs/runs/raster_mc_sample.png  (방색+검정벽)")


if __name__ == "__main__":
    main()
