"""Figure 3 (한국 복원) 후보 렌더 — 라벨 없음 · 역할별 옅은 파스텔 · 범례(legend) 별도.

요구(2026-06-29): 도면 안 한글/영문 라벨 제거(작아서 안 보임·논문 부적합).
색=역할(일관) → 하단 범례로 식별. 색은 옅게(soft pastel). RPLAN Figure2 스타일과 통일.

파이프라인: AI-Hub 소스 → _states_from_dr → geomgraph.build → from_geomgraph → autocorrect
            → (라벨 없는 파스텔 렌더) → 개별 PNG + 번호 콘택트시트 + 범례.

사용(서버 115):
  PYTHONPATH=src:scripts python scripts/fig_korplan_clean.py \
    --house APT --scan-limit 600 --want 60 --out /tmp/figset_kor --dpi 220
옵션: --doors --windows (서브틀 마크 포함), --min-rooms/--max-rooms, --convex-min
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Polygon as MPoly  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

import build_corrected_auto as B  # noqa: E402
from plan2graph import geomgraph as GG, cadrender as CR  # noqa: E402
import diag_placement as DP  # noqa: E402
try:
    from survey_outline import footprint_metrics  # noqa: E402  (외곽 품질)
except Exception:  # noqa: BLE001
    footprint_metrics = None

# ── 역할 → (범례 그룹, 옅은 파스텔 색) ──────────────────────────────────────
# 색은 명도 높고 채도 낮게(연하게). 한 역할군 = 한 색(일관) → 범례로 식별.
ROLE_GROUP = {
    "거실": "living", "주방": "kitchen",
    "안방": "master", "침실": "bedroom",
    "화장실": "bath", "욕실": "bath", "전용화장실": "bath", "전용욕실": "bath",
    "공용욕실": "bath", "파우더룸": "powder",
    "현관": "entrance", "복도": "corridor", "전실": "vestibule",
    "발코니": "balcony", "베란다": "balcony",
    "드레스룸": "dress", "실외기실": "utility",
    "다목적공간": "multi", "다용도실": "multi", "팬트리": "multi",
    "알파룸": "alpha", "창고": "storage",
}
GROUP_COLOR = {
    "living":   "#FBE2CE",  # 거실 — 옅은 살구
    "kitchen":  "#FCF2C9",  # 주방 — 옅은 노랑
    "master":   "#C9DDF1",  # 안방 — 옅은 파랑
    "bedroom":  "#DFDAEF",  # 침실 — 옅은 라벤더
    "bath":     "#C9E8DC",  # 욕실/화장실 — 옅은 민트
    "powder":   "#E6F0E0",  # 파우더룸 — 옅은 연두
    "entrance": "#EADCC4",  # 현관 — 옅은 베이지
    "corridor": "#EAEAEA",  # 복도 — 옅은 회색
    "vestibule": "#ECE0EC",  # 전실 — 옅은 모브
    "balcony":  "#D9EFCE",  # 발코니 — 옅은 초록
    "dress":    "#F3D9E8",  # 드레스룸 — 옅은 분홍
    "utility":  "#DEE5EC",  # 실외기실 — 옅은 청회색
    "multi":    "#E5EFD9",  # 다용도실 — 옅은 연두회색
    "alpha":    "#F0E7D4",  # 알파룸 — 옅은 크림
    "storage":  "#E8E4DA",  # 창고 — 옅은 베이지회색
    "other":    "#EDEDED",  # 기타 — 회색
}
GROUP_KO = {
    "living": "거실 Living room", "kitchen": "주방 Kitchen",
    "master": "안방 Master bedroom", "bedroom": "침실 Bedroom",
    "bath": "욕실 Bathroom", "powder": "파우더룸 Powder room",
    "entrance": "현관 Entrance", "corridor": "복도 Corridor",
    "vestibule": "전실 Vestibule", "balcony": "발코니 Balcony",
    "dress": "드레스룸 Dressing room", "utility": "실외기실 Utility",
    "multi": "다용도실 Multipurpose", "alpha": "알파룸 Alpha room",
    "storage": "창고 Storage", "other": "기타 Other",
}
_UNMAPPED = set()


def role_color(role):
    g = ROLE_GROUP.get(role, "other")
    if role not in ROLE_GROUP and role:
        _UNMAPPED.add(role)
    return GROUP_COLOR[g]


# ── 라벨 없는 파스텔 렌더 ────────────────────────────────────────────────────
def render_plan_png(geom, *, dpi=220, doors=False, windows=False,
                    ext_lw=3.4, int_lw=1.3, target_h=3.0) -> bytes:
    allx, ally = [], []
    for r in geom.rooms:
        for (x, y) in r.polygon:
            allx.append(x); ally.append(y)
    if not allx:
        raise ValueError("no geometry")
    x0, x1, y0, y1 = min(allx), max(allx), min(ally), max(ally)
    W, H = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    fig_w = target_h * (W / H)
    fig, ax = plt.subplots(figsize=(fig_w, target_h))
    ax.set_aspect("equal"); ax.invert_yaxis(); ax.axis("off")

    for r in geom.rooms:                       # 방 채움(옅은 파스텔, 역할색)
        if len(r.polygon) >= 3:
            ax.add_patch(MPoly(r.polygon, closed=True, facecolor=role_color(r.role),
                               edgecolor="none", zorder=1))
    for w in geom.walls:                        # 벽: 외벽 두껍게/내벽 얇게
        (ax1, ay1), (ax2, ay2) = w.seg
        ax.plot([ax1, ax2], [ay1, ay2], color="#161616",
                lw=ext_lw if w.type == "exterior" else int_lw, zorder=3,
                solid_capstyle="round")
    if windows:                                 # 창: 옅은 파랑 이중선
        for win in geom.windows:
            x, y = win.pos; w = max(win.width_px, 12)
            ax.plot([x - w / 2, x + w / 2], [y - 2, y - 2], color="#5b9bd5", lw=1.1, zorder=4)
            ax.plot([x - w / 2, x + w / 2], [y + 2, y + 2], color="#5b9bd5", lw=1.1, zorder=4)
    if doors:                                   # 문: 옅은 주황 개구부 마크
        for d in geom.doors:
            x, y = d.pos; w = max(d.width_px, 12)
            ax.plot([x - w / 2, x + w / 2], [y, y], color="#e8a64d", lw=2.2, zorder=4,
                    solid_capstyle="butt")

    m = 0.03 * max(W, H)
    ax.set_xlim(x0 - m, x1 + m); ax.set_ylim(y1 + m, y0 - m)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", pad_inches=0.04,
                facecolor="white")
    plt.close(fig)
    return buf.getvalue()


# ── 품질 필터 ────────────────────────────────────────────────────────────────
def _min_unique_verts(g):
    mn = 99
    for r in g["rooms"].values():
        pts = [tuple(p) for p in (r.get("polygon") or [])]
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        if pts:
            mn = min(mn, len(set(pts)))
    return mn


def _has_diagonal(g, tol=4.0):
    for r in g["rooms"].values():
        pts = [tuple(p) for p in (r.get("polygon") or [])]
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        n = len(pts)
        for i in range(n):
            a, b = pts[i], pts[(i + 1) % n]
            if abs(b[0] - a[0]) > tol and abs(b[1] - a[1]) > tol:
                return True
    return False


def n_entrance(g):
    return sum(1 for r in g["rooms"].values()
               if (r.get("role") or r.get("base")) == "현관")


# 슬리버 가드 면제(가늘어도 정상): 발코니·복도·실외기실·전실
_THIN_OK = {"발코니", "베란다", "복도", "실외기실", "전실", "다용도실", "다목적공간"}


def _bbox_fill(g):
    """sum(방 면적)/bbox 면적 — 벽 두께 간극에 강건한 밀집도(footprint union 대체).
    한국 아파트=밀집(0.8~0.95), 산만/이형(#03형)=낮음."""
    allx, ally, area = [], [], 0.0
    for r in g["rooms"].values():
        poly = r.get("polygon") or []
        for (x, y) in poly:
            allx.append(x); ally.append(y)
        area += float(r.get("area_px") or 0.0)
    if not allx:
        return 0.0
    bw = (max(allx) - min(allx)) * (max(ally) - min(ally))
    return area / bw if bw > 0 else 0.0


def _max_sliver(g):
    """비-thin 방 중 최대 종횡비(슬리버=가는 방 검출)."""
    mx = 0.0
    for r in g["rooms"].values():
        role = r.get("role") or r.get("base")
        if role in _THIN_OK:
            continue
        ar = r.get("aspect_ratio")
        if ar:
            mx = max(mx, float(ar))
    return mx


def quality(g, args, rej):
    """통과 시 (score, metrics) 반환, 탈락 시 None. rej=Counter(탈락 사유 집계)."""
    rooms = g.get("rooms") or {}
    nr = len(rooms)
    if not (args.min_rooms <= nr <= args.max_rooms):
        rej["nrooms"] += 1; return None
    if n_entrance(g) != 1:
        rej["entrance"] += 1; return None
    if not (g.get("validation") or {}).get("passed"):
        rej["validation"] += 1; return None
    try:
        m = DP.metrics(g)
    except Exception:
        rej["metrics_err"] += 1; return None
    if not m:
        rej["no_metrics"] += 1; return None
    if m["selfint_rooms"] != 0:
        rej["selfint"] += 1; return None
    if m["overlap_frac"] >= args.overlap_max:
        rej["overlap"] += 1; return None
    if m["span_ratio"] >= 8:
        rej["span"] += 1; return None
    if _min_unique_verts(g) < 4:
        rej["triangle"] += 1; return None
    if _has_diagonal(g):
        rej["diagonal"] += 1; return None
    fill = _bbox_fill(g)                     # 벽-간극 강건 밀집도
    if fill < args.fill_min:
        rej["lowfill"] += 1; return None
    sliver = _max_sliver(g)
    if sliver > args.sliver_max:
        rej["sliver"] += 1; return None
    # score: 깔끔할수록 높게(겹침↓·밀집↑·슬리버↓)
    score = (1 - m["overlap_frac"]) + fill + max(0.0, 1.0 - sliver / 8.0)
    return score, m, fill, sliver


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--house", default="APT")
    ap.add_argument("--scan-limit", type=int, default=600)
    ap.add_argument("--want", type=int, default=60)
    ap.add_argument("--min-rooms", type=int, default=7)
    ap.add_argument("--max-rooms", type=int, default=13)
    ap.add_argument("--overlap-max", type=float, default=0.20)
    ap.add_argument("--fill-min", type=float, default=0.60)
    ap.add_argument("--sliver-max", type=float, default=5.0)
    ap.add_argument("--pool-mult", type=float, default=2.5,
                    help="want×배수 만큼 후보 모은 뒤 점수·방수 다양성으로 want개 선별")
    ap.add_argument("--out", default="/tmp/figset_kor")
    ap.add_argument("--dpi", type=int, default=220)
    ap.add_argument("--doors", action="store_true")
    ap.add_argument("--windows", action="store_true")
    args = ap.parse_args()

    from collections import Counter
    os.makedirs(args.out, exist_ok=True)
    gdir = os.path.join(args.out, "graphs")
    os.makedirs(gdir, exist_ok=True)
    pooldir = os.path.join(args.out, "pool")     # 수집 후보 전량(크래시 보험·재선별용)
    os.makedirs(pooldir, exist_ok=True)

    rej = Counter()
    pool = []                       # (score, plan_id, st.plan_id, g, nrooms)
    n_plans = n_units = 0
    pool_target = int(args.want * args.pool_mult)
    for plan_id, house, dr, prov in B._iter_plans("aihub", None, args.house):
        if n_plans >= args.scan_limit or len(pool) >= pool_target:
            break
        n_plans += 1
        try:
            states = list(B._states_from_dr(dr, plan_id, house))
        except Exception:
            continue
        best = None                 # 한 도면(시트)당 최고 1세대만(중복 방지)
        for st in states:
            n_units += 1
            try:
                g = GG.build(st, dr)
            except Exception:
                rej["build_err"] += 1; continue
            q = quality(g, args, rej)
            if q is None:
                continue
            score, m, fill, sliver = q
            if best is None or score > best[0]:
                best = (score, st.plan_id, g, len(g["rooms"]), m["overlap_frac"])
        if best is not None:
            pool.append((best[0], plan_id, best[1], best[2], best[3], best[4]))
            with open(os.path.join(pooldir, best[1] + ".json"), "w", encoding="utf-8") as f:
                json.dump(best[2], f, ensure_ascii=False)   # 크래시 보험(재수집 회피)
            print(f"  pool[{len(pool)}] {best[1]} rooms={best[3]} "
                  f"overlap={best[4]:.3f} score={best[0]:.2f}", flush=True)

    # 다양성 선별: 방 개수 버킷별(rec[4]=방수)로 점수 상위 라운드로빈 → want개
    from collections import defaultdict
    buckets = defaultdict(list)
    for rec in pool:
        buckets[rec[4]].append(rec)
    for b in buckets.values():
        b.sort(key=lambda r: -r[0])
    chosen = []
    keys = sorted(buckets)
    while len(chosen) < args.want and any(buckets[k] for k in keys):
        for k in keys:
            if buckets[k]:
                chosen.append(buckets[k].pop(0))
                if len(chosen) >= args.want:
                    break
    chosen.sort(key=lambda r: (r[4], -r[0]))   # 방수↑ 정렬(보기 편하게)

    thumbs = []
    for idx, (score, plan_id, upid, g, nr, ov) in enumerate(chosen):
        try:
            geom = CR.autocorrect(CR.from_geomgraph(g))
            png = render_plan_png(geom, dpi=args.dpi, doors=args.doors, windows=args.windows)
        except Exception as e:  # noqa: BLE001
            print(f"  render err {upid}: {type(e).__name__}: {e}", flush=True)
            continue
        name = f"kor_{idx:02d}_{upid}"
        with open(os.path.join(args.out, name + ".png"), "wb") as f:
            f.write(png)
        with open(os.path.join(gdir, name + ".json"), "w", encoding="utf-8") as f:
            json.dump(g, f, ensure_ascii=False)
        thumbs.append((idx, upid, Image.open(io.BytesIO(png)).convert("RGB"), nr))

    print(f"\nscanned plans={n_plans} units={n_units} pool={len(pool)} rendered={len(thumbs)}", flush=True)
    print("rejections:", dict(rej.most_common()), flush=True)
    if _UNMAPPED:
        print("UNMAPPED ROLES (→ other/gray):", sorted(_UNMAPPED), flush=True)

    _legend(args.out)
    _contact_sheet(thumbs, os.path.join(args.out, "_contact.png"))
    print(f"→ {args.out}  ({len(thumbs)} PNG + graphs/ + _contact.png + _legend.png)", flush=True)


def _font(size):
    for p in ("fonts/NanumGothic.ttf", "/home/ju/plan2graph/fonts/NanumGothic.ttf",
              "C:/Windows/Fonts/malgun.ttf"):
        try:
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _legend(out_dir, groups=None):
    if groups is None:
        groups = ["living", "kitchen", "master", "bedroom", "bath", "entrance",
                  "corridor", "balcony", "dress", "utility", "multi", "vestibule"]
    cols = 4
    rows = math.ceil(len(groups) / cols)
    cw, rh, pad, sw = 360, 56, 30, 46
    W, H = cols * cw + pad, rows * rh + pad
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    fnt = _font(26)
    for i, gkey in enumerate(groups):
        cx = pad + (i % cols) * cw
        cy = pad // 2 + (i // cols) * rh
        col = GROUP_COLOR[gkey]
        rgb = tuple(int(col[j:j + 2], 16) for j in (1, 3, 5))
        d.rectangle([cx, cy + 6, cx + sw, cy + 6 + sw * 3 // 4], fill=rgb, outline="#161616", width=2)
        d.text((cx + sw + 12, cy + 12), GROUP_KO[gkey], fill="#111", font=fnt)
    im.save(os.path.join(out_dir, "_legend.png"))


def _contact_sheet(thumbs, path, cell=300, cols=6):
    if not thumbs:
        return
    rows = math.ceil(len(thumbs) / cols)
    pad, cap = 14, 30
    cw, ch = cell + pad, cell + pad + cap
    im = Image.new("RGB", (cols * cw, rows * ch), "white")
    d = ImageDraw.Draw(im)
    fnt = _font(22)
    for k, (idx, pid, img, nr) in enumerate(thumbs):
        img = img.copy(); img.thumbnail((cell, cell))
        ox = (k % cols) * cw + (cw - img.width) // 2
        oy = (k // cols) * ch + cap + (cell - img.height) // 2
        im.paste(img, (ox, oy))
        d.text(((k % cols) * cw + 6, (k // cols) * ch + 4),
               f"#{idx:02d}  ({nr}rm)", fill="#111", font=fnt)
    im.save(path)


if __name__ == "__main__":
    main()
