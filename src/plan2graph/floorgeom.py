"""위상(topology) → 좌표 도면(geometry) — 규칙기반 1세대 배치기.

위상 그래프(방=노드·인접=엣지)에는 좌표가 없다. 이 모듈은 방을 **사각형으로 타일링**해
실제 평면도 모양을 만든다(squarified treemap). 방 크기는 유형별 표준 면적비로 정하고,
배치 순서는 현관/거실에서 BFS로 잡아 인접한 방이 가까이 놓이게 한다.
렌더 시 위상 엣지로 연결된 방들이 실제로 경계를 공유하면 **문(door)**, 외부(exterior)와
이어지면 **창/현관**을 외벽에 그린다.

⚠️ 1세대(rectangular dissection) — 면적비·인접을 근사한다. 좌표 회귀/diffusion/RL은 로드맵(§4).
"""
from __future__ import annotations

import sys
from pathlib import Path

import networkx as nx

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph.topology import EXTERIOR  # noqa: E402

# 유형별 표준 면적비(상대 가중) — 한국 공동주택 관행 근사. 실측 scale 확보 전 비율로만 사용.
AREA_WEIGHT = {
    "공간_거실": 24, "공간_침실": 14, "공간_주방": 11, "공간_화장실": 5,
    "공간_현관": 4, "공간_발코니": 7, "공간_드레스룸": 4, "공간_다목적공간": 7,
    "공간_실외기실": 2, "공간_엘리베이터홀": 5, "공간_계단실": 6,
    "공간_엘리베이터": 4, "공간_기타": 5,
}
_DEFAULT_W = 7.0

HIER_COLOR = {"public": "#E8453C", "private": "#4C9BE8", "service": "#2DBE60", None: "#999"}

_EPS = 1e-6


def _type(d):
    t = d.get("type")
    if not t:
        return None
    return t if str(t).startswith("공간_") else "공간_" + str(t)


def _weight(t):
    return AREA_WEIGHT.get(t, _DEFAULT_W)


def _bfs_order(G):
    """현관→거실 우선 BFS 순서(인접 방이 treemap에서 가까이 놓이도록). 외부 제외."""
    rooms = [n for n in G.nodes if n != EXTERIOR]
    if not rooms:
        return []

    def _rank(n):
        t = _type(G.nodes[n])
        return 0 if t == config.ENTRANCE_CLASS else (1 if t == "공간_거실" else 2)

    start = min(rooms, key=_rank)
    order, seen = [], set()
    queue = [start]
    while queue:
        n = queue.pop(0)
        if n in seen or n == EXTERIOR:
            continue
        seen.add(n)
        order.append(n)
        nbrs = sorted((m for m in G.neighbors(n) if m != EXTERIOR and m not in seen),
                      key=lambda m: _weight(_type(G.nodes[m])), reverse=True)
        queue.extend(nbrs)
    order.extend(n for n in rooms if n not in seen)  # 비연결 방도 포함
    return order


# ── squarified treemap (Bruls et al.) — 입력 순서대로 사각형 반환 ──────────────
def _layout(sizes, x, y, dx, dy):
    if dx >= dy:
        w = sum(sizes) / dy
        out, cy = [], y
        for s in sizes:
            out.append((x, cy, w, s / w)); cy += s / w
        return out
    h = sum(sizes) / dx
    out, cx = [], x
    for s in sizes:
        out.append((cx, y, s / h, h)); cx += s / h
    return out


def _leftover(sizes, x, y, dx, dy):
    if dx >= dy:
        w = sum(sizes) / dy
        return (x + w, y, dx - w, dy)
    h = sum(sizes) / dx
    return (x, y + h, dx, dy - h)


def _worst(sizes, x, y, dx, dy):
    return max(max(w / h, h / w) for (_, _, w, h) in _layout(sizes, x, y, dx, dy))


def _squarify(sizes, x, y, dx, dy):
    sizes = [float(s) for s in sizes]
    if not sizes:
        return []
    if len(sizes) == 1:
        return _layout(sizes, x, y, dx, dy)
    i = 1
    while (i < len(sizes)
           and _worst(sizes[:i], x, y, dx, dy) >= _worst(sizes[:i + 1], x, y, dx, dy)):
        i += 1
    cur, rest = sizes[:i], sizes[i:]
    lx, ly, ldx, ldy = _leftover(cur, x, y, dx, dy)
    return _layout(cur, x, y, dx, dy) + _squarify(rest, lx, ly, ldx, ldy)


def layout_rooms(G, width=12.0, height=9.0):
    """위상 그래프 → {node: (x, y, w, h)} 사각형 배치(좌하단 원점)."""
    order = _bfs_order(G)
    if not order:
        return {}
    weights = [_weight(_type(G.nodes[n])) for n in order]
    total = sum(weights)
    sizes = [w / total * (width * height) for w in weights]
    rects = _squarify(sizes, 0.0, 0.0, width, height)
    return dict(zip(order, rects))


def _shared_edge(r1, r2):
    """두 사각형이 공유하는 벽 구간 → ('v'|'h', 좌표, lo, hi) 또는 None."""
    x1, y1, w1, h1 = r1
    x2, y2, w2, h2 = r2
    # 수직 벽(좌우로 인접): x 경계 일치 + y 겹침
    for xa, xb in ((x1 + w1, x2), (x2 + w2, x1)):
        if abs(xa - xb) < 1e-3:
            lo, hi = max(y1, y2), min(y1 + h1, y2 + h2)
            if hi - lo > 0.3:
                return ("v", xa, lo, hi)
    # 수평 벽(상하로 인접): y 경계 일치 + x 겹침
    for ya, yb in ((y1 + h1, y2), (y2 + h2, y1)):
        if abs(ya - yb) < 1e-3:
            lo, hi = max(x1, x2), min(x1 + w1, x2 + w2)
            if hi - lo > 0.3:
                return ("h", ya, lo, hi)
    return None


def render_floorplan_fig(G, title: str = "", width=12.0, height=9.0):
    """위상 그래프 → 좌표 평면도 Figure(방=색사각형·벽=검은테·문=흰틈·창/현관=외벽표시)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from plan2graph.review import KFONT

    rects = layout_rooms(G, width, height)
    fig, ax = plt.subplots(figsize=(10, 7.5))
    fig.patch.set_facecolor("white")
    if not rects:
        ax.text(0.5, 0.5, "(방 없음)", ha="center", va="center")
        ax.axis("off")
        return fig

    # 방 + 벽 + 라벨
    for n, (x, y, w, h) in rects.items():
        d = G.nodes[n]
        hc = HIER_COLOR.get(d.get("hierarchy"), "#999")
        ax.add_patch(Rectangle((x, y), w, h, facecolor=hc, alpha=0.30,
                               edgecolor="#222", linewidth=2.5, zorder=1))
        name = str(_type(d) or n).replace("공간_", "")
        ax.text(x + w / 2, y + h / 2, name, ha="center", va="center",
                fontsize=max(8, min(13, 5 + w + h)), fontfamily=KFONT,
                fontweight="bold", color="#222", zorder=3)

    # 문 — 위상 엣지로 연결 & 사각형이 실제 경계 공유
    for u, v, _ in G.edges(data=True):
        if u == EXTERIOR or v == EXTERIOR or u not in rects or v not in rects:
            continue
        se = _shared_edge(rects[u], rects[v])
        if not se:
            continue
        orient, coord, lo, hi = se
        mid = (lo + hi) / 2
        gap = min(0.45, (hi - lo) / 3)
        if orient == "v":
            ax.plot([coord, coord], [mid - gap, mid + gap], color="white", linewidth=4, zorder=4)
        else:
            ax.plot([mid - gap, mid + gap], [coord, coord], color="white", linewidth=4, zorder=4)

    # 외부 연결(현관/창) — 외부와 이어진 방을 외벽 쪽에 표시
    for u, v in G.edges():
        room = u if v == EXTERIOR else (v if u == EXTERIOR else None)
        if room is None or room not in rects:
            continue
        x, y, w, h = rects[room]
        is_ent = _type(G.nodes[room]) == config.ENTRANCE_CLASS
        cx, cy = x + w / 2, y  # 하단 외벽 기준
        ax.plot([cx - 0.4, cx + 0.4], [cy, cy],
                color=("#E8453C" if is_ent else "#1565C0"), linewidth=5, zorder=5)
        ax.text(cx, cy - 0.25, "현관" if is_ent else "창", ha="center", va="top",
                fontsize=7, fontfamily=KFONT,
                color=("#E8453C" if is_ent else "#1565C0"), zorder=5)

    ax.add_patch(Rectangle((0, 0), width, height, fill=False, edgecolor="#111",
                           linewidth=3.5, zorder=2))  # 외벽
    ax.set_xlim(-0.6, width + 0.6)
    ax.set_ylim(-0.9, height + 0.4)
    ax.set_aspect("equal")
    ax.set_title(title, fontsize=11)
    ax.axis("off")
    return fig
