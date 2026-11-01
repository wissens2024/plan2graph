"""Figure 3 최종 몽타주(로컬) — 고른 N장 geomgraph JSON → 일관 렌더 + 하단 범례.

개별 후보(fig_korplan_clean.py)와 *완전히 같은* 파스텔/무라벨 스타일로 다시 그려
화질·선두께·색이 100% 일치(사용자: 편집하면 화질 안 맞음 → 한 렌더러로 통일).

사용(로컬):
  python scripts/fig_montage_local.py --dir "C:/Users/juchu/Desktop/KorPlan_figset" \
    --picks kor_03,kor_07,kor_11,kor_19,kor_22,kor_30 --cols 3 \
    --out "C:/Users/juchu/Desktop/KorPlan_Figure3.png"
picks=쉼표목록(.png 없는 베이스명 또는 번호 03 등). graphs/<name>.json 에서 재렌더.
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "src"))

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Polygon as MPoly  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from plan2graph import cadrender as CR  # noqa: E402

# fig_korplan_clean 과 동일 팔레트 — 한 곳에서만 정의하려 import 시도, 실패 시 내장 사본.
try:
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from fig_korplan_clean import (GROUP_COLOR, GROUP_KO, ROLE_GROUP, role_color,  # noqa: E402
                                   render_plan_png)
    _HAVE = True
except Exception:  # noqa: BLE001  (서버 빌드 의존성 없을 때 내장 사본 사용)
    _HAVE = False
    ROLE_GROUP = {
        "거실": "living", "주방": "kitchen", "안방": "master", "침실": "bedroom",
        "화장실": "bath", "욕실": "bath", "전용화장실": "bath", "전용욕실": "bath",
        "공용욕실": "bath", "파우더룸": "powder", "현관": "entrance", "복도": "corridor",
        "전실": "vestibule", "발코니": "balcony", "베란다": "balcony", "드레스룸": "dress",
        "실외기실": "utility", "다목적공간": "multi", "다용도실": "multi", "팬트리": "multi",
        "알파룸": "alpha", "창고": "storage",
    }
    GROUP_COLOR = {
        "living": "#FBE2CE", "kitchen": "#FCF2C9", "master": "#C9DDF1", "bedroom": "#DFDAEF",
        "bath": "#C9E8DC", "powder": "#E6F0E0", "entrance": "#EADCC4", "corridor": "#EAEAEA",
        "vestibule": "#ECE0EC", "balcony": "#D9EFCE", "dress": "#F3D9E8", "utility": "#DEE5EC",
        "multi": "#E5EFD9", "alpha": "#F0E7D4", "storage": "#E8E4DA", "other": "#EDEDED",
    }
    GROUP_KO = {
        "living": "거실 Living room", "kitchen": "주방 Kitchen", "master": "안방 Master bedroom",
        "bedroom": "침실 Bedroom", "bath": "욕실 Bathroom", "powder": "파우더룸 Powder room",
        "entrance": "현관 Entrance", "corridor": "복도 Corridor", "vestibule": "전실 Vestibule",
        "balcony": "발코니 Balcony", "dress": "드레스룸 Dressing room", "utility": "실외기실 Utility",
        "multi": "다용도실 Multipurpose", "alpha": "알파룸 Alpha room", "storage": "창고 Storage",
        "other": "기타 Other",
    }

    def role_color(role):
        return GROUP_COLOR[ROLE_GROUP.get(role, "other")]

    def render_plan_png(geom, *, dpi=220, doors=False, windows=False,
                        ext_lw=3.4, int_lw=1.3, target_h=3.0):
        allx, ally = [], []
        for r in geom.rooms:
            for (x, y) in r.polygon:
                allx.append(x); ally.append(y)
        x0, x1, y0, y1 = min(allx), max(allx), min(ally), max(ally)
        W, H = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
        fig, ax = plt.subplots(figsize=(target_h * (W / H), target_h))
        ax.set_aspect("equal"); ax.invert_yaxis(); ax.axis("off")
        for r in geom.rooms:
            if len(r.polygon) >= 3:
                ax.add_patch(MPoly(r.polygon, closed=True, facecolor=role_color(r.role),
                                   edgecolor="none", zorder=1))
        for w in geom.walls:
            (a1, b1), (a2, b2) = w.seg
            ax.plot([a1, a2], [b1, b2], color="#161616",
                    lw=ext_lw if w.type == "exterior" else int_lw, zorder=3,
                    solid_capstyle="round")
        if windows:
            for win in geom.windows:
                x, y = win.pos; w = max(win.width_px, 12)
                ax.plot([x - w / 2, x + w / 2], [y - 2, y - 2], color="#5b9bd5", lw=1.1, zorder=4)
                ax.plot([x - w / 2, x + w / 2], [y + 2, y + 2], color="#5b9bd5", lw=1.1, zorder=4)
        if doors:
            for d in geom.doors:
                x, y = d.pos; w = max(d.width_px, 12)
                ax.plot([x - w / 2, x + w / 2], [y, y], color="#e8a64d", lw=2.2, zorder=4)
        m = 0.03 * max(W, H)
        ax.set_xlim(x0 - m, x1 + m); ax.set_ylim(y1 + m, y0 - m)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", pad_inches=0.04,
                    facecolor="white")
        plt.close(fig)
        return buf.getvalue()


def _font(size):
    for p in ("C:/Windows/Fonts/malgun.ttf", os.path.join(ROOT, "fonts", "NanumGothic.ttf"),
              "/home/ju/plan2graph/fonts/NanumGothic.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _resolve(dir_, pick):
    """pick = 베이스명 / 번호(03) / 파일명 → graphs/*.json 경로."""
    gdir = os.path.join(dir_, "graphs")
    cands = glob.glob(os.path.join(gdir, "*.json"))
    pick = pick.strip()
    if pick.endswith(".png"):
        pick = pick[:-4]
    for c in cands:
        base = os.path.basename(c)[:-5]
        if base == pick or base.endswith(pick) or f"kor_{pick}" in base:
            return c
    # 번호만 준 경우 kor_NN_ 접두 매칭
    for c in cands:
        if os.path.basename(c).startswith(f"kor_{pick}_") or os.path.basename(c).startswith(f"kor_{int(pick):02d}_"):
            return c
    raise FileNotFoundError(f"pick '{pick}' 에 맞는 graphs/*.json 없음")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="figset 폴더(내부 graphs/ 보유)")
    ap.add_argument("--picks", required=True, help="쉼표목록(베이스명/번호)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cols", type=int, default=3)
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--cell", type=int, default=900, help="셀 한 변 px(정사각 캔버스)")
    ap.add_argument("--doors", action="store_true")
    ap.add_argument("--windows", action="store_true")
    ap.add_argument("--no-legend", action="store_true")
    args = ap.parse_args()

    picks = [p for p in args.picks.split(",") if p.strip()]
    used_groups = []
    imgs = []
    for p in picks:
        gp = _resolve(args.dir, p)
        g = json.load(open(gp, encoding="utf-8"))
        for r in (g.get("rooms") or {}).values():
            grp = ROLE_GROUP.get(r.get("role") or r.get("base"), "other")
            if grp not in used_groups:
                used_groups.append(grp)
        geom = CR.autocorrect(CR.from_geomgraph(g))
        png = render_plan_png(geom, dpi=args.dpi, doors=args.doors, windows=args.windows,
                              target_h=4.0)
        imgs.append(Image.open(io.BytesIO(png)).convert("RGB"))
        print(f"  rendered {os.path.basename(gp)[:-5]} rooms={len(g.get('rooms') or {})}", flush=True)

    cols = args.cols
    rows = math.ceil(len(imgs) / cols)
    cell = args.cell
    gap = cell // 18
    gridW = cols * cell + (cols + 1) * gap
    gridH = rows * cell + (rows + 1) * gap

    # 범례 영역 높이
    leg_groups = [k for k in ["living", "kitchen", "master", "bedroom", "bath", "entrance",
                              "corridor", "balcony", "dress", "utility", "multi", "vestibule",
                              "alpha", "storage", "powder"] if k in used_groups]
    leg_cols = min(4, max(1, len(leg_groups)))
    leg_rows = math.ceil(len(leg_groups) / leg_cols) if not args.no_legend else 0
    row_h = int(cell * 0.085)
    legH = (leg_rows * row_h + 2 * gap) if leg_rows else 0

    canvas = Image.new("RGB", (gridW, gridH + legH), "white")
    for k, im in enumerate(imgs):
        c, r = k % cols, k // cols
        im = im.copy()
        im.thumbnail((cell, cell))
        ox = gap + c * (cell + gap) + (cell - im.width) // 2
        oy = gap + r * (cell + gap) + (cell - im.height) // 2
        canvas.paste(im, (ox, oy))

    if leg_rows:
        d = ImageDraw.Draw(canvas)
        fnt = _font(int(row_h * 0.46))
        sw = int(row_h * 0.55)
        colw = gridW // leg_cols
        y0 = gridH + gap
        for i, gk in enumerate(leg_groups):
            cx = gap + (i % leg_cols) * colw
            cy = y0 + (i // leg_cols) * row_h
            col = GROUP_COLOR[gk]
            rgb = tuple(int(col[j:j + 2], 16) for j in (1, 3, 5))
            d.rectangle([cx, cy + sw // 4, cx + sw, cy + sw // 4 + sw * 3 // 4],
                        fill=rgb, outline="#161616", width=max(2, sw // 22))
            d.text((cx + sw + sw // 3, cy + sw // 3), GROUP_KO[gk], fill="#111", font=fnt)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    canvas.save(args.out)
    print(f"→ {args.out}  ({len(imgs)} plans, {cols}x{rows} + legend)", flush=True)


if __name__ == "__main__":
    main()
