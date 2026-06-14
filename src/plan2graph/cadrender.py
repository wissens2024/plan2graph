"""도면 렌더 코어 — 생성형 도면(matplotlib SVG/PNG) + AutoCAD(DXF) + 자기교정 루프.

[[docs/RENDER.md]] 계약 구현. 위상도면(버블)은 안 만든다. 두 산출물만:
  ① render_fig  → 화면용 생성형 도면(벽·문·창·기구·실명·치수)  (의존성: matplotlib만)
  ② render_dxf  → 건축 사무소 작업도면(레이어·치수·심볼)        (의존성: ezdxf)
렌더 전 autocorrect(검사→고치기→재검사) 동일 패턴 적용([[geometry-realization-is-bottleneck]]).

T·G 공용 코어. 입력은 공통 Geometry — 좌표는 생성형 기하 AI(geom_gen)가 만들고
from_floorgeom(rooms,boxes,edges)로 Geometry화(T·G 공통). 실측 G 그래프는 from_geomgraph.
스키마는 분리 유지(ADR-0002), 렌더 출력만 공용(=도면 품질 비교). (옛 treemap from_parsed_graph 폐기)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# 역할(role) → 한글 실명. 없으면 base 사용.
ROLE_KO = {
    "거실": "거실", "주방": "주방", "현관": "현관", "침실": "침실", "안방": "안방",
    "화장실": "화장실", "욕실": "욕실", "전용화장실": "전용욕실", "전용욕실": "전용욕실",
    "드레스룸": "드레스룸", "파우더룸": "파우더룸", "발코니": "발코니", "실외기실": "실외기실",
    "다목적공간": "다용도실", "복도": "복도", "전실": "전실", "알파룸": "알파룸",
}
# 기구 클래스 → 표시 약칭(심볼 라벨)
FIX_KO = {"객체_변기": "변기", "객체_세면대": "세면", "객체_싱크대": "싱크",
          "객체_욕조": "욕조", "객체_가스레인지": "레인지"}


@dataclass
class FixtureG:
    cls: str
    bbox: tuple   # (x, y, w, h) px


@dataclass
class RoomG:
    id: int
    role: str
    polygon: list           # [(x,y), ...] px
    area_m2: float | None
    label_pt: tuple         # (x,y) px
    fixtures: list = field(default_factory=list)


@dataclass
class WallG:
    seg: tuple              # ((x1,y1),(x2,y2)) px
    type: str               # "interior" | "exterior"
    openings: list = field(default_factory=list)


@dataclass
class DoorG:
    pos: tuple              # (x,y)
    width_px: float
    hinge: tuple | None = None
    swing_deg: float | None = None
    is_entrance: bool = False
    rooms: tuple | None = None   # 잇는 두 방 id (인접실현 검사용; 없으면 None)


@dataclass
class WindowG:
    pos: tuple
    width_px: float
    orient_deg: float | None = None


@dataclass
class Geometry:
    plan_id: str
    house: str
    scale_mm_per_px: float | None
    bbox: tuple             # (x,y,w,h) px — 세대 외곽
    rooms: list = field(default_factory=list)
    walls: list = field(default_factory=list)
    doors: list = field(default_factory=list)
    windows: list = field(default_factory=list)
    issues: list = field(default_factory=list)        # 자기교정 후 잔여
    correct_log: list = field(default_factory=list)   # 라운드별 {iter, found, fixed}


# ════════════════════════════════════════════════════════════════════════════
# 어댑터 — G geomgraph(g-0.3) → Geometry
# ════════════════════════════════════════════════════════════════════════════
def _centroid(poly):
    if not poly:
        return (0.0, 0.0)
    n = len(poly)
    return (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)


def from_geomgraph(g: dict) -> Geometry:
    """G 그래프(geomgraph.build 출력) → 공통 Geometry. 필드는 g-0.3 그대로."""
    rooms = []
    for nid, r in (g.get("rooms") or {}).items():
        poly = [tuple(p) for p in (r.get("polygon") or [])]
        cx, cy = r.get("centroid") or _centroid(poly)
        fxs = []
        for f in (r.get("fixtures") or []):
            if isinstance(f, str):          # 문자열 태그(위치 없음) — 중심점에 0크기
                fxs.append(FixtureG(cls=f, bbox=(cx, cy, 0, 0)))
            elif isinstance(f, dict):
                fxs.append(FixtureG(cls=f.get("class") or f.get("cls") or "객체",
                                    bbox=tuple(f.get("bbox") or (cx, cy, 0, 0))))
        rooms.append(RoomG(id=int(nid) if str(nid).lstrip("-").isdigit() else nid,
                           role=r.get("role") or r.get("base") or "기타",
                           polygon=poly, area_m2=r.get("area_m2"),
                           label_pt=(float(cx), float(cy)), fixtures=fxs))
    walls = []
    for w in (g.get("walls") or []):
        seg = w.get("segment")
        if seg and len(seg) == 2:
            walls.append(WallG(seg=(tuple(seg[0]), tuple(seg[1])),
                               type=w.get("type") or "interior",
                               openings=list(w.get("openings") or [])))
    doors = []
    for d in (g.get("doors") or []):
        o = d.get("orientation") or {}
        cn = d.get("connects") or d.get("rooms")
        doors.append(DoorG(pos=tuple(d.get("position") or (0, 0)),
                           width_px=float(d.get("width_px") or 30.0),
                           hinge=tuple(o["hinge"]) if o.get("hinge") else None,
                           swing_deg=o.get("swing_dir_deg"),
                           is_entrance=bool(d.get("is_entrance")),
                           rooms=tuple(cn) if cn and len(cn) == 2 else None))
    windows = []
    for win in (g.get("windows") or []):
        windows.append(WindowG(pos=tuple(win.get("position") or (0, 0)),
                              width_px=float(win.get("width_px") or 30.0),
                              orient_deg=win.get("orientation_deg")))
    bbox = tuple(g.get("bbox_px") or (0, 0, 1000, 1000))
    return Geometry(plan_id=g.get("plan_id") or g.get("unit_id") or "?",
                    house=g.get("house") or "?",
                    scale_mm_per_px=g.get("scale_mm_per_px"),
                    bbox=bbox, rooms=rooms, walls=walls, doors=doors, windows=windows)


# ════════════════════════════════════════════════════════════════════════════
# 어댑터 — T geometry(treemap 박스) → Geometry  (geo모델 없음, 박스형 그대로 유지)
# ════════════════════════════════════════════════════════════════════════════
def from_floorgeom(rooms, boxes, edges, *, width_m=12.0, height_m=9.0,
                   px_per_m=100.0) -> Geometry:
    """Parsed treemap 출력 → 공통 Geometry. boxes=[[cx,cy,w,h]] 0..1, rooms=[(role,frac,nwin)],
    edges=[(i,j)] 인접. **박스형 그대로**(이상한 집 유지) — G의 실측형과 비교용."""
    W, H = width_m * px_per_m, height_m * px_per_m
    scale_mm = 1000.0 / px_per_m                      # 1m=px_per_m px → mm/px
    rms, walls, doors, centers = [], [], [], []
    for i, (b, rm) in enumerate(zip(boxes, rooms)):
        cx, cy, w, h = b
        x0, y0, x1, y1 = (cx - w / 2) * W, (cy - h / 2) * H, (cx + w / 2) * W, (cy + h / 2) * H
        poly = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        role = rm[0] if isinstance(rm, (list, tuple)) else str(rm)
        rms.append(RoomG(id=i, role=role, polygon=poly,
                         area_m2=(w * width_m) * (h * height_m),
                         label_pt=(cx * W, cy * H), fixtures=[]))
        centers.append((cx * W, cy * H))
        for a, c in [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                     ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]:
            ext = _on_border(a, W, H) and _on_border(c, W, H)
            walls.append(WallG(seg=(a, c), type="exterior" if ext else "interior"))
    for (i, j) in edges:                              # 문 = 인접 두 방 중점
        if i < len(centers) and j < len(centers):
            doors.append(DoorG(pos=((centers[i][0] + centers[j][0]) / 2,
                                    (centers[i][1] + centers[j][1]) / 2),
                               width_px=W * 0.04, rooms=(i, j)))
    return Geometry(plan_id="parsed", house="?", scale_mm_per_px=scale_mm,
                    bbox=(0, 0, W, H), rooms=rms, walls=walls, doors=doors, windows=[])


def _on_border(p, W, H, tol=1.0):
    return abs(p[0]) < tol or abs(p[0] - W) < tol or abs(p[1]) < tol or abs(p[1] - H) < tol


# ════════════════════════════════════════════════════════════════════════════
# 자기교정 — verify(검사) → fix(고치기) → 재검사 (동일 패턴, G=실측 정리)
# ════════════════════════════════════════════════════════════════════════════
def _poly_bounds(poly):
    xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _pt_in_poly(pt, poly):
    x, y = pt; inside = False; n = len(poly); j = n - 1
    for i in range(n):
        xi, yi = poly[i]; xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-9) + xi):
            inside = not inside
        j = i
    return inside


def _bbox(poly):
    xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _rect(poly):
    """폴리곤 → (x,y,w,h) bbox(px). treemap은 정확, 실측폴리곤은 근사."""
    x0, y0, x1, y1 = _bbox(poly)
    return (x0, y0, x1 - x0, y1 - y0)


def rooms_adjacent(rA, rB):
    """두 방이 벽을 공유하는가 → share_wall 구간(px) 또는 None.
    **realizer와 같은 정의**(floorgeom.share_wall, nail 4). min_len=문 폭."""
    from . import floorgeom as _fg
    if not (rA and rB and rA.polygon and rB.polygon):
        return None
    door_px = 30.0  # ≈0.3m@100ppm — floorgeom.share_wall(min_len=0.3m)와 동일 임계
    return _fg.share_wall(_rect(rA.polygon), _rect(rB.polygon),
                          min_len=door_px, coincide=0.5)


def verify(geom: Geometry) -> list[str]:
    """렌더 무결성 검사 — 문제를 사람이 읽는 문자열로. **T·G 공용 단일 자(尺).**
    인접실현(병목)·고립을 문 off-wall보다 먼저 본다(문스냅이 미실현을 화장빨로 덮지 못하게)."""
    iss = []
    if not geom.scale_mm_per_px:
        iss.append("R8 치수불가(scale 없음)")
    by_id = {r.id: r for r in geom.rooms}

    # R6 인접 미실현 — 위상엣지(문의 방쌍)인데 두 방이 벽을 안 맞댐(=문 못 냄). 병목 지표.
    realized_of = {r.id: 0 for r in geom.rooms}
    for d in geom.doors:
        if not d.rooms:
            continue
        a, b = d.rooms
        ra, rb = by_id.get(a), by_id.get(b)
        if ra is None or rb is None:
            continue
        if rooms_adjacent(ra, rb):
            realized_of[a] = realized_of.get(a, 0) + 1
            realized_of[b] = realized_of.get(b, 0) + 1
        else:
            iss.append(f"R6 인접미실현 {ra.role}-{rb.role}")

    # R7 방 고립 — 문(위상엣지)은 있는데 실현된 인접이 0개(=실제로 들어갈 문 없음)
    has_edge = set()
    for d in geom.doors:
        if d.rooms:
            has_edge.update(d.rooms)
    for r in geom.rooms:
        if r.id in has_edge and realized_of.get(r.id, 0) == 0:
            iss.append(f"R7 방고립 {r.role}")

    # R3 문 off-wall — 단, 두 방이 실제 인접일 때만(미실현은 R6가 잡음; 스냅으로 덮지 않음)
    for d in geom.doors:
        if geom.rooms and not any(_near_poly(d.pos, r.polygon, 25) for r in geom.rooms):
            adj = d.rooms and rooms_adjacent(by_id.get(d.rooms[0]), by_id.get(d.rooms[1]))
            if adj or not d.rooms:
                iss.append(f"R3 문 off-wall @ {tuple(round(v) for v in d.pos)}")

    for r in geom.rooms:                        # R4 기구 방밖 / R2 폴리곤 결손
        for f in r.fixtures:
            fc = (f.bbox[0] + f.bbox[2] / 2, f.bbox[1] + f.bbox[3] / 2)
            if r.polygon and not _pt_in_poly(fc, r.polygon):
                iss.append(f"R4 기구 방밖 {FIX_KO.get(f.cls, f.cls)}@{r.role}")
        if r.polygon and len(r.polygon) < 3:
            iss.append(f"R2 방 폴리곤 결손 {r.role}")

    # A3 긴 현관(종횡비) = 전실 의심 — 형상으로만 측정 가능
    for r in geom.rooms:
        if r.role == "현관" and r.polygon:
            x0, y0, x1, y1 = _bbox(r.polygon)
            w, h = x1 - x0, y1 - y0
            if min(w, h) > 0 and max(w, h) / min(w, h) >= 3.0:
                iss.append(f"A3 긴현관(전실의심) 종횡비{max(w, h) / min(w, h):.1f}")
    return iss


def _near_poly(pt, poly, tol):
    if not poly:
        return False
    if _pt_in_poly(pt, poly):
        return True
    # 경계 선분까지 거리
    for i in range(len(poly)):
        if _seg_dist(pt, poly[i], poly[(i + 1) % len(poly)]) <= tol:
            return True
    return False


def _seg_dist(p, a, b):
    px, py = p; ax, ay = a; bx, by = b
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def fix(geom: Geometry, issues: list[str]) -> tuple[Geometry, list[str]]:
    """G: 실측 기하 정리(treemap 교체 아님). 위반된 것만 고치고 **한 일을 기록해 반환**."""
    applied: list[str] = []
    for r in geom.rooms:
        if not r.polygon:
            continue
        x0, y0, x1, y1 = _poly_bounds(r.polygon)
        for f in r.fixtures:                    # R4 기구 방밖 → 방 안(여백)으로
            fx, fy, fw, fh = f.bbox
            if _pt_in_poly((fx + fw / 2, fy + fh / 2), r.polygon):
                continue
            mx, my = fw / 2 + 2, fh / 2 + 2
            cx = (x0 + x1) / 2 if x1 - mx < x0 + mx else min(max(fx + fw / 2, x0 + mx), x1 - mx)
            cy = (y0 + y1) / 2 if y1 - my < y0 + my else min(max(fy + fh / 2, y0 + my), y1 - my)
            f.bbox = (cx - fw / 2, cy - fh / 2, fw, fh)
            applied.append(f"기구 '{FIX_KO.get(f.cls, f.cls)}'@{ROLE_KO.get(r.role, r.role)} → 방 안으로 이동")
    by_id = {r.id: r for r in geom.rooms}
    for d in geom.doors:                        # R3 문 off-wall → 스냅(단 nail-3 분기)
        if not (geom.rooms and not any(_near_poly(d.pos, r.polygon, 25) for r in geom.rooms)):
            continue
        if d.rooms:                             # 방쌍 알면: 실제 인접일 때만 공유벽으로 스냅
            seg = rooms_adjacent(by_id.get(d.rooms[0]), by_id.get(d.rooms[1]))
            if not seg:
                continue                        # 미실현 인접 → 스냅 금지(R6로 남김; 화장빨 방지)
            orient, coord, lo, hi = seg
            mid = (lo + hi) / 2
            old = d.pos
            d.pos = (coord, mid) if orient == "v" else (mid, coord)
            applied.append(f"문 '{ROLE_KO.get(by_id[d.rooms[0]].role, '')}-"
                           f"{ROLE_KO.get(by_id[d.rooms[1]].role, '')}' → 공유벽 스냅")
        else:                                   # 방쌍 모름(G 실측 등) → 기존 최근접 벽 스냅
            old, best, bd = d.pos, d.pos, 1e9
            for r in geom.rooms:
                for i in range(len(r.polygon)):
                    a, b = r.polygon[i], r.polygon[(i + 1) % len(r.polygon)]
                    dd = _seg_dist(d.pos, a, b)
                    if dd < bd:
                        bd, best = dd, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            d.pos = best
            applied.append(f"문 @({round(old[0])},{round(old[1])}) → 벽 경계로 스냅")
    return geom, applied


def autocorrect(geom: Geometry, max_iter: int = 5) -> Geometry:
    """검사→고치기→재검사 루프. **라운드별 기록**을 geom.correct_log에, 잔여는 geom.issues에.
    무엇을 몇 바퀴 고쳤는지 GUI가 그대로 보여줄 수 있다."""
    geom.correct_log = []
    for i in range(1, max_iter + 1):
        found = verify(geom)
        if not found:                            # 더 검사할 게 없음 = 완료
            geom.correct_log.append({"iter": i, "found": [], "fixed": [], "done": True})
            break
        geom, applied = fix(geom, found)
        geom.correct_log.append({"iter": i, "found": found, "fixed": applied})
        if not applied:                          # 검사는 걸리는데 못 고침 → 중단(무한루프 방지)
            break
    geom.issues = verify(geom)
    return geom


# ════════════════════════════════════════════════════════════════════════════
# 렌더 ① — 생성형 도면 (matplotlib Figure; st.pyplot / savefig svg·png 겸용)
# ════════════════════════════════════════════════════════════════════════════
def render_fig(geom: Geometry, *, dims: bool = True, labels: bool = True,
               fixtures: bool = True, figsize=(9, 9)):
    """공통 Geometry → matplotlib Figure(진짜 도면 스타일). 화면·SVG·PNG 겸용."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon as MPoly, Arc
    _set_korean_font(plt)
    fig, ax = plt.subplots(figsize=figsize)
    ax.set_aspect("equal"); ax.invert_yaxis(); ax.axis("off")   # 이미지 좌표(y-down)

    for r in geom.rooms:                        # 방 채움(옅게) + 외곽
        if len(r.polygon) >= 3:
            ax.add_patch(MPoly(r.polygon, closed=True, facecolor="#f7f7f4",
                               edgecolor="none", zorder=1))
    for w in geom.walls:                         # 벽: 외벽 두껍게
        (x1, y1), (x2, y2) = w.seg
        ax.plot([x1, x2], [y1, y2], color="#222",
                lw=3.2 if w.type == "exterior" else 1.8, zorder=3,
                solid_capstyle="round")
    for d in geom.doors:                         # 문: 호(arc) 스윙 또는 개구부 마크
        x, y = d.pos; w = max(d.width_px, 12)
        if d.hinge and d.swing_deg is not None:
            hx, hy = d.hinge
            ax.add_patch(Arc((hx, hy), 2 * w, 2 * w, angle=0,
                             theta1=d.swing_deg - 45, theta2=d.swing_deg + 45,
                             color="#555", lw=1.0, zorder=4))
            ax.plot([hx, x], [hy, y], color="#555", lw=1.0, zorder=4)
        else:
            ax.plot([x - w / 2, x + w / 2], [y, y], color="#888", lw=1.2,
                    zorder=4)   # 단순 개구부
        if d.is_entrance:
            ax.plot(x, y, marker="v", color="#c33", ms=6, zorder=5)
    for win in geom.windows:                     # 창: 이중선 마크
        x, y = win.pos; w = max(win.width_px, 12)
        ax.plot([x - w / 2, x + w / 2], [y - 1.5, y - 1.5], color="#37c", lw=1.0, zorder=4)
        ax.plot([x - w / 2, x + w / 2], [y + 1.5, y + 1.5], color="#37c", lw=1.0, zorder=4)
    if fixtures:                                 # 기구 심볼(약칭 박스)
        for r in geom.rooms:
            for f in r.fixtures:
                fx, fy, fw, fh = f.bbox
                ax.add_patch(plt.Rectangle((fx, fy), max(fw, 8), max(fh, 8),
                             fill=False, edgecolor="#693", lw=0.8, zorder=4))
                ax.text(fx + max(fw, 8) / 2, fy + max(fh, 8) / 2, FIX_KO.get(f.cls, ""),
                        ha="center", va="center", fontsize=5, color="#473", zorder=5)
    if labels:                                   # 실명 + 면적
        for r in geom.rooms:
            nm = ROLE_KO.get(r.role, r.role)
            txt = nm + (f"\n{r.area_m2:.1f}㎡" if r.area_m2 else "")
            ax.text(r.label_pt[0], r.label_pt[1], txt, ha="center", va="center",
                    fontsize=7, color="#111", zorder=6)
    if dims and geom.scale_mm_per_px:            # 치수: 세대 외곽 가로/세로(mm)
        _draw_outer_dims(ax, geom)
    if geom.issues:                              # 자기교정 잔여 = 빨강 경고
        ax.set_title(f"[자기교정 잔여 {len(geom.issues)}건 — 보정필요]", color="#c33", fontsize=8)
    fig.tight_layout()
    return fig


def _draw_outer_dims(ax, geom):
    x, y, w, h = geom.bbox
    s = geom.scale_mm_per_px
    ax.annotate("", (x, y - 18), (x + w, y - 18),
                arrowprops=dict(arrowstyle="<->", color="#666", lw=0.8))
    ax.text(x + w / 2, y - 26, f"{w * s:.0f} mm", ha="center", va="bottom", fontsize=6, color="#666")
    ax.annotate("", (x - 18, y), (x - 18, y + h),
                arrowprops=dict(arrowstyle="<->", color="#666", lw=0.8))
    ax.text(x - 26, y + h / 2, f"{h * s:.0f} mm", ha="right", va="center", fontsize=6,
            color="#666", rotation=90)


def _set_korean_font(plt):
    try:
        from pathlib import Path
        import matplotlib.font_manager as fm
        for cand in (Path(__file__).resolve().parents[2] / "fonts" / "NanumGothic.ttf",):
            if cand.exists():
                fm.fontManager.addfont(str(cand))
                plt.rcParams["font.family"] = fm.FontProperties(fname=str(cand)).get_name()
                break
        plt.rcParams["axes.unicode_minus"] = False
    except Exception:  # noqa: BLE001
        pass


def render_svg(geom: Geometry) -> str:
    """Figure → SVG 문자열(다운로드·임베드)."""
    import io
    fig = render_fig(geom)
    buf = io.StringIO(); fig.savefig(buf, format="svg"); _close(fig)
    return buf.getvalue()


def render_png(geom: Geometry, dpi: int = 150) -> bytes:
    import io
    fig = render_fig(geom)
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=dpi); _close(fig)
    return buf.getvalue()


def _close(fig):
    import matplotlib.pyplot as plt
    plt.close(fig)


# ════════════════════════════════════════════════════════════════════════════
# 렌더 ② — AutoCAD 작업도면 (DXF, ezdxf) — 레이어·치수·심볼
# ════════════════════════════════════════════════════════════════════════════
LAYER_COLOR = {"WALL": 7, "DOOR": 3, "WINDOW": 5, "FIXTURE": 2, "FURNITURE": 2,
               "DIM": 8, "TEXT": 7, "GRID": 9, "ROOM": 4}


def render_dxf(geom: Geometry) -> bytes:
    """공통 Geometry → DXF(mm). 레이어 분리·치수·실명·기구. AutoCAD 편집용.
    ezdxf 필요(미설치 시 RuntimeError)."""
    try:
        import ezdxf
    except ImportError as e:
        raise RuntimeError("ezdxf 미설치 — `pip install ezdxf` (서버) 후 DXF 가능") from e
    import io
    doc = ezdxf.new("R2010", setup=True)
    msp = doc.modelspace()
    for name, col in LAYER_COLOR.items():
        if name not in doc.layers:
            doc.layers.add(name, color=col)
    s = geom.scale_mm_per_px or 1.0
    def P(x, y):
        return (x * s, -y * s)            # px→mm, y축 뒤집기(CAD 상향)
    for w in geom.walls:
        (x1, y1), (x2, y2) = w.seg
        msp.add_lwpolyline([P(x1, y1), P(x2, y2)], dxfattribs={"layer": "WALL"})
    for r in geom.rooms:
        if len(r.polygon) >= 3:
            msp.add_lwpolyline([P(*p) for p in r.polygon], close=True,
                               dxfattribs={"layer": "ROOM"})
        nm = ROLE_KO.get(r.role, r.role) + (f" {r.area_m2:.1f}㎡" if r.area_m2 else "")
        msp.add_text(nm, dxfattribs={"layer": "TEXT", "height": 200 if s == 1 else 200 * s}
                     ).set_placement(P(*r.label_pt))
        for f in r.fixtures:
            fx, fy, fw, fh = f.bbox
            msp.add_lwpolyline([P(fx, fy), P(fx + fw, fy), P(fx + fw, fy + fh),
                                P(fx, fy + fh)], close=True, dxfattribs={"layer": "FIXTURE"})
    for d in geom.doors:
        x, y = d.pos; w = max(d.width_px, 12)
        msp.add_line(P(x - w / 2, y), P(x + w / 2, y), dxfattribs={"layer": "DOOR"})
        if d.hinge and d.swing_deg is not None:
            hx, hy = d.hinge
            cx, cy = P(hx, hy)
            msp.add_arc((cx, cy), radius=w * s, start_angle=d.swing_deg - 45,
                        end_angle=d.swing_deg + 45, dxfattribs={"layer": "DOOR"})
    for win in geom.windows:
        x, y = win.pos; w = max(win.width_px, 12)
        msp.add_line(P(x - w / 2, y), P(x + w / 2, y), dxfattribs={"layer": "WINDOW"})
    x, y, ww, hh = geom.bbox                      # 외곽 치수
    try:
        msp.add_linear_dim(base=P(x, y - 40), p1=P(x, y), p2=P(x + ww, y),
                           dxfattribs={"layer": "DIM"}).render()
        msp.add_linear_dim(base=P(x - 40, y), p1=P(x, y), p2=P(x, y + hh), angle=90,
                           dxfattribs={"layer": "DIM"}).render()
    except Exception:  # noqa: BLE001
        pass
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")
