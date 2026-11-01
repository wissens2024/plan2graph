"""KorPlan 논문 그림 v2 렌더 — 선명색(v2) 팔레트 + 영문 범례 + 도면 내 텍스트 없음.

fig_korplan_clean 과 *같은* 렌더 경로(matplotlib, 같은 선두께/여백)로 그려 화질·스타일 100% 일치.
차이는 팔레트뿐: 파스텔 → v2 선명색(사용자 확정 참조 이미지 기준).

개별 고해상도(그림당 PNG 1장) + 선택적 공용 범례 1장.

사용:
  python scripts/fig_v2_render.py --graphs <dir> --picks kor_03,kor_07 --out-dir <dir> --dpi 300
  python scripts/fig_v2_render.py --graphs <dir> --all --out-dir <dir>          # 폴더 전체
  python scripts/fig_v2_render.py --legend-only --out-dir <dir>                 # 범례만 1장
"""
from __future__ import annotations
import argparse, glob, io, json, math, os, sys

WIN_COLOR = "#2E75D6"   # 창 — 파랑 (논문 캡션: windows in blue)
DOOR_COLOR = "#E8811A"  # 문 — 주황 (논문 캡션: doors in orange)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "src"))
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Polygon as MPoly  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from plan2graph import cadrender as CR  # noqa: E402

# 역할 → 그룹 (범례 9종으로 통합; 참조 이미지 legend와 동일 구성)
ROLE_GROUP = {
    "거실": "living",
    "주방": "kitchen",
    "안방": "master",
    "침실": "bedroom",
    "화장실": "bath", "욕실": "bath", "전용화장실": "bath", "전용욕실": "bath",
    "공용욕실": "bath", "파우더룸": "bath",
    "현관": "entrance", "복도": "entrance", "전실": "entrance",
    "엘리베이터홀": "entrance", "계단실": "entrance",
    "발코니": "balcony", "베란다": "balcony", "테라스": "balcony",
    "드레스룸": "dress",
    "실외기실": "utility", "다용도실": "utility", "다목적공간": "utility",
    "창고": "utility", "팬트리": "utility", "펜트리": "utility", "세탁실": "utility",
    "알파룸": "utility", "대피공간": "utility",
}
# v2 선명색 — 사용자 확정 참조 이미지
V2_COLOR = {
    "living":   "#E8453C",  # Living room — 빨강
    "kitchen":  "#F2A900",  # Kitchen — 주황
    "bedroom":  "#6CA6E8",  # Bedroom — 하늘
    "master":   "#1F6FD6",  # Master bedroom — 진파랑
    "bath":     "#26A69A",  # Bathroom — 청록
    "balcony":  "#2DBE60",  # Balcony — 초록
    "dress":    "#A070D0",  # Dressing room — 보라
    "entrance": "#9AA0A6",  # Entrance / corridor — 회색
    "utility":  "#4C8C3A",  # Utility — 진초록
    "other":    "#BCBCBC",  # 기타 — 옅은 회색
}
GROUP_EN = {
    "living": "Living room", "kitchen": "Kitchen", "bedroom": "Bedroom",
    "master": "Master bedroom", "bath": "Bathroom", "balcony": "Balcony",
    "dress": "Dressing room", "entrance": "Entrance / corridor", "utility": "Utility",
}
LEGEND_ORDER = ["living", "kitchen", "bedroom", "master", "bath",
                "balcony", "dress", "entrance", "utility"]


ROLE_EN = {
    "거실": "Living", "주방": "Kitchen", "안방": "Master BR", "침실": "Bedroom",
    "화장실": "Bath", "욕실": "Bath", "전용화장실": "Bath", "전용욕실": "Bath",
    "공용욕실": "Bath", "파우더룸": "Powder", "현관": "Entrance", "복도": "Corridor",
    "전실": "Vestibule", "발코니": "Balcony", "베란다": "Balcony", "테라스": "Terrace",
    "드레스룸": "Dressing", "실외기실": "Utility", "다용도실": "Utility",
    "다목적공간": "Utility", "창고": "Storage", "팬트리": "Pantry", "펜트리": "Pantry",
    "세탁실": "Laundry", "알파룸": "Alpha", "대피공간": "Refuge",
    "엘리베이터홀": "Hall", "계단실": "Stair", "기타": "Etc",
}


def role_color(role):
    return V2_COLOR[ROLE_GROUP.get(role, "other")]


def _nearest_wall(walls, pos):
    """opening 위치에서 가장 가까운 벽선의 (벽 위 투영점 cx,cy · 단위방향 ux,uy · 세그먼트 길이 L).
    마크를 벽선 위에 얹고 길이를 벽 길이로 제한하기 위함(밖으로 삐져나옴 방지)."""
    px, py = pos
    best = None; bd = 1e18
    for w in walls:
        (x1, y1), (x2, y2) = w.seg
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy
        if L2 < 1e-6:
            continue
        t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
        cx, cy = x1 + t * dx, y1 + t * dy
        d = (px - cx) ** 2 + (py - cy) ** 2
        if d < bd:
            bd = d; best = (cx, cy, dx, dy, math.sqrt(L2))
    if not best:
        return (px, py, 1.0, 0.0, 10.0)
    cx, cy, dx, dy, L = best
    return (cx, cy, dx / L, dy / L, L)


def _seg_dir_len(p1, p2):
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    L = math.hypot(dx, dy)
    return (1.0, 0.0, 0.0) if L < 1e-6 else (dx / L, dy / L, L)


def _project_clamp(pt, p1, p2):
    px, py = pt; x1, y1 = p1; x2, y2 = p2
    dx, dy = x2 - x1, y2 - y1; L2 = dx * dx + dy * dy
    if L2 < 1e-9:
        return (x1, y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
    return (x1 + t * dx, y1 + t * dy)


def _nearest_wall_seg(walls, pos, wtype=None):
    best = None; bd = 1e18
    for w in walls:
        if wtype and w.type != wtype:
            continue
        q = _project_clamp(pos, w.seg[0], w.seg[1])
        d = (pos[0] - q[0]) ** 2 + (pos[1] - q[1]) ** 2
        if d < bd:
            bd = d; best = (w.seg[0], w.seg[1])
    return best


def _axis_overlap(a1, a2, b1, b2, col_tol, gap_tol):
    """두 축정렬 edge가 나란하고(수직간격<gap_tol) 겹치면 그 중앙선 세그먼트를 반환.
    벽두께로 방이 떨어져 있어도(공유벽=평행 offset) 중앙선을 잡는다."""
    ax1, ay1 = a1; ax2, ay2 = a2; bx1, by1 = b1; bx2, by2 = b2
    if abs(ay1 - ay2) < col_tol and abs(by1 - by2) < col_tol and abs(ay1 - by1) < gap_tol:
        lo = max(min(ax1, ax2), min(bx1, bx2)); hi = min(max(ax1, ax2), max(bx1, bx2))
        if hi - lo > gap_tol:
            y = (ay1 + by1) / 2.0
            return ((lo, y), (hi, y), hi - lo)
    if abs(ax1 - ax2) < col_tol and abs(bx1 - bx2) < col_tol and abs(ax1 - bx1) < gap_tol:
        lo = max(min(ay1, ay2), min(by1, by2)); hi = min(max(ay1, ay2), max(by1, by2))
        if hi - lo > gap_tol:
            x = (ax1 + bx1) / 2.0
            return ((x, lo), (x, hi), hi - lo)
    return None


def _shared_wall(polyA, polyB, span, near_pt=None):
    """두 방의 공유벽(중앙선) → (p1,p2). near_pt 주면 그 점에 가장 가까운 공유변(=문 실제위치),
    없으면 가장 긴 겹침. gap_tol은 실제 벽두께 수준으로 좁혀 안 붙은 평행벽 오매칭을 막는다."""
    def edges(poly):
        return list(zip(poly, poly[1:] + poly[:1]))
    col_tol = max(3.0, span * 0.01); gap_tol = max(14.0, span * 0.04)
    cands = []
    for a1, a2 in edges(polyA):
        for b1, b2 in edges(polyB):
            seg = _axis_overlap(a1, a2, b1, b2, col_tol, gap_tol)
            if seg:
                cands.append((seg[0], seg[1], seg[2]))
    if not cands:
        return None
    if near_pt is not None:
        def d2(c):
            q = _project_clamp(near_pt, c[0], c[1])
            return (near_pt[0] - q[0]) ** 2 + (near_pt[1] - q[1]) ** 2
        c = min(cands, key=d2)
    else:
        c = max(cands, key=lambda c: c[2])
    return (c[0], c[1])


def _place_openings(geom, span, placement):
    """정확 배치: 문=잇는 두 방의 공유벽 / 창=외벽. placement=clean이면 벽 중앙, raw면 실제위치 투영."""
    by_id = {r.id: r for r in geom.rooms}
    def mid(p1, p2):
        return ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
    OPEN_PAIRS = [{"거실", "주방"}, {"거실", "식당"}, {"주방", "식당"}]  # LDK 개방형 = 문 없음
    DOOR_ACC_TOL = span * 0.06
    doors = []
    for d in getattr(geom, "doors", []):
        rm = getattr(d, "rooms", None)
        if not (rm and len(rm) == 2):
            continue                       # 잇는 방 모르면 정확히 못 놓음 → 스킵(floating 방지)
        ra = by_id.get(rm[0]); rb = by_id.get(rm[1])
        if not (ra and rb and ra.polygon and rb.polygon):
            continue
        if {ra.role, rb.role} in OPEN_PAIRS:
            continue                       # 거실-부엌 개방형: 문 안 그림
        seg = _shared_wall(ra.polygon, rb.polygon, span, near_pt=d.pos)
        if not seg:
            continue                       # 공유벽 없으면 문 안 그림(벽에서 뜨는 문 방지)
        p1, p2 = seg
        proj = _project_clamp(d.pos, p1, p2)
        if placement == "filter" and math.hypot(d.pos[0] - proj[0], d.pos[1] - proj[1]) > DOOR_ACC_TOL:
            continue                       # 문이 공유벽에서 멂 = 부정확 → 삭제
        ux, uy, L = _seg_dir_len(p1, p2)
        wd = min(max(getattr(d, "width_px", 0) or 0, span * 0.02), span * 0.05, L * 0.8)
        pos = mid(p1, p2) if placement == "clean" else proj
        doors.append((pos, ux, uy, wd))
    wins = []
    WIN_ACC_TOL = span * 0.035    # 모델 창이 외벽에서 이 이내로 가까울 때만 '정확' → 유지
    for w in getattr(geom, "windows", []):
        # filter: 창을 새로 만들지 않고, 모델이 낸 창 중 외벽에 '실제로 가까운' 것만 남김
        seg = _nearest_wall_seg(geom.walls, w.pos, "exterior")
        if not seg:
            continue                 # 외벽 자체가 없으면(비정상) 삭제
        p1, p2 = seg
        proj = _project_clamp(w.pos, p1, p2)
        if placement == "filter":
            dist = math.hypot(w.pos[0] - proj[0], w.pos[1] - proj[1])
            if dist > WIN_ACC_TOL:
                continue             # 외벽에서 멂 = 부정확한 창 → 삭제
        ux, uy, L = _seg_dir_len(p1, p2)
        wd = min(max(getattr(w, "width_px", 0) or 0, span * 0.02), span * 0.06, L * 0.85)
        pos = mid(p1, p2) if placement == "clean" else proj   # filter/raw = 실제 위치(외벽 투영)
        wins.append((pos, ux, uy, wd))
    return doors, wins


def _is_shared_edge(p1, p2, others, span):
    """이 edge가 다른 방과 맞닿은(공유) 변인가 → 아니면 바깥 접한 '자유변'."""
    col_tol = max(3.0, span * 0.01); gap_tol = max(18.0, span * 0.06)
    for poly in others:
        es = list(zip(poly, poly[1:] + poly[:1]))
        for b1, b2 in es:
            if _axis_overlap(p1, p2, b1, b2, col_tol, gap_tol):
                return True
    return False


def _arch_windows(geom, span):
    """보수적 창 배치: 확실한 것만. 발코니·거실의 '충분히 긴' 바깥 자유변에만 창 1개.
    잘못 단 창 여럿보다 적게/없는 게 낫다(침실·주방 등 향 불확실한 실은 제외)."""
    WINROOMS = {"발코니", "베란다", "거실", "안방", "침실"}
    MIN_LEN = span * 0.08          # 확실한(충분히 긴) 바깥 자유변만 — 짧은 변엔 창 생략
    rooms = [r for r in geom.rooms if getattr(r, "polygon", None) and len(r.polygon) >= 3]
    wins = []
    for r in rooms:
        if r.role not in WINROOMS:
            continue
        poly = r.polygon
        others = [o.polygon for o in rooms if o is not r]
        edges = list(zip(poly, poly[1:] + poly[:1]))
        free = [(a, b) for (a, b) in edges if not _is_shared_edge(a, b, others, span)]
        if not free:
            continue
        a, b = max(free, key=lambda e: math.hypot(e[1][0] - e[0][0], e[1][1] - e[0][1]))
        ux, uy, L = _seg_dir_len(a, b)
        if L < MIN_LEN:
            continue
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        wins.append((mid, ux, uy, min(L * 0.5, span * 0.08)))
    return wins


def _graph_openings(geom, span):
    """repair된 그래프의 창·문을 최근접 벽에 투영+정렬해 그림(cadrender와 동일 방식).
    문 규칙: 거실-주방 개방(문X), 침실-침실/안방(방-방) 직결 문 금지(rules_arch '룸 지나 룸')."""
    by_id = {r.id: r for r in geom.rooms}
    BED = {"침실", "안방"}
    OPEN = ({"거실", "주방"}, {"거실", "식당"}, {"주방", "식당"})

    def door_ok(d):
        rm = getattr(d, "rooms", None)
        if not (rm and len(rm) == 2):
            return True
        ra, rb = by_id.get(rm[0]), by_id.get(rm[1])
        if not (ra and rb):
            return True
        roles = {ra.role, rb.role}
        if roles in OPEN:
            return False                       # 거실-주방 개방(문 없음)
        if ra.role in BED and rb.role in BED:
            return False                       # 방-방 직결 문 금지
        return True

    def conv(items, wmin, wmax):
        out = []
        for it in items:
            seg = _nearest_wall_seg(geom.walls, it.pos)
            if seg:
                ux, uy, _ = _seg_dir_len(seg[0], seg[1])
                pos = _project_clamp(it.pos, seg[0], seg[1])
            else:
                ux, uy, pos = 1.0, 0.0, it.pos
            out.append((pos, ux, uy, min(max(getattr(it, "width_px", 0) or 0, span * wmin), span * wmax)))
        return out
    wlist = conv(getattr(geom, "windows", []), 0.03, 0.09)
    dlist = conv([d for d in getattr(geom, "doors", []) if door_ok(d)], 0.02, 0.05)
    return dlist, wlist


def render_plan_png(geom, *, dpi=300, ext_lw=3.4, int_lw=1.3, target_h=4.0,
                    doors=False, windows=False, placement="raw", labels=False):
    """fig_korplan_clean.render_plan_png 과 동일 스타일(무라벨). 팔레트만 v2.
    doors/windows=True 면 벽 방향에 맞춰 문(주황 1선)·창(파랑 2선) 오버레이."""
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
    span = max(W, H)
    if placement == "graph":
        dlist, wlist = _graph_openings(geom, span)   # repair된 창·문 있는 그대로
    else:
        mode = "clean" if placement == "arch" else placement
        dlist, wlist = _place_openings(geom, span, mode)
        if placement == "arch":
            wlist = _arch_windows(geom, span)
    if windows:
        off = span * 0.005
        for (x, y), ux, uy, wd in wlist:
            nx, ny = -uy, ux
            for s in (-1, 1):
                ax.plot([x - ux * wd / 2 + nx * off * s, x + ux * wd / 2 + nx * off * s],
                        [y - uy * wd / 2 + ny * off * s, y + uy * wd / 2 + ny * off * s],
                        color=WIN_COLOR, lw=1.7, zorder=5, solid_capstyle="butt")
    if doors:
        for (x, y), ux, uy, wd in dlist:
            ax.plot([x - ux * wd / 2, x + ux * wd / 2], [y - uy * wd / 2, y + uy * wd / 2],
                    color=DOOR_COLOR, lw=2.6, zorder=6, solid_capstyle="butt")
    if labels:                                   # 영문 실명(방 중앙). 작은 방은 생략(겹침 방지)
        for r in geom.rooms:
            if len(r.polygon) < 3:
                continue
            nm = ROLE_EN.get(r.role, "")
            if not nm:
                continue
            rxs = [p[0] for p in r.polygon]; rys = [p[1] for p in r.polygon]
            rw = max(rxs) - min(rxs); rh = max(rys) - min(rys)
            if min(rw, rh) < span * 0.075:        # 라벨 안 들어가는 작은 방은 생략
                continue
            fs = 7.0 if len(nm) > 7 else 8.0
            lp = getattr(r, "label_pt", None) or (sum(rxs) / len(rxs), sum(rys) / len(rys))
            ax.text(lp[0], lp[1], nm, ha="center", va="center", fontsize=fs, color="#111",
                    zorder=7, bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.55))
    m = 0.03 * max(W, H)
    ax.set_xlim(x0 - m, x1 + m); ax.set_ylim(y1 + m, y0 - m)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", pad_inches=0.04,
                facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def _font(size):
    for p in ("C:/Windows/Fonts/malgunbd.ttf", "C:/Windows/Fonts/malgun.ttf",
              os.path.join(ROOT, "fonts", "NanumGothic.ttf")):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def legend_png(groups=None, sw=42, pad=26, cols=5, row_h=64, fsz=26, colw=340):
    groups = groups or LEGEND_ORDER
    rows = math.ceil(len(groups) / cols)
    W = cols * colw + 2 * pad
    H = rows * row_h + 2 * pad
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img); fnt = _font(fsz)
    for i, g in enumerate(groups):
        cx = pad + (i % cols) * colw
        cy = pad + (i // cols) * row_h
        rgb = tuple(int(V2_COLOR[g][j:j + 2], 16) for j in (1, 3, 5))
        d.rectangle([cx, cy, cx + sw, cy + sw], fill=rgb, outline="#161616", width=3)
        d.text((cx + sw + 14, cy + sw // 2 - fsz // 2 - 2), GROUP_EN[g], fill="#111", font=fnt)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graphs", help="그래프 JSON 폴더")
    ap.add_argument("--picks", help="쉼표목록(베이스명/번호), 미지정+--all=전체")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--doors", action="store_true")
    ap.add_argument("--windows", action="store_true")
    ap.add_argument("--placement", choices=["raw", "clean", "filter", "arch"], default="raw",
                    help="filter=모델 창 중 외벽에 정확한 것만 남김(권장) / raw=실제위치 / clean=중앙 / arch=규칙생성")
    ap.add_argument("--legend-only", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    legend_png().save(os.path.join(args.out_dir, "_legend_v2.png"))
    print(f"legend -> {os.path.join(args.out_dir, '_legend_v2.png')}", flush=True)
    if args.legend_only:
        return

    cands = sorted(glob.glob(os.path.join(args.graphs, "*.json")))
    if args.picks:
        want = [p.strip() for p in args.picks.split(",") if p.strip()]
        sel = []
        for w in want:
            hit = next((c for c in cands if os.path.basename(c)[:-5] == w
                        or os.path.basename(c)[:-5].endswith(w) or f"_{w}_" in os.path.basename(c)
                        or os.path.basename(c).startswith(f"kor_{w}_")), None)
            if hit:
                sel.append(hit)
            else:
                print(f"  [miss] pick '{w}'", flush=True)
        cands = sel
    if args.limit:
        cands = cands[:args.limit]

    for gp in cands:
        name = os.path.basename(gp)[:-5]
        try:
            g = json.load(open(gp, encoding="utf-8"))
            geom = CR.autocorrect(CR.from_geomgraph(g))
            png = render_plan_png(geom, dpi=args.dpi, doors=args.doors, windows=args.windows,
                                  placement=args.placement)
            outp = os.path.join(args.out_dir, name + ".png")
            with open(outp, "wb") as f:
                f.write(png)
            print(f"  {name}  rooms={len(g.get('rooms') or {})} -> {outp}", flush=True)
        except Exception as e:
            print(f"  [fail] {name}: {e}", flush=True)


if __name__ == "__main__":
    main()
