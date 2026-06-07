"""사람-인-더-루프 위상 편집 — 신규 구축(기존 추출/골드 코드 미사용).

배경: 자동 위상 추출은 연결공간(복도·전실) 누락으로 '방을 지나 방' 도면을 낳고,
규칙 튜닝으로 고치려 하면 두더지잡기로 수렴하지 않는다(세션 반복 확인).
→ 결론: 사람이 원본 도면 위에서 위상을 직접 만든다. 이 모듈이 그 도구의 코어다.

원칙(엄수):
  - 자동 위상 추론 0. corridor carving·door routing 같은 휴리스틱을 일절 쓰지 않는다.
    (extract2·topology.build_graph 등 시소게임 산물 import 안 함)
  - 원시 라벨(SPA/STR/OBJ/OCR)과 PNG만 읽는다. coco·geometry 파서는 '데이터 판독'이지
    추론이 아니다(폴리곤화까지만). 위상은 전부 사람이 만든다.
  - 노드 = 라벨된 방(원시). 엣지 = 빈 상태에서 시작 → 사람이 연결공간·문·역할을 박는다.
  - 결과는 신규 포맷(topo-human-v1)으로 data/staging/topo_human/ 에 저장(기존 골드와 분리).

구성: 데이터 로더 · 편집 상태/연산(순수) · 영속 · matplotlib 렌더(헤드리스) · Streamlit 화면.
"""
from __future__ import annotations

import io
import json
import sys
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import networkx as nx

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph.coco import load_coco  # noqa: E402
from plan2graph.geometry import assemble_drawing, Drawing  # noqa: E402

# ── 저장 위치(신규, 기존 골드와 분리). staging=현재 단일 진실 ────────────────────
OUT_DIR = config.DATA_DIR / "staging" / "topo_human"
REC_DIR = OUT_DIR / "records"
LEDGER = OUT_DIR / "_ledger.jsonl"

SCHEMA = "topo-human-v1"
CONNECTOR_BASES = ("복도", "전실")
VIA_KINDS = ("door", "open", "corridor", "entrance")
STATUSES = ("미검수", "검증완료", "모호", "제외")
# 사람이 지정하는 역할(라벨 base에서 세분) — 욕실/화장실, 안방/침실, 전용 등
ROLES = ("거실", "주방", "현관", "침실", "안방", "화장실", "욕실", "전용화장실",
         "전용욕실", "드레스룸", "발코니", "실외기실", "다목적공간", "복도", "전실",
         "기타", "실외", "엘리베이터홀", "계단실", "엘리베이터")

ROLE_COLOR = {
    "침실": "#6CA6E8", "안방": "#1f6fd6", "거실": "#E8453C", "주방": "#F2A900",
    "화장실": "#9ACD66", "욕실": "#3a9a3a", "전용욕실": "#1a7a4a", "전용화장실": "#7ab04a",
    "현관": "#d9534f", "발코니": "#2DBE60", "드레스룸": "#a070d0", "복도": "#9aa0a6",
    "전실": "#c0c4c8", "기타": "#bcbcbc", "실외기실": "#9cc8e8", "다목적공간": "#fdc08a",
    "계단실": "#888", "엘리베이터": "#888", "엘리베이터홀": "#888", "실외": "#ddd",
}


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 로더 — 느슨한 라벨파일을 PNG 내용해시로 묶음(zip·인덱스·추론 미사용)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class RawPlan:
    plan_id: str                 # {HOUSE}_FP_{crc}_{size}
    house: str
    png_path: Path
    label_paths: dict = field(default_factory=dict)   # {'SPA': Path, 'STR': Path, ...}


def _png_fp(data: bytes) -> str:
    """PNG 내용 지문(crc32_size) — SPA/STR/OBJ/OCR을 같은 도면으로 묶는 키."""
    return f"{zlib.crc32(data) & 0xffffffff:08x}_{len(data)}"


def scan_dir(dirpath) -> list[RawPlan]:
    """라벨 디렉터리 → 도면 목록. 파일명 {HOUSE}_FP_{LABEL}_{key}.json|PNG 를
    PNG 내용 지문으로 그룹화(라벨별 key가 달라도 같은 그림이면 한 도면)."""
    dirpath = Path(dirpath)
    if not dirpath.is_dir():
        return []
    # stem -> (path, house, label, suffix)
    pngs, jsons = {}, {}
    for p in sorted(dirpath.iterdir()):
        suf = p.suffix.lower()
        if suf not in (".png", ".json"):
            continue
        parts = p.stem.split("_")
        if len(parts) < 4 or parts[1] != "FP":
            continue
        house, label = parts[0], parts[2]
        (pngs if suf == ".png" else jsons)[p.stem] = (p, house, label)
    groups: dict[str, RawPlan] = {}
    for stem, (png, house, label) in pngs.items():
        try:
            fp = _png_fp(png.read_bytes())
        except OSError:
            continue
        plan_id = f"{house}_FP_{fp}"
        rp = groups.setdefault(plan_id, RawPlan(plan_id, house, png))
        jp = jsons.get(stem)
        if jp:
            rp.label_paths[label] = jp[0]
    return sorted(groups.values(), key=lambda r: r.plan_id)


def load_plan(rp: RawPlan, scale: float | None = config.DEFAULT_SCALE):
    """RawPlan → (Drawing, png_bytes). 위상 그래프는 만들지 않는다(사람 몫)."""
    docs = []
    for label in ("SPA", "STR", "OBJ", "OCR"):
        jp = rp.label_paths.get(label)
        if jp:
            docs.append(load_coco(jp))
    dr = assemble_drawing(docs, image_path=rp.png_path, scale=scale)
    return dr, rp.png_path.read_bytes()


def segment_units(dr: Drawing, gap: float = 40.0, min_rooms: int = 3) -> list[list[int]]:
    """시트에 타일된 여러 세대를 편집 단위로 분리. **공간 근접 군집**(방 폴리곤
    거리 ≤ gap 이면 같은 타일)의 연결요소. 위상 추론(문/복도) 아님 — 세대끼리는
    큰 빈틈으로 떨어져 있어 근접만으로 깔끔히 갈린다. 반환: 방 index 리스트들."""
    rooms = [(i, r) for i, r in enumerate(dr.rooms) if r.polygon is not None]
    g = nx.Graph()
    g.add_nodes_from(i for i, _ in rooms)
    if rooms:
        from shapely.strtree import STRtree
        geoms = [r.polygon for _, r in rooms]
        idxs = [i for i, _ in rooms]
        tree = STRtree(geoms)
        for i, r in rooms:
            for hit in tree.query(r.polygon.buffer(gap)):
                j = idxs[int(hit)]
                if j != i and r.polygon.distance(dr.rooms[j].polygon) <= gap:
                    g.add_edge(i, j)
    units = [sorted(c) for c in nx.connected_components(g) if len(c) >= min_rooms]
    return sorted(units, key=lambda u: -len(u))


# ─────────────────────────────────────────────────────────────────────────────
# 편집 상태 + 연산 (순수 로직, UI 비의존)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Node:
    id: int
    base: str          # 라벨 원본 종류(침실/거실/...) 또는 복도/전실
    role: str          # 사람이 지정하는 세분 역할
    source: str        # 'label' | 'human'
    cx: float
    cy: float
    polygon: object = None      # shapely Polygon(라벨 방) 또는 None(사람 연결공간)
    fixtures: list = field(default_factory=list)   # 방 안의 기구(욕조·변기…) — 역할판단 보조
    area_px: float = 0.0


@dataclass
class State:
    plan_id: str
    house: str
    nodes: dict = field(default_factory=dict)    # id -> Node
    edges: list = field(default_factory=list)    # [{a,b,via,source}]
    _next_conn: int = 100000


def _fixtures_in(dr: Drawing, poly) -> list[str]:
    from shapely.geometry import Point
    if poly is None:
        return []
    return [o.class_name.replace("객체_", "") for o in dr.objects
            if o.centroid and poly.contains(Point(*o.centroid))]


def init_state(dr: Drawing, plan_id: str, house: str,
               room_ids=None) -> State:
    """원시 방 = 노드. 엣지 0(자동추론 금지). 기구는 보조정보로 귀속.
    room_ids 주면 그 방들만 = 세대 단위 편집(segment_units 결과)."""
    ids = list(room_ids) if room_ids is not None else list(range(len(dr.rooms)))
    nodes = {}
    for i in ids:
        r = dr.rooms[i]
        if r.polygon is None:
            continue
        base = r.class_name.replace("공간_", "")
        cx, cy = r.centroid if r.centroid else (0.0, 0.0)
        nodes[i] = Node(id=i, base=base, role=base, source="label",
                        cx=float(cx), cy=float(cy), polygon=r.polygon,
                        fixtures=_fixtures_in(dr, r.polygon), area_px=float(r.area_px))
    return State(plan_id=plan_id, house=house, nodes=nodes, edges=[])


def add_edge(st: State, a: int, b: int, via: str) -> bool:
    if a == b or a not in st.nodes or b not in st.nodes:
        return False
    for e in st.edges:
        if {e["a"], e["b"]} == {a, b}:
            e["via"] = via
            return True
    st.edges.append({"a": a, "b": b, "via": via, "source": "human"})
    return True


def remove_edge(st: State, a: int, b: int) -> None:
    st.edges = [e for e in st.edges if {e["a"], e["b"]} != {a, b}]


def add_connector(st: State, base: str, member_ids: list[int], via: str = "door") -> int:
    """연결공간(복도/전실) 노드 신설 + 지정한 방들과 연결. 위치=구성원 중심."""
    members = [st.nodes[m] for m in member_ids if m in st.nodes]
    cx = sum(n.cx for n in members) / len(members) if members else 0.0
    cy = sum(n.cy for n in members) / len(members) if members else 0.0
    nid = st._next_conn
    st._next_conn += 1
    st.nodes[nid] = Node(id=nid, base=base, role=base, source="human",
                         cx=cx, cy=cy, polygon=None)
    for m in member_ids:
        add_edge(st, nid, m, via)
    return nid


def set_role(st: State, nid: int, role: str) -> None:
    if nid in st.nodes:
        st.nodes[nid].role = role


def remove_node(st: State, nid: int) -> None:
    st.nodes.pop(nid, None)
    st.edges = [e for e in st.edges if nid not in (e["a"], e["b"])]


# ─────────────────────────────────────────────────────────────────────────────
# 영역 그리기(사람) + 차감(carve) + 면적 — 복도/전실을 '진짜 폴리곤'으로 신설
#   사람이 도면 위에 찍은 꼭짓점 → 폴리곤. 겹치는 방에서 difference로 잘라내(carve)
#   면적 중복 제거. 자동 carve(폐기한 extract2)를 사람 손으로.
# ─────────────────────────────────────────────────────────────────────────────
def _largest_poly(geom):
    """Polygon/Multi/GeometryCollection → 최대면적 Polygon(없으면 None)."""
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type == "Polygon":
        return geom
    polys = [g for g in getattr(geom, "geoms", [])
             if g.geom_type == "Polygon" and not g.is_empty]
    return max(polys, key=lambda g: g.area) if polys else None


def add_drawn_region(st: State, base: str, points, dr: Drawing,
                     role: str | None = None) -> int | None:
    """사람이 찍은 꼭짓점(원본 px)들 → 폴리곤 연결공간 노드. 겹치는 라벨 방에서
    difference로 차감(carve)해 면적 중복 제거. 면적 계산."""
    from shapely.geometry import Polygon as _Poly
    from shapely.validation import make_valid
    pts = [(float(x), float(y)) for x, y in points]
    if len(pts) < 3:
        return None
    poly = _Poly(pts)
    if not poly.is_valid:
        poly = _largest_poly(make_valid(poly))
    if poly is None or poly.is_empty:
        return None
    for n in st.nodes.values():       # 겹치는 라벨 방에서 차감
        if n.source == "label" and n.polygon is not None and n.polygon.intersects(poly):
            carved = _largest_poly(n.polygon.difference(poly))
            if carved is not None:
                n.polygon = carved
                n.area_px = carved.area
                c = carved.centroid
                n.cx, n.cy = c.x, c.y
    nid = st._next_conn
    st._next_conn += 1
    c = poly.centroid
    st.nodes[nid] = Node(id=nid, base=base, role=role or base, source="human",
                         cx=c.x, cy=c.y, polygon=poly, area_px=poly.area)
    return nid


# ─────────────────────────────────────────────────────────────────────────────
# SVG(완전 기하) = 사람 산출물 · 위상 추출의 입력. 원본 raster/라벨은 별도 보존.
#   영역=<polygon data-*> · 문=<circle data-kind=door> · 비문연결=<line data-kind=link>.
#   AI-Hub 원본 px 좌표계 그대로(SPA/STR에서 옴).
# ─────────────────────────────────────────────────────────────────────────────
SVG_SCHEMA = "topo-human-svg-v1"


def to_svg(st: State, dr: Drawing) -> str:
    W, H = int(dr.width or 0), int(dr.height or 0)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'viewBox="0 0 {W} {H}" data-schema="{SVG_SCHEMA}" data-plan="{st.plan_id}" '
           f'data-house="{st.house}">']
    for n in st.nodes.values():
        if n.polygon is None:
            continue
        pts = " ".join(f"{round(x, 1)},{round(y, 1)}"
                       for x, y in n.polygon.exterior.coords)
        out.append(
            f'  <polygon points="{pts}" data-id="{n.id}" data-base="{n.base}" '
            f'data-role="{n.role}" data-source="{n.source}" '
            f'data-area-px="{round(n.area_px, 1)}" fill="{ROLE_COLOR.get(n.role, "#ccc")}" '
            f'fill-opacity="0.4" stroke="#333" stroke-width="1"/>')
    for d in dr.doors:
        if d.centroid:
            out.append(
                f'  <circle cx="{round(d.centroid[0], 1)}" cy="{round(d.centroid[1], 1)}" '
                f'r="6" data-kind="door" data-subtype="{d.subtype or ""}"/>')
    for e in st.edges:           # 사람이 표시한 비-문 연결(open/corridor/entrance)
        out.append(f'  <line data-kind="link" data-a="{e["a"]}" data-b="{e["b"]}" '
                   f'data-via="{e["via"]}"/>')
    out.append("</svg>")
    return "\n".join(out)


def parse_svg(svg: str):
    """우리 SVG → (regions, doors, links). 위상 추출의 입력(SVG가 단일 진실)."""
    import xml.etree.ElementTree as ET
    from shapely.geometry import Polygon as _Poly
    root = ET.fromstring(svg)
    ns = root.tag[:root.tag.index("}") + 1] if root.tag.startswith("{") else ""
    regions, doors, links = [], [], []
    for el in root:
        tag = el.tag.replace(ns, "")
        if tag == "polygon":
            coords = []
            for tok in el.get("points", "").split():
                xy = tok.split(",")
                if len(xy) == 2:
                    coords.append((float(xy[0]), float(xy[1])))
            if len(coords) >= 3:
                regions.append({
                    "id": int(el.get("data-id")), "base": el.get("data-base"),
                    "role": el.get("data-role"), "source": el.get("data-source"),
                    "area_px": float(el.get("data-area-px", 0) or 0),
                    "polygon": _Poly(coords)})
        elif tag == "circle" and el.get("data-kind") == "door":
            doors.append({"x": float(el.get("cx")), "y": float(el.get("cy")),
                          "subtype": el.get("data-subtype") or None})
        elif tag == "line" and el.get("data-kind") == "link":
            links.append({"a": int(el.get("data-a")), "b": int(el.get("data-b")),
                          "via": el.get("data-via")})
    return regions, doors, links


def extract_topology(regions, doors, links=None, gap: float = 60.0):
    """완전 기하(영역+문) → 위상 그래프(결정적). 문마다 가장 가까운 2영역=엣지.
    영역에 복도가 들어있어 routing 정확(두더지잡기 없음). 면적은 노드 속성."""
    from shapely.geometry import Point
    G = nx.Graph()
    for r in regions:
        G.add_node(r["id"], base=r["base"], role=r["role"],
                   area_px=round(r["area_px"], 1),
                   is_connector=r["base"] in CONNECTOR_BASES)
    for d in doors:
        p = Point(d["x"], d["y"])
        cand = sorted(((r["polygon"].distance(p), r["id"]) for r in regions),
                      key=lambda t: t[0])
        cand = [(dist, rid) for dist, rid in cand if dist <= gap][:2]
        if len(cand) == 2 and cand[0][1] != cand[1][1]:
            G.add_edge(cand[0][1], cand[1][1], via="door")
    for l in (links or []):
        if l["a"] in G and l["b"] in G:
            G.add_edge(l["a"], l["b"], via=l["via"])
    return G


def save_svg(st: State, dr: Drawing, *, status: str = "검증완료",
             curator: str = "", notes: str = "", ts: str | None = None) -> Path:
    REC_DIR.mkdir(parents=True, exist_ok=True)
    p = REC_DIR / f"{st.plan_id}.svg"
    p.write_text(to_svg(st, dr), encoding="utf-8")
    set_status(st.plan_id, status, curator=curator, notes=notes,
               ts=ts or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return p


# ─────────────────────────────────────────────────────────────────────────────
# 영속 (신규 포맷 topo-human-v1)
# ─────────────────────────────────────────────────────────────────────────────
def _ring(poly) -> list:
    try:
        xs, ys = poly.exterior.xy
        return [[round(float(x), 1), round(float(y), 1)] for x, y in zip(xs, ys)]
    except Exception:
        return []


def to_record(st: State, *, status: str = "검증완료", curator: str = "",
              notes: str = "", ts: str | None = None) -> dict:
    nodes = [{
        "id": n.id, "base": n.base, "role": n.role,
        "is_connector": n.base in CONNECTOR_BASES, "source": n.source,
        "centroid_px": [round(n.cx, 1), round(n.cy, 1)],
        "polygon_px": _ring(n.polygon) if n.polygon is not None else [],
        "fixtures": n.fixtures, "area_px2": round(n.area_px, 1),
    } for n in st.nodes.values()]
    return {
        "schema": SCHEMA, "plan_id": st.plan_id, "unit_id": st.plan_id,
        "house": st.house, "source": "aihub",
        "nodes": nodes,
        "edges": [{"a": e["a"], "b": e["b"], "via": e["via"],
                   "source": e.get("source", "human")} for e in st.edges],
        "status": status, "curator": curator, "notes": notes,
        "ts": ts or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def save_record(rec: dict) -> Path:
    REC_DIR.mkdir(parents=True, exist_ok=True)
    p = REC_DIR / f"{rec['unit_id']}.json"
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    set_status(rec["unit_id"], rec["status"], curator=rec.get("curator", ""),
               notes=rec.get("notes", ""), ts=rec["ts"])
    return p


def load_record(unit_id: str) -> dict | None:
    p = REC_DIR / f"{unit_id}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def state_from_record(rec: dict, dr: Drawing | None = None) -> State:
    """저장 레코드 → State 복원(재편집). 라벨 방 polygon은 dr 있으면 다시 붙임."""
    nodes, nextc = {}, 100000
    for nd in rec["nodes"]:
        poly = None
        if (dr is not None and nd["source"] == "label"
                and isinstance(nd["id"], int) and nd["id"] < len(dr.rooms)):
            poly = dr.rooms[nd["id"]].polygon
        nodes[nd["id"]] = Node(
            id=nd["id"], base=nd["base"], role=nd["role"], source=nd["source"],
            cx=nd["centroid_px"][0], cy=nd["centroid_px"][1], polygon=poly,
            fixtures=nd.get("fixtures", []), area_px=nd.get("area_px2", 0.0))
        if isinstance(nd["id"], int) and nd["id"] >= nextc:
            nextc = nd["id"] + 1
    edges = [{"a": e["a"], "b": e["b"], "via": e["via"],
              "source": e.get("source", "human")} for e in rec["edges"]]
    st = State(plan_id=rec["plan_id"], house=rec["house"], nodes=nodes, edges=edges)
    st._next_conn = nextc
    return st


def load_svg_text(unit_id: str) -> str | None:
    p = REC_DIR / f"{unit_id}.svg"
    return p.read_text(encoding="utf-8") if p.exists() else None


def state_from_svg(svg: str, dr: Drawing, plan_id: str, house: str) -> State:
    """저장 SVG → State 복원(재편집). 영역=노드, link=엣지. 기구는 dr에서 재귀속."""
    regions, _doors, links = parse_svg(svg)
    nodes, nextc = {}, 100000
    for r in regions:
        poly = r["polygon"]
        c = poly.centroid
        nodes[r["id"]] = Node(id=r["id"], base=r["base"], role=r["role"],
                              source=r["source"], cx=c.x, cy=c.y, polygon=poly,
                              area_px=r["area_px"],
                              fixtures=_fixtures_in(dr, poly) if r["source"] == "label" else [])
        if isinstance(r["id"], int) and r["id"] >= nextc:
            nextc = r["id"] + 1
    edges = [{"a": l["a"], "b": l["b"], "via": l["via"], "source": "human"} for l in links]
    st = State(plan_id=plan_id, house=house, nodes=nodes, edges=edges)
    st._next_conn = nextc
    return st


def set_status(unit_id: str, status: str, *, curator: str = "",
               notes: str = "", ts: str = "") -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    row = {"unit_id": unit_id, "status": status, "curator": curator,
           "notes": notes, "at": ts}
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_ledger() -> dict:
    out = {}
    if LEDGER.exists():
        for ln in LEDGER.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                r = json.loads(ln)
                out[r["unit_id"]] = r
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 렌더 (matplotlib, 헤드리스 — 테스트·Streamlit 공용)
# ─────────────────────────────────────────────────────────────────────────────
def _set_kfont() -> None:
    """한글 폰트 설정(□ 방지). 시스템 폰트 우선, 없으면 번들 fonts/NanumGothic.ttf
    등록(review.py와 동일 전략 — 단, 가비지 의존 피하려 review import는 안 함)."""
    import matplotlib
    from matplotlib import font_manager
    avail = {f.name for f in font_manager.fontManager.ttflist}
    for f in ("Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR"):
        if f in avail:
            matplotlib.rcParams["font.family"] = f
            matplotlib.rcParams["axes.unicode_minus"] = False
            return
    for p in (ROOT / "fonts" / "NanumGothic.ttf", Path.home() / ".fonts" / "NanumGothic.ttf"):
        if p.exists():
            try:
                font_manager.fontManager.addfont(str(p))
                matplotlib.rcParams["font.family"] = \
                    font_manager.FontProperties(fname=str(p)).get_name()
                matplotlib.rcParams["axes.unicode_minus"] = False
                return
            except Exception:
                continue


def render_figure(dr: Drawing, png: bytes | None, st: State,
                  highlight: int | None = None, figsize=(9, 9)):
    """원본 위에 현재 위상(방·연결공간·엣지·역할) 오버레이. 문 위치는 회색 참고점."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _set_kfont()
    fig, ax = plt.subplots(figsize=figsize)
    if png:
        from PIL import Image
        ax.imshow(Image.open(io.BytesIO(png)).convert("RGB"), alpha=0.55)
    # 엣지(폴리곤 아래 그리면 묻혀서 위에)
    ec = {"door": "#111", "open": "#F2A900", "corridor": "#666", "entrance": "#d00"}
    for e in st.edges:
        a, b = st.nodes.get(e["a"]), st.nodes.get(e["b"])
        if a and b:
            ax.plot([a.cx, b.cx], [a.cy, b.cy], color=ec.get(e["via"], "#111"),
                    lw=2.0, ls="--" if e["via"] == "corridor" else "-", zorder=4)
    # 방 폴리곤
    for n in st.nodes.values():
        if n.polygon is not None:
            try:
                xs, ys = n.polygon.exterior.xy
                ax.fill(xs, ys, color=ROLE_COLOR.get(n.role, "#ccc"),
                        alpha=0.65 if n.id == highlight else 0.4,
                        ec="red" if n.id == highlight else "#333",
                        lw=2.5 if n.id == highlight else 0.7, zorder=2)
            except Exception:
                pass
    # 연결공간(폴리곤 없음) = 다이아몬드 마커
    for n in st.nodes.values():
        if n.polygon is None:
            ax.plot(n.cx, n.cy, "D", ms=12, zorder=5,
                    color=ROLE_COLOR.get(n.role, "#888"), mec="#222")
    # 라벨(id·역할)
    for n in st.nodes.values():
        ax.text(n.cx, n.cy, f"{n.id}\n{n.role}", fontsize=7, ha="center",
                va="center", weight="bold", color="#111", zorder=6)
    # 문 위치(원시 STR) — 사람 참고용 회색점
    for d in dr.doors:
        if d.centroid:
            ax.plot(d.centroid[0], d.centroid[1], "s", ms=3, color="#555",
                    alpha=0.6, zorder=3)
    # 편집 중인 세대로 줌(전체 시트가 아니라 해당 유닛 bbox + 여백)
    xs_all, ys_all = [], []
    for n in st.nodes.values():
        if n.polygon is not None:
            x0, y0, x1, y1 = n.polygon.bounds
            xs_all += [x0, x1]
            ys_all += [y0, y1]
        else:
            xs_all.append(n.cx)
            ys_all.append(n.cy)
    if xs_all:
        m = 70
        ax.set_xlim(min(xs_all) - m, max(xs_all) + m)
        ax.set_ylim(max(ys_all) + m, min(ys_all) - m)   # 이미지 좌표(y 반전)
    nroom = sum(1 for n in st.nodes.values() if n.base not in CONNECTOR_BASES)
    nconn = len(st.nodes) - nroom
    ax.set_title(f"{st.plan_id}   방 {nroom} · 연결공간 {nconn} · 엣지 {len(st.edges)}")
    ax.axis("off")
    fig.tight_layout()
    return fig


def _hex_rgba(hex_color: str, alpha: int):
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


def crop_overlay_image(dr: Drawing, png: bytes, st: State, draw_pts,
                       margin: int = 90, max_w: int = 760):
    """유닛 크롭 raster + 영역 외곽 + 그리는 중 폴리곤 → (PIL RGB, (x0,y0,native_w,native_h)).
    클릭→원본좌표 매핑용. draw_pts=현재 찍은 꼭짓점(원본 px)."""
    from PIL import Image, ImageDraw
    if not png:
        return None, (0, 0, 0, 0)
    img = Image.open(io.BytesIO(png)).convert("RGB")
    xs, ys = [], []
    for n in st.nodes.values():
        if n.polygon is not None:
            a, b, c, d = n.polygon.bounds
            xs += [a, c]
            ys += [b, d]
    for x, y in draw_pts:
        xs.append(x)
        ys.append(y)
    if not xs:
        return None, (0, 0, 0, 0)
    x0 = max(0, int(min(xs) - margin))
    y0 = max(0, int(min(ys) - margin))
    x1 = min(img.width, int(max(xs) + margin))
    y1 = min(img.height, int(max(ys) + margin))
    crop = img.crop((x0, y0, x1, y1)).convert("RGBA")
    ov = Image.new("RGBA", crop.size, (0, 0, 0, 0))
    dd = ImageDraw.Draw(ov)
    for n in st.nodes.values():
        if n.polygon is None:
            continue
        pts = [(x - x0, y - y0) for x, y in n.polygon.exterior.coords]
        if len(pts) >= 3:
            dd.polygon(pts, fill=_hex_rgba(ROLE_COLOR.get(n.role, "#cccccc"), 80),
                       outline=(50, 50, 50, 255))
    if draw_pts:
        fp = [(x - x0, y - y0) for x, y in draw_pts]
        if len(fp) >= 2:
            dd.line(fp + ([fp[0]] if len(fp) >= 3 else []), fill=(230, 30, 30, 255), width=3)
        for px, py in fp:
            dd.ellipse([px - 6, py - 6, px + 6, py + 6], fill=(230, 30, 30, 255))
    out = Image.alpha_composite(crop, ov).convert("RGB")
    native_w, native_h = x1 - x0, y1 - y0
    if out.width > max_w:
        out = out.resize((max_w, max(1, int(out.height * max_w / out.width))))
    return out, (x0, y0, native_w, native_h)


# ─────────────────────────────────────────────────────────────────────────────
# Streamlit 화면 (admin.py에서 호출)
# ─────────────────────────────────────────────────────────────────────────────
def _rerun(st_mod) -> None:
    (getattr(st_mod, "rerun", None) or st_mod.experimental_rerun)()


def _draw_buf(bundle, unit_id):
    return bundle["draw"].setdefault(
        unit_id, {"active": False, "base": "복도", "pts": [], "last": None})


def render_editor() -> None:
    import streamlit as st
    try:
        from streamlit_image_coordinates import streamlit_image_coordinates as _img_coords
    except Exception:  # noqa: BLE001
        _img_coords = None

    st.title("✏️ 위상 편집 (신규 · 사람 인-더-루프)")
    st.caption("사람이 원본 위에 완전 기하(영역)를 만들고 → 위상은 결정적으로 추출. "
               "복도/전실을 그려넣고(겹친 방서 차감·면적), 문은 자동·역할 확정 → SVG gold 저장.")

    default_dir = str(config.DATA_DIR / "raw" / "linked_demo")
    src = st.sidebar.text_input("라벨 디렉터리", value=default_dir)
    try:
        plans = scan_dir(src)
    except Exception as e:  # noqa: BLE001
        st.error(f"스캔 실패: {e}")
        return
    if not plans:
        st.warning(f"도면 없음: {src}")
        return

    led = load_ledger()
    ids = [p.plan_id for p in plans]
    sel = st.sidebar.selectbox(f"도면 ({len(ids)})", ids)
    rp = next(p for p in plans if p.plan_id == sel)
    st.sidebar.caption(f"라벨: {', '.join(rp.label_paths) or '없음'}")

    skey = f"topoedit::{sel}"
    if skey not in st.session_state or st.sidebar.button("↺ 이 도면 다시 로드"):
        dr, png = load_plan(rp)
        st.session_state[skey] = {"dr": dr, "png": png,
                                  "units": segment_units(dr), "states": {}, "draw": {}}
    bundle = st.session_state[skey]
    dr, png, units = bundle["dr"], bundle["png"], bundle["units"]
    if not units:
        st.warning("세대 분리 결과 없음(방 부족).")
        return

    def _uid(k):
        return f"{rp.plan_id}_u{min(units[k])}"

    ui = st.sidebar.selectbox(
        f"세대 ({len(units)})", range(len(units)),
        format_func=lambda k: f"세대 {k + 1} · {len(units[k])}방 · "
                              f"[{led.get(_uid(k), {}).get('status', '미검수')}]")
    unit_id = _uid(ui)
    states = bundle["states"]
    if unit_id not in states or st.sidebar.button("↺ 이 세대 초기화"):
        svg = load_svg_text(unit_id)
        states[unit_id] = (state_from_svg(svg, dr, unit_id, rp.house) if svg
                           else init_state(dr, unit_id, rp.house, units[ui]))
        bundle["draw"].pop(unit_id, None)
    stt = states[unit_id]
    draw = _draw_buf(bundle, unit_id)

    col1, col2 = st.columns([3, 2])
    with col1:
        if draw["active"] and _img_coords is not None:
            st.info(f"🖊 **{draw['base']}** 그리는 중 — 영역 모서리를 차례로 클릭 "
                    f"(점 {len(draw['pts'])}개, 3개 이상이면 완성 가능)")
            img, (cx0, cy0, ncw, nch) = crop_overlay_image(dr, png, stt, draw["pts"])
            if img is not None:
                val = _img_coords(img, key=f"ic_{unit_id}")
                if val and val.get("x") is not None:
                    if (val["x"], val["y"]) != draw["last"]:
                        draw["pts"].append((cx0 + (val["x"] / img.width) * ncw,
                                            cy0 + (val["y"] / img.height) * nch))
                        draw["last"] = (val["x"], val["y"])
                        _rerun(st)
        else:
            st.pyplot(render_figure(dr, png, stt))

    with col2:
        st.markdown("**➍ 연결공간 그리기** (복도·전실 영역 → 겹친 방서 차감·면적)")
        if _img_coords is None:
            st.warning("streamlit-image-coordinates 미설치 — 그리기 불가")
        elif not draw["active"]:
            draw["base"] = st.selectbox("종류", CONNECTOR_BASES, key="db")
            if st.button("🖊 그리기 시작", use_container_width=True):
                draw["active"] = True
                draw["pts"] = []
                draw["last"] = None
                _rerun(st)
        else:
            d1, d2, d3 = st.columns(3)
            if d1.button("↩ 점취소") and draw["pts"]:
                draw["pts"].pop()
                _rerun(st)
            if d2.button("✓ 완성"):
                if len(draw["pts"]) >= 3:
                    add_drawn_region(stt, draw["base"], draw["pts"], dr)
                draw["active"] = False
                draw["pts"] = []
                draw["last"] = None
                _rerun(st)
            if d3.button("✗ 취소"):
                draw["active"] = False
                draw["pts"] = []
                draw["last"] = None
                _rerun(st)

        st.markdown("**➋ 연결(엣지)** — 문은 저장 시 자동추출, open/corridor만 수동")
        a = st.selectbox("A", list(stt.nodes),
                         format_func=lambda i: f"{i}:{stt.nodes[i].role}", key="ea")
        b = st.selectbox("B", list(stt.nodes),
                         format_func=lambda i: f"{i}:{stt.nodes[i].role}", key="eb")
        via = st.selectbox("종류", VIA_KINDS, key="ev")
        if st.button("+ 연결", use_container_width=True):
            if add_edge(stt, a, b, via):
                _rerun(st)
        for e in list(stt.edges):
            c1, c2 = st.columns([5, 1])
            c1.text(f"{e['a']}:{stt.nodes[e['a']].role} — "
                    f"{e['b']}:{stt.nodes[e['b']].role}  ({e['via']})")
            if c2.button("✕", key=f"de_{e['a']}_{e['b']}"):
                remove_edge(stt, e["a"], e["b"])
                _rerun(st)

    st.markdown("---")
    st.markdown("**➌ 역할 지정** (기구🛁·면적 보고 욕실/화장실·안방 등 세분)")
    cols = st.columns(4)
    for i, (nid, n) in enumerate(list(stt.nodes.items())):
        with cols[i % 4]:
            fx = f" 🛁{','.join(n.fixtures)}" if n.fixtures else ""
            ar = f" · {n.area_px:,.0f}px²" if n.area_px else ""
            newr = st.selectbox(
                f"{nid} ({n.base}){fx}{ar}", ROLES,
                index=ROLES.index(n.role) if n.role in ROLES else ROLES.index("기타"),
                key=f"role_{nid}")
            if newr != n.role:
                set_role(stt, nid, newr)
            if n.source == "human" and st.button("삭제", key=f"rm_{nid}"):
                remove_node(stt, nid)
                _rerun(st)

    st.markdown("---")
    if st.button("🔎 위상 추출 미리보기 (SVG→그래프)"):
        regions, doors, links = parse_svg(to_svg(stt, dr))
        G = extract_topology(regions, doors, links)
        nd = sum(1 for *_x, v in G.edges(data="via") if v == "door")
        st.info(f"추출: 노드 {G.number_of_nodes()} · 엣지 {G.number_of_edges()} "
                f"(문 {nd} · 수동 {G.number_of_edges() - nd})")

    note = st.text_input("메모")
    status = st.selectbox("상태", STATUSES, index=STATUSES.index("검증완료"))
    if st.button("💾 저장 (SVG gold)", type="primary"):
        p = save_svg(stt, dr, status=status, curator="admin", notes=note)
        nreg = sum(1 for n in stt.nodes.values() if n.polygon is not None)
        st.success(f"저장: {p.name} · 영역 {nreg}개")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", nargs="?", default=str(config.DATA_DIR / "raw" / "linked_demo"))
    a = ap.parse_args()
    for rp in scan_dir(a.dir):
        dr, png = load_plan(rp)
        units = segment_units(dr)
        print(f"{rp.plan_id}  labels={list(rp.label_paths)}  "
              f"rooms={len(dr.rooms)} doors={len(dr.doors)} objects={len(dr.objects)}  "
              f"세대 {len(units)}개: {[len(u) for u in units]}")
