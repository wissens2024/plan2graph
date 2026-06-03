"""관리자 콘솔 로직 계층 (UI 비의존) — 격리/채택 도면 검토·교정·결정 기록.

구성:
- 로더: sheet_id → zip에서 PNG·라벨 온디맨드 추출 → Drawing·시트그래프 재구성.
- 교정 알고리즘(플러그형): 벽틈 개방통로 추론 / 인접 병합 / 수동 엣지.
  각 알고리즘은 '추가할 엣지 목록'을 돌려주고, 적용 후 세대 재분해·재분류로 before→after 비교.
- 결정 원장: review_decisions.csv 에 (도면, 알고리즘, 파라미터, 결과, 시각) 누적.

admin.py(Streamlit)가 이 모듈을 호출한다. 무거운 인덱스는 호출측에서 1회 만들어 주입.
"""
from __future__ import annotations

import csv
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx
from shapely.geometry import Point
from shapely.ops import unary_union
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph.unpack import discover_zips, parse_name  # noqa: E402
from plan2graph.coco import load_coco_bytes  # noqa: E402
from plan2graph.geometry import assemble_drawing, Drawing  # noqa: E402
from plan2graph.topology import (build_graph, iter_units, _RoomIndex,  # noqa: E402
                                 _rooms_for_door, _space_nodes, EXTERIOR)
from plan2graph.rules import validate  # noqa: E402
from plan2graph.schema import serialize  # noqa: E402
from plan2graph.build_dataset import classify_unit  # noqa: E402

LEDGER = config.PROCESSED_DIR / "review_decisions.csv"
LEDGER_FIELDS = ["timestamp", "sheet_id", "graph_id", "action", "algorithm",
                 "params", "result_status", "n_rooms_before", "n_rooms_after",
                 "note"]


# ─────────────────────────────────────────────────────────────────────────────
# 인덱스 (zip 중앙 디렉터리) — 호출측에서 1회 생성해 캐시
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Indices:
    pairs: dict                      # split별 fingerprint -> {label:key}
    label_entry: dict                # (split,label,key) -> (zip,entry)
    source_entry: dict               # (split,label,key) -> (zip,entry)  PNG


def build_indices(splits=("Training", "Validation")) -> Indices:
    import json
    pairs, label_entry, source_entry = {}, {}, {}
    for split in splits:
        lp = config.INTERIM_DIR / f"linked_spa_str_{split.lower()}.json"
        if lp.exists():
            link = json.loads(lp.read_text(encoding="utf-8"))
            for fp, labels in link["pairs"].items():
                pairs[(split, fp)] = labels
    for z in discover_zips():
        split = z["split"]
        if split not in splits:
            continue
        try:
            with zipfile.ZipFile(z["path"]) as zf:
                names = zf.namelist()
        except zipfile.BadZipFile:
            continue
        tgt = (label_entry if z["content"] == "라벨" else source_entry)
        for n in names:
            if n.endswith("/"):
                continue
            m = parse_name(Path(n).stem)
            if m is None or m["drawing"] != config.TARGET_DRAWING_TYPE:
                continue
            tgt[(split, m["label"], m["key"])] = (str(z["path"]), n)
    return Indices(pairs, label_entry, source_entry)


# 열린 ZipFile 핸들 캐시 — 매 호출마다 중앙 디렉터리 재파싱하지 않도록 재사용.
_ZIP_HANDLES: dict[str, zipfile.ZipFile] = {}


def _read_zip(zip_path: str, entry: str) -> bytes:
    zf = _ZIP_HANDLES.get(zip_path)
    if zf is None:
        zf = zipfile.ZipFile(zip_path)
        _ZIP_HANDLES[zip_path] = zf
    return zf.read(entry)


def fingerprint_of(sheet_id: str) -> str:
    """'APT_FP_<crc>_<size>' → '<crc>_<size>'."""
    return sheet_id.split("_FP_", 1)[1]


@dataclass
class Sheet:
    sheet_id: str
    split: str
    house: str
    dr: Drawing
    G: nx.Graph
    png_bytes: bytes | None


def load_sheet(sheet_id: str, idx: Indices,
               split: str | None = None) -> Sheet | None:
    """sheet_id → zip에서 라벨·PNG 추출 → Drawing·시트그래프 재구성.
    split 미지정 시 통합 풀에서 지문이 속한 AI-Hub split을 자동 탐색."""
    fp = fingerprint_of(sheet_id)
    labels = None
    if split is not None and (split, fp) in idx.pairs:
        labels = idx.pairs[(split, fp)]
    else:
        for s in ("Training", "Validation"):   # 통합 풀: 어느 split이든 탐색
            if (s, fp) in idx.pairs:
                split, labels = s, idx.pairs[(s, fp)]
                break
    if labels is None:
        return None
    docs = []
    house = sheet_id.split("_")[0]
    for label in ("SPA", "STR", "OBJ", "OCR"):
        key = labels.get(label)
        if key is None:
            continue
        ie = idx.label_entry.get((split, label, key))
        if ie:
            docs.append(load_coco_bytes(_read_zip(*ie), source=ie[1]))
    if not docs:
        return None
    dr = assemble_drawing(docs)
    G = build_graph(dr, graph_id=sheet_id)
    G.graph["house_type"] = house
    png = None
    for label in ("SPA", "STR", "OBJ", "OCR"):
        key = labels.get(label)
        se = idx.source_entry.get((split, label, key)) if key else None
        if se:
            png = _read_zip(*se)
            break
    return Sheet(sheet_id, split, house, dr, G, png)


# ─────────────────────────────────────────────────────────────────────────────
# 교정 알고리즘 — 추가할 엣지 [(a,b,via), ...] 반환 (적용 전 미리보기 가능)
# ─────────────────────────────────────────────────────────────────────────────
def algo_wallgap_open(dr: Drawing, max_gap: float = 60.0,
                      min_open_ratio: float = 0.30) -> list[tuple]:
    """벽틈 개방통로 추론(콘솔 교정용). 파이프라인 표준엣지와 동일 로직 사용.
    topology.open_passages: 두 방 사이 간격(gap)에서 벽 미피복 비율 ≥ 임계 → 통로.
    """
    from plan2graph.topology import open_passages
    return [(a, b, "open") for a, b in
            open_passages(dr, max_gap=max_gap, min_ratio=min_open_ratio)]


def algo_adjacency_merge(dr: Drawing, G: nx.Graph,
                         max_gap: float = 50.0) -> list[tuple]:
    """인접 병합: 현관 컴포넌트에서 떨어진 고립 방을, gap 이내 최근접 방과
    via='adjacent'로 연결(벽 무시, 공격적 복구). 사람이 미리보기로 검증 전제.
    """
    sub = G.subgraph(_space_nodes(G))
    comps = list(nx.connected_components(sub))
    # 현관 포함 컴포넌트 = 본체
    main = set()
    for c in comps:
        if any(G.nodes[n].get("is_entrance") for n in c):
            main |= c
    if not main:
        main = max(comps, key=len) if comps else set()
    geoms = {i: dr.rooms[i].polygon for i in range(len(dr.rooms))
             if dr.rooms[i].polygon is not None}
    edges = []
    for i in geoms:
        if i in main:
            continue
        # 본체 방 중 최근접
        best, bd = None, max_gap
        for j in main:
            if j not in geoms:
                continue
            d = geoms[i].distance(geoms[j])
            if d < bd:
                best, bd = j, d
        if best is not None:
            edges.append((i, best, "adjacent"))
    return edges


def apply_edges(G: nx.Graph, dr: Drawing, edges: list[tuple]) -> nx.Graph:
    """엣지 추가한 사본 반환."""
    H = G.copy()
    for a, b, via in edges:
        if not H.has_edge(a, b):
            H.add_edge(a, b, via=via, door_type=None)
    return H


# ─────────────────────────────────────────────────────────────────────────────
# 교정 후 재평가 (before→after 비교용)
# ─────────────────────────────────────────────────────────────────────────────
def evaluate(dr: Drawing, G: nx.Graph) -> dict:
    """시트그래프 → 세대 분해·분류 요약."""
    units, noise = iter_units(G, min_rooms=config.ACCEPT_MIN_ROOMS - 3)
    out = []
    for k, U in enumerate(units):
        rec = serialize(U, graph_id=f"{G.graph.get('graph_id')}_u{k}",
                        house_type=G.graph.get("house_type"),
                        width=dr.width, height=dr.height, validation=validate(U))
        status, reason = classify_unit(rec)
        out.append({"idx": k, "n_rooms": U.graph.get("n_rooms"),
                    "status": status, "reason": reason,
                    "program": rec["constraints"]["program"]})
    n_complete = sum(1 for u in out if u["status"] == "complete")
    return {"n_units": len(units), "n_complete": n_complete,
            "n_noise": len(noise), "units": out}


# ─────────────────────────────────────────────────────────────────────────────
# 결정 원장
# ─────────────────────────────────────────────────────────────────────────────
def record_decision_to(ledger: "Path", row: dict) -> None:
    """임의 원장 경로에 결정 1건 추가(출처별 staging 원장 공용)."""
    ledger.parent.mkdir(parents=True, exist_ok=True)
    new = not ledger.exists()
    with open(ledger, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in LEDGER_FIELDS})


def load_ledger_from(ledger: "Path") -> list[dict]:
    if not ledger.exists():
        return []
    with open(ledger, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def record_decision(row: dict) -> None:
    record_decision_to(LEDGER, row)


def load_ledger() -> list[dict]:
    return load_ledger_from(LEDGER)


def load_queue(which: str, processed_dir: Path = config.PROCESSED_DIR) -> list[dict]:
    """'quarantine' 또는 'accepted' 큐 로드."""
    fn = "quarantine.csv" if which == "quarantine" else "accepted.csv"
    p = processed_dir / fn
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_record(graph_id: str, processed_dir: Path = config.PROCESSED_DIR) -> dict | None:
    """채택된 세대 그래프 JSON 레코드 로드(graphs/<id>.json)."""
    import json
    p = processed_dir / "graphs" / f"{graph_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def record_to_graph(record: dict) -> nx.Graph:
    """저장된 레코드(layout) → networkx 그래프 복원(렌더용)."""
    G = nx.Graph(graph_id=record["graph_id"])
    for nd in record["layout"]["nodes"]:
        G.add_node(nd["id"], type=nd.get("type"), hierarchy=nd.get("hierarchy"),
                   centroid=nd.get("centroid_px"), is_entrance=nd.get("is_entrance"))
    for e in record["layout"]["edges"]:
        G.add_edge(e["source"], e["target"], via=e.get("via"),
                   door_type=e.get("door_type"))
    return G


def unit_records(dr: Drawing, G: nx.Graph, sheet_id: str, house: str,
                 min_rooms: int | None = None) -> list[dict]:
    """시트그래프 → 세대별 표준 레코드 + (status, reason) 리스트."""
    mr = config.ACCEPT_MIN_ROOMS - 3 if min_rooms is None else min_rooms
    units, _ = iter_units(G, min_rooms=mr)
    out = []
    for k, U in enumerate(units):
        rec = serialize(U, graph_id=f"{sheet_id}_u{k}", house_type=house,
                        width=dr.width, height=dr.height, validation=validate(U))
        status, reason = classify_unit(rec)
        out.append({"record": rec, "status": status, "reason": reason,
                    "rooms": list(U.nodes())})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 렌더링 (matplotlib Figure 반환 — Streamlit st.pyplot용)
# ─────────────────────────────────────────────────────────────────────────────
import io  # noqa: E402

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager as _fm  # noqa: E402
from matplotlib.patches import Polygon as MplPolygon  # noqa: E402

_avail = {f.name for f in _fm.fontManager.ttflist}
KFONT = next((f for f in ("Malgun Gothic", "AppleGothic", "NanumGothic")
              if f in _avail), "DejaVu Sans")
if KFONT == "DejaVu Sans":   # 시스템에 한글폰트 없으면 번들 TTF 등록(한글 □ 방지)
    from pathlib import Path as _Path
    for _p in (_Path(__file__).resolve().parents[2] / "fonts" / "NanumGothic.ttf",
               _Path.home() / ".fonts" / "NanumGothic.ttf"):
        if _p.exists():
            _fm.fontManager.addfont(str(_p))
            KFONT = _fm.FontProperties(fname=str(_p)).get_name()
            break
matplotlib.rcParams["font.family"] = KFONT
matplotlib.rcParams["axes.unicode_minus"] = False

_VIA_COLOR = {"door": "#E8453C", "balcony": "#2DBE60",
              "open": "#F2A900", "adjacent": "#9B59B6",
              "entrance": "#555555", "exterior_door": "#888888"}
_PALETTE = ["#4C9BE8", "#E8453C", "#2DBE60", "#F2A900", "#9B59B6",
            "#16A085", "#E67E22", "#2C3E50", "#D81B60", "#00ACC1"]


def _component_of(G: nx.Graph) -> dict:
    """공간 노드 → 컴포넌트 색 index."""
    sub = G.subgraph(_space_nodes(G))
    comp_id = {}
    for cid, comp in enumerate(nx.connected_components(sub)):
        for n in comp:
            comp_id[n] = cid
    return comp_id


def render_overlay_fig(sheet: "Sheet", extra_edges: list[tuple] | None = None,
                       focus_rooms: list | None = None, crop_pad: float = 120.0):
    """도면 PNG 위 방(컴포넌트색)·문(빨강선)·발코니(초록)·교정엣지(주황) 오버레이.
    focus_rooms 지정 시 해당 세대만 진하게, 나머지는 흐리게, 세대 영역으로 확대.
    """
    dr, G = sheet.dr, sheet.G
    fig, ax = plt.subplots(figsize=(13, 13), dpi=130)
    fig.patch.set_facecolor("white")
    focus = set(focus_rooms) if focus_rooms is not None else None

    # 표시 영역: focus면 그 세대 bbox+여백, 아니면 전체.
    region = None
    if focus is not None:
        bxs, bys = [], []
        for i in focus:
            if i < len(dr.rooms) and dr.rooms[i].polygon is not None:
                x0, y0, x1, y1 = dr.rooms[i].polygon.bounds
                bxs += [x0, x1]; bys += [y0, y1]
        if bxs:
            region = (min(bxs) - crop_pad, min(bys) - crop_pad,
                      max(bxs) + crop_pad, max(bys) + crop_pad)

    if sheet.png_bytes:
        from PIL import Image
        img = Image.open(io.BytesIO(sheet.png_bytes))
        ow, oh = img.size
        # 원본을 표시 영역만 잘라 전체 해상도 유지(확대해도 선명). 좌표는 extent로 매핑.
        rx0, ry0, rx1, ry1 = region if region else (0, 0, ow, oh)
        rx0 = max(0, int(rx0)); ry0 = max(0, int(ry0))
        rx1 = min(ow, int(rx1)); ry1 = min(oh, int(ry1))
        crop = img.crop((rx0, ry0, rx1, ry1))
        # 너무 큰 영역만 상한(2800px)으로 축소 — extent는 원좌표 유지해 정렬 보존.
        cw, ch = crop.size
        m = max(cw, ch)
        if m > 2800:
            crop = crop.resize((max(1, cw * 2800 // m), max(1, ch * 2800 // m)))
        # 원본은 흑백(L). cmap 미지정 시 기본 viridis가 노랗게 칠함 → gray 고정.
        ax.imshow(crop, cmap="gray", vmin=0, vmax=255, extent=(rx0, rx1, ry1, ry0))
    comp_id = _component_of(G)
    fxs, fys = [], []
    for i, r in enumerate(dr.rooms):
        if r.polygon is None:
            continue
        dim = focus is not None and i not in focus
        col = "#BBBBBB" if dim else _PALETTE[comp_id.get(i, 0) % len(_PALETTE)]
        a_fill = 0.10 if dim else 0.32
        polys = r.polygon.geoms if r.polygon.geom_type == "MultiPolygon" else [r.polygon]
        for g in polys:
            xs, ys = g.exterior.xy
            ax.add_patch(MplPolygon(list(zip(xs, ys)), closed=True,
                                    facecolor=col, edgecolor=col, alpha=a_fill, lw=1.0))
            if focus is not None and i in focus:
                fxs += list(xs); fys += list(ys)
        if r.centroid and not dim:
            ax.text(r.centroid[0], r.centroid[1], r.class_name.replace("공간_", ""),
                    fontsize=7, ha="center", va="center", color="black",
                    bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.7))
    cen = {i: dr.rooms[i].centroid for i in range(len(dr.rooms))
           if dr.rooms[i].centroid}
    for u, v, d in G.edges(data=True):
        if u == EXTERIOR or v == EXTERIOR or u not in cen or v not in cen:
            continue
        if focus is not None and not (u in focus or v in focus):
            continue
        c = _VIA_COLOR.get(d.get("via"), "#333333")
        ax.plot([cen[u][0], cen[v][0]], [cen[u][1], cen[v][1]],
                color=c, lw=2.0, alpha=0.9)
    for a, b, via in (extra_edges or []):
        if a in cen and b in cen:
            ax.plot([cen[a][0], cen[b][0]], [cen[a][1], cen[b][1]],
                    color=_VIA_COLOR.get(via, "#F2A900"), lw=2.0, ls="--", alpha=0.95)
    if focus is not None and fxs:
        ax.set_xlim(min(fxs) - crop_pad, max(fxs) + crop_pad)
        ax.set_ylim(max(fys) + crop_pad, min(fys) - crop_pad)  # y 반전(이미지 좌표)
    ax.set_title(f"{sheet.sheet_id}  방{len(dr.rooms)} 문{len(dr.doors)} "
                 f"창{len(dr.windows)}  (색=연결요소)", fontsize=9)
    ax.axis("off")
    fig.tight_layout()
    return fig


def _spread_geo(geo: dict, min_dist: float, iters: int = 150,
                anchor: float = 0.03) -> dict:
    """도면 좌표(geo)를 시작점으로, 겹치는 노드만 밀어내 가독성 확보.
    공간 배치(왼쪽 위/중앙 등 상대 위치)는 유지하면서 라벨 겹침만 제거.
    결정적(난수 없음).
    """
    import math
    pos = {n: [float(x), float(y)] for n, (x, y) in geo.items()}
    xs = [p[0] for p in pos.values()] or [0]
    ys = [p[1] for p in pos.values()] or [0]
    s = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
    mnx, mny = min(xs), min(ys)
    for n in pos:  # [0,1] 정규화(종횡비 유지)
        pos[n][0] = (pos[n][0] - mnx) / s
        pos[n][1] = (pos[n][1] - mny) / s
    orig = {n: list(p) for n, p in pos.items()}
    nodes = list(pos)
    for _ in range(iters):
        for a in range(len(nodes)):
            for b in range(a + 1, len(nodes)):
                na, nb = nodes[a], nodes[b]
                dx = pos[nb][0] - pos[na][0]
                dy = pos[nb][1] - pos[na][1]
                d = math.hypot(dx, dy) or 1e-6
                if d < min_dist:
                    push = (min_dist - d) / 2.0
                    ux, uy = dx / d, dy / d
                    pos[na][0] -= ux * push; pos[na][1] -= uy * push
                    pos[nb][0] += ux * push; pos[nb][1] += uy * push
        for n in nodes:  # 원래 도면 위치로 살짝 당겨 전체 배치 유지
            pos[n][0] += (orig[n][0] - pos[n][0]) * anchor
            pos[n][1] += (orig[n][1] - pos[n][1]) * anchor
    return pos


def render_graph_fig(G: nx.Graph, title: str = "", node_size: int = 2600,
                     font_size: int = 13, layout: str = "spatial"):
    """위상 그래프(노드=방, 색=위계) Figure.
    layout='spatial' 도면 위치 유지+겹침제거(기본) / 'geo' 원좌표 / 'kamada' 펼침.
    """
    hc = {"public": "#E8453C", "private": "#4C9BE8", "service": "#2DBE60", None: "#999"}
    geo = {}
    for n, d in G.nodes(data=True):
        c = d.get("centroid")
        geo[n] = (c[0], -c[1]) if c else (0.0, 0.0)
    if layout == "kamada":
        try:
            pos = nx.kamada_kawai_layout(G)
        except Exception:
            pos = nx.spring_layout(G, seed=7, k=2.0, iterations=100)
    elif layout == "geo":
        pos = geo
    else:  # spatial: 도면 위치 유지하며 겹침만 제거
        min_dist = 0.14 * (node_size / 2600.0) ** 0.5
        pos = _spread_geo(geo, min_dist=min_dist)
    labels, colors = {}, []
    for n, d in G.nodes(data=True):
        labels[n] = "外" if n == EXTERIOR else str(d.get("type", n)).replace("공간_", "")
        colors.append(hc.get(d.get("hierarchy"), "#999"))
    fig, ax = plt.subplots(figsize=(10, 10))
    fig.patch.set_facecolor("white")
    ec = [_VIA_COLOR.get(d.get("via"), "#555") for _, _, d in G.edges(data=True)]
    nx.draw_networkx_edges(G, pos, ax=ax, width=2.2, edge_color=ec, alpha=0.8)
    nx.draw_networkx_nodes(G, pos, ax=ax, node_color=colors, node_size=node_size,
                           alpha=0.92, edgecolors="white", linewidths=1.5)
    nx.draw_networkx_labels(G, pos, labels, ax=ax, font_size=font_size,
                            font_family=KFONT, font_color="white", font_weight="bold")
    ax.set_title(title, fontsize=11)
    ax.margins(0.12)
    ax.axis("off")
    fig.tight_layout()
    return fig
