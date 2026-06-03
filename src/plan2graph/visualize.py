"""시각 검증 — 도면 PNG 위 방/문/창/벽 오버레이 + 위상 그래프 렌더.

1장 게이트의 핵심 도구: 기하 객체화(1-2)와 그래프 추론(1-3) 결과를 도면 위에 겹쳐
사람이 육안으로 검증한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph.geometry import Drawing, Element  # noqa: E402

# 한글 라벨 폰트 (Windows 기본 맑은 고딕) — 실제 설치된 폰트만 선택
from matplotlib import font_manager as _fm  # noqa: E402

_available = {f.name for f in _fm.fontManager.ttflist}
KFONT = "DejaVu Sans"
for _f in ("Malgun Gothic", "AppleGothic", "NanumGothic"):
    if _f in _available:
        KFONT = _f
        break
# 시스템에 한글 폰트가 없으면(예: 리눅스 서버) 번들 TTF를 등록 — 한글이 □로 깨지는 것 방지.
#   fonts/NanumGothic.ttf : github.com/google/fonts/ofl/nanumgothic (OFL) 에서 받아둘 것.
if KFONT == "DejaVu Sans":
    from pathlib import Path as _Path
    for _p in (_Path(__file__).resolve().parents[2] / "fonts" / "NanumGothic.ttf",
               _Path.home() / ".fonts" / "NanumGothic.ttf"):
        if _p.exists():
            _fm.fontManager.addfont(str(_p))
            KFONT = _fm.FontProperties(fname=str(_p)).get_name()
            break
matplotlib.rcParams["font.family"] = KFONT
matplotlib.rcParams["axes.unicode_minus"] = False

ROOM_FILL = "#4C9BE8"
DOOR_COLOR = "#E8453C"
WINDOW_COLOR = "#2DBE60"
WALL_COLOR = "#888888"
OBJECT_COLOR = "#F2A900"


def _draw_poly(ax, el: Element, *, edge, face=None, alpha=0.35, lw=1.5):
    if el.polygon is None:
        return
    geoms = el.polygon.geoms if el.polygon.geom_type == "MultiPolygon" else [el.polygon]
    for g in geoms:
        xs, ys = g.exterior.xy
        ax.add_patch(MplPolygon(
            list(zip(xs, ys)), closed=True, edgecolor=edge,
            facecolor=face if face else "none",
            alpha=alpha if face else 1.0, linewidth=lw))


def overlay_drawing(dr: Drawing, image_path: str | Path, out_path: str | Path,
                    label_rooms: bool = True) -> Path:
    """도면 위 방(채움)·문(빨강)·창(초록)·벽(회색)·객체(주황) 오버레이."""
    img = Image.open(image_path)
    fig, ax = plt.subplots(figsize=(16, 16 * img.height / img.width))
    ax.imshow(img)

    for w in dr.walls:
        _draw_poly(ax, w, edge=WALL_COLOR, alpha=1.0, lw=0.8)
    for r in dr.rooms:
        _draw_poly(ax, r, edge="#1B4F8A", face=ROOM_FILL, alpha=0.30, lw=1.0)
    for d in dr.doors:
        _draw_poly(ax, d, edge=DOOR_COLOR, alpha=1.0, lw=2.2)
    for wd in dr.windows:
        _draw_poly(ax, wd, edge=WINDOW_COLOR, alpha=1.0, lw=2.2)
    for o in dr.objects:
        _draw_poly(ax, o, edge=OBJECT_COLOR, alpha=1.0, lw=1.5)

    if label_rooms:
        for r in dr.rooms:
            if r.centroid is None:
                continue
            name = r.class_name.replace("공간_", "")
            ax.text(r.centroid[0], r.centroid[1], name, fontsize=7,
                    ha="center", va="center", color="white",
                    bbox=dict(boxstyle="round,pad=0.15", fc="#1B4F8A", ec="none", alpha=0.8))

    ax.set_title(f"rooms={len(dr.rooms)} doors={len(dr.doors)} "
                 f"windows={len(dr.windows)} walls={len(dr.walls)}", fontsize=11)
    ax.axis("off")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    print(f"오버레이 저장: {out_path}")
    return out_path


def render_graph(G, out_path: str | Path, title: str = "") -> Path:
    """networkx 위상 그래프를 도면 좌표 위치로 렌더(노드=방, 엣지=문)."""
    import networkx as nx
    out_path = Path(out_path)
    pos = {}
    labels = {}
    colors = []
    hierarchy_color = {"public": "#E8453C", "private": "#4C9BE8",
                       "service": "#2DBE60", None: "#999999"}
    for n, data in G.nodes(data=True):
        c = data.get("centroid")
        pos[n] = (c[0], -c[1]) if c else (0, 0)  # y 뒤집어 이미지 방향과 일치
        labels[n] = data.get("type", str(n)).replace("공간_", "") if n != "exterior" else "外"
        colors.append(hierarchy_color.get(data.get("hierarchy"), "#999999"))

    fig, ax = plt.subplots(figsize=(12, 12))
    nx.draw_networkx_edges(G, pos, ax=ax, width=1.5, edge_color="#555555")
    nx.draw_networkx_nodes(G, pos, ax=ax, node_color=colors, node_size=600, alpha=0.9)
    nx.draw_networkx_labels(G, pos, labels, ax=ax, font_size=7, font_family=KFONT)
    edge_lbl = {(u, v): (d.get("door_type") or d.get("via", ""))
                for u, v, d in G.edges(data=True)}
    nx.draw_networkx_edge_labels(G, pos, edge_lbl, ax=ax, font_size=6,
                                 font_family=KFONT)
    ax.set_title(title, fontsize=11)
    ax.axis("off")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    print(f"그래프 저장: {out_path}")
    return out_path


if __name__ == "__main__":
    from plan2graph.coco import load_coco
    from plan2graph.geometry import assemble_drawing
    # 사용법: visualize.py <image.png> <label1.json> [label2.json ...]
    image = sys.argv[1]
    docs = [load_coco(p) for p in sys.argv[2:]]
    dr = assemble_drawing(docs, image_path=image)
    out = ROOT / "notebooks" / (Path(image).stem + "_overlay.png")
    overlay_drawing(dr, image, out)
