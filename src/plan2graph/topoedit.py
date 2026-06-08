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
# 그리기로 신설 가능한 공간 종류 — 연결공간(복도·전실)뿐 아니라 빠지거나 잘못된 방도 직접 추가.
DRAW_BASES = ("복도", "전실", "발코니", "다목적공간", "실외기실", "드레스룸", "파우더룸",
              "기타", "거실", "주방", "침실", "화장실", "현관")
VIA_KINDS = ("door", "open")   # via 도메인(자동 도출값). door=탐지된 문 사이, open=문 없음.
#   사람이 고르지 않음 — derive_via가 문 테이블 조인으로 결정(corridor/entrance는 폐기).
STATUSES = ("미검수", "검증완료", "모호", "제외")
# 사람이 지정하는 역할(라벨 base에서 세분) — 욕실/화장실, 안방/침실, 전용 등
ROLES = ("거실", "주방", "현관", "침실", "안방", "화장실", "욕실", "전용화장실",
         "전용욕실", "드레스룸", "파우더룸", "발코니", "실외기실", "다목적공간", "복도", "전실",
         "기타", "구조물", "실외", "엘리베이터홀", "계단실", "엘리베이터")

ROLE_COLOR = {
    "침실": "#6CA6E8", "안방": "#1f6fd6", "거실": "#E8453C", "주방": "#F2A900",
    "화장실": "#9ACD66", "욕실": "#3a9a3a", "전용욕실": "#1a7a4a", "전용화장실": "#7ab04a",
    "현관": "#d9534f", "발코니": "#2DBE60", "드레스룸": "#a070d0", "복도": "#9aa0a6",
    "파우더룸": "#d08ac0", "전실": "#c0c4c8", "기타": "#bcbcbc",
    "실외기실": "#9cc8e8", "다목적공간": "#fdc08a",
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


def sheet_scale_info(plan_id: str):
    """scale 보정 결과(scale.csv)에서 이 시트 축척 조회.
    반환 {mm_per_px, confidence, bedroom_med_m2} 또는 None. (scale 보정 ↔ 편집기 통합)"""
    try:
        from plan2graph import scale_ocr
        row = scale_ocr.load_scale_csv().get(plan_id)
    except Exception:  # noqa: BLE001
        return None
    if not row:
        return None
    s = row.get("scale_mm_per_px")
    try:
        mm = float(s) if s not in (None, "") else None
    except (TypeError, ValueError):
        mm = None
    return {"mm_per_px": mm, "confidence": row.get("confidence", ""),
            "bedroom_med_m2": row.get("bedroom_med_m2", "")}


def load_plan(rp: RawPlan, scale: float | None = config.DEFAULT_SCALE):
    """RawPlan → (Drawing, png_bytes). 위상 그래프는 만들지 않는다(사람 몫).
    scale 보정값(scale.csv, confidence=ok)이 있으면 dr.scale(m/px) 설정 → 면적 m²."""
    docs = []
    for label in ("SPA", "STR", "OBJ", "OCR"):
        jp = rp.label_paths.get(label)
        if jp:
            docs.append(load_coco(jp))
    dr = assemble_drawing(docs, image_path=rp.png_path, scale=scale)
    info = sheet_scale_info(rp.plan_id)
    if info and info["mm_per_px"] and info["confidence"] == "ok":
        dr.scale = info["mm_per_px"] / 1000.0     # mm/px → m/px
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


def _infer_door_edges(dr: Drawing, nodes: dict, gap: float = 60.0) -> list:
    """문마다 가장 가까운 2개 방 → 기본 연결선(편집 출발점). source=auto.
    복도 신설 전이라 일부는 방-방(틀림)이지만, 사람이 삭제·재연결하는 토대."""
    from shapely.geometry import Point
    items = [(nid, n.polygon) for nid, n in nodes.items() if n.polygon is not None]
    edges, seen = [], set()
    for d in dr.doors:
        if not d.centroid:
            continue
        p = Point(*d.centroid)
        cand = sorted(((poly.distance(p), nid) for nid, poly in items), key=lambda t: t[0])
        cand = [(dist, nid) for dist, nid in cand if dist <= gap][:2]
        if len(cand) == 2 and cand[0][1] != cand[1][1]:
            key = frozenset((cand[0][1], cand[1][1]))
            if key not in seen:
                seen.add(key)
                edges.append({"a": cand[0][1], "b": cand[1][1],
                              "via": "door", "source": "auto"})
    return edges


def init_state(dr: Drawing, plan_id: str, house: str,
               room_ids=None) -> State:
    """원시 방 = 노드. 엣지 = 문 기반 기본 연결(편집 출발점). 기구는 보조정보로 귀속.
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
    return State(plan_id=plan_id, house=house, nodes=nodes,
                 edges=_infer_door_edges(dr, nodes))


def suggest_roles(st: State, dr: Drawing) -> dict:
    """라벨만이 아니라 **이름(OCR)·기구(OBJ)·면적** 신호로 역할 자동 제안 {nid: role}.
    위상(연결)이 아니라 속성이라 자동 추정 OK — 사람이 확정. 신호 없으면 제안 없음.
    - OCR 이름(폴리곤 내 텍스트)이 최강: '안방'·'욕실'·'드레스' 등 직접 매핑
    - 기구: 욕조→욕실, 싱크대/가스레인지→주방, 변기만→화장실
    - 면적: 가장 큰 침실→안방, 아주 작은 기타→구조물(기둥/배관)
    """
    from shapely.geometry import Point
    NAME = (("안방", "안방"), ("드레스", "드레스룸"), ("파우더", "파우더룸"),
            ("부부욕실", "전용욕실"),
            ("부부", "전용욕실"), ("욕실", "욕실"), ("화장실", "화장실"),
            ("주방", "주방"), ("식당", "주방"), ("거실", "거실"), ("현관", "현관"),
            ("발코니", "발코니"), ("복도", "복도"), ("다용도", "다목적공간"),
            ("실외기", "실외기실"), ("침실", "침실"))
    texts = [(t.ocr_text, t.centroid) for t in getattr(dr, "texts", [])
             if getattr(t, "ocr_text", None) and t.centroid]
    beds = [(n.area_px, nid) for nid, n in st.nodes.items()
            if n.base == "침실" and n.polygon is not None]
    master = max(beds)[1] if beds else None
    sc = getattr(dr, "scale", None)
    out = {}
    for nid, n in st.nodes.items():
        if n.polygon is None:
            continue
        sug = None
        for txt, c in texts:                              # 1) OCR 이름
            try:
                if n.polygon.contains(Point(*c)):
                    for kw, role in NAME:
                        if kw in txt:
                            sug = role
                            break
            except Exception:
                pass
            if sug:
                break
        if sug is None:                                   # 2) 기구
            if any("욕조" in f for f in n.fixtures):
                sug = "욕실"
            elif any(f in ("싱크대", "가스레인지") for f in n.fixtures):
                sug = "주방"
        if sug is None:                                   # 3) 면적
            if nid == master and n.base == "침실":
                sug = "안방"
            elif n.base == "기타":
                m2 = (n.area_px * sc * sc) if sc else None
                if (m2 is not None and m2 < 1.0) or (m2 is None and n.area_px < 5000):
                    sug = "구조물"
        if sug and sug != n.role:
            out[nid] = sug
    return out


def add_edge(st: State, a: int, b: int, via: str) -> bool:
    if a == b or a not in st.nodes or b not in st.nodes:
        return False
    for e in st.edges:
        if {e["a"], e["b"]} == {a, b}:
            e["via"] = via
            return True
    st.edges.append({"a": a, "b": b, "via": via, "source": "human"})
    return True


def derive_via(st: State, dr: Drawing, a: int, b: int, gap: float = 60.0) -> str:
    """노드 a-b 사이에 **탐지된 문**이 있으면 'door', 없으면 'open'으로 도출.
    문(종류 포함)은 이미 탐지·표시돼 있으니 사람이 고르지 않는다(관계만 선언, 종류는 조인).
    extract_topology의 문→엣지 로직과 동일 기준(문의 최근접 2영역 == {a,b})."""
    from shapely.geometry import Point
    regions = [(nid, n.polygon) for nid, n in st.nodes.items() if n.polygon is not None]
    for d in getattr(dr, "doors", []):
        if not d.centroid:
            continue
        p = Point(*d.centroid)
        cand = sorted(((poly.distance(p), nid) for nid, poly in regions),
                      key=lambda t: t[0])
        cand = [nid for dist, nid in cand if dist <= gap][:2]
        if len(cand) == 2 and set(cand) == {a, b}:
            return "door"
    return "open"


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
    sc = getattr(dr, "scale", None)           # m/px (scale 보정에서 옴)
    smm = round(sc * 1000, 4) if sc else ""    # mm/px
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'viewBox="0 0 {W} {H}" data-schema="{SVG_SCHEMA}" data-plan="{st.plan_id}" '
           f'data-house="{st.house}" data-scale-mm-per-px="{smm}">']
    for n in st.nodes.values():
        if n.polygon is None:
            continue
        pts = " ".join(f"{round(x, 1)},{round(y, 1)}"
                       for x, y in n.polygon.exterior.coords)
        m2 = f"{n.area_px * sc * sc:.2f}" if sc else ""
        out.append(
            f'  <polygon points="{pts}" data-id="{n.id}" data-base="{n.base}" '
            f'data-role="{n.role}" data-source="{n.source}" '
            f'data-area-px="{round(n.area_px, 1)}" data-area-m2="{m2}" '
            f'fill="{ROLE_COLOR.get(n.role, "#ccc")}" '
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


def write_svg(st: State, dr: Drawing) -> Path:
    """SVG 파일만 기록(자동저장용 — ledger/상태 미기록)."""
    REC_DIR.mkdir(parents=True, exist_ok=True)
    p = REC_DIR / f"{st.plan_id}.svg"
    p.write_text(to_svg(st, dr), encoding="utf-8")
    return p


def save_svg(st: State, dr: Drawing, *, status: str = "검증완료",
             curator: str = "", notes: str = "", ts: str | None = None) -> Path:
    """SVG 기록 + 상태(ledger) 기록(명시 저장·검증완료 표시용)."""
    p = write_svg(st, dr)
    set_status(st.plan_id, status, curator=curator, notes=notes,
               ts=ts or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return p


def delete_record(unit_id: str) -> None:
    """저장 SVG 삭제(처음부터 다시 시작용)."""
    p = REC_DIR / f"{unit_id}.svg"
    if p.exists():
        p.unlink()


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


def _disp_id(nid) -> str:
    """표시용 짧은 id — 라벨 방=원본 idx(0~), 연결공간(100000+)=C1,C2… (긴 숫자 숨김)."""
    return f"C{nid - 99999}" if isinstance(nid, int) and nid >= 100000 else str(nid)


def _area_label(area_px: float, dr) -> str:
    """축척(dr.scale=m/px) 있으면 ㎡, 없으면 px². m²=area_px×scale²."""
    sc = getattr(dr, "scale", None)
    if sc:
        return f"{area_px * sc * sc:.1f}㎡"
    return f"{area_px:,.0f}px²"


def _poly_patch(poly, **kw):
    """shapely Polygon(구멍 포함) → matplotlib PathPatch(holes 투명하게)."""
    from matplotlib.path import Path as _MP
    from matplotlib.patches import PathPatch
    verts, codes = [], []
    for ring in [poly.exterior, *poly.interiors]:
        cs = list(ring.coords)
        if len(cs) < 3:
            continue
        verts += cs
        codes += [_MP.MOVETO] + [_MP.LINETO] * (len(cs) - 2) + [_MP.CLOSEPOLY]
    return PathPatch(_MP(verts, codes), **kw)


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
    # 방 폴리곤 (구멍=carve된 연결공간 자리는 투명하게 보이도록 PathPatch)
    for n in st.nodes.values():
        if n.polygon is not None and n.polygon.geom_type == "Polygon":
            try:
                ax.add_patch(_poly_patch(
                    n.polygon, facecolor=ROLE_COLOR.get(n.role, "#ccc"),
                    alpha=0.65 if n.id == highlight else 0.4,
                    edgecolor="red" if n.id == highlight else "#333",
                    lw=2.5 if n.id == highlight else 0.7, zorder=2))
            except Exception:
                pass
    # 연결공간(폴리곤 없음) = 다이아몬드 마커
    for n in st.nodes.values():
        if n.polygon is None:
            ax.plot(n.cx, n.cy, "D", ms=12, zorder=5,
                    color=ROLE_COLOR.get(n.role, "#888"), mec="#222")
    # 라벨(짧은 id·역할)
    for n in st.nodes.values():
        ax.text(n.cx, n.cy, f"{_disp_id(n.id)}\n{n.role}", fontsize=7, ha="center",
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


_PIL_FONT = None


def _pil_font():
    """PIL ImageDraw용 한글 폰트(없으면 기본). 기본폰트는 latin-1뿐이라 한글 크래시→TTF 필수."""
    global _PIL_FONT
    if _PIL_FONT is None:
        from PIL import ImageFont
        for p in (ROOT / "fonts" / "NanumGothic.ttf",
                  Path.home() / ".fonts" / "NanumGothic.ttf",
                  Path("C:/Windows/Fonts/malgun.ttf"),
                  Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
                  Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")):
            try:
                if p.exists():
                    _PIL_FONT = ImageFont.truetype(str(p), 13)
                    break
            except Exception:
                continue
        if _PIL_FONT is None:
            _PIL_FONT = ImageFont.load_default()
    return _PIL_FONT


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


def bake_background(dr: Drawing, png: bytes, st: State, max_w: int = 900,
                    margin: int = 90, highlight=None, highlight_edge=None):
    """캔버스 배경 = 원본 크롭 + 영역(반투명 색박스) + 연결선 + 노드(동그라미+id).
    반환 (PIL RGB, (x0,y0,native_w,native_h), (disp_w,disp_h)). 클릭→원본 좌표 매핑용.
    highlight=강조할 노드 id(연결 시 선택된 A를 빨갛게).
    highlight_edge=(a,b) 강조할 연결선(선 클릭으로 선택→자홍색 halo·삭제대상)."""
    from PIL import Image, ImageDraw
    if not png:
        return None, (0, 0, 0, 0), (0, 0)
    img = Image.open(io.BytesIO(png)).convert("RGB")
    xs, ys = [], []
    for n in st.nodes.values():
        if n.polygon is not None:
            a, b, c, d = n.polygon.bounds
            xs += [a, c]
            ys += [b, d]
    if xs:
        x0 = max(0, int(min(xs) - margin))
        y0 = max(0, int(min(ys) - margin))
        x1 = min(img.width, int(max(xs) + margin))
        y1 = min(img.height, int(max(ys) + margin))
    else:
        x0, y0, x1, y1 = 0, 0, img.width, img.height
    crop = img.crop((x0, y0, x1, y1)).convert("RGBA")
    ov = Image.new("RGBA", crop.size, (0, 0, 0, 0))
    dd = ImageDraw.Draw(ov)
    R = 15
    # 1) 영역 색박스
    for n in st.nodes.values():
        if n.polygon is None:
            continue
        pts = [(x - x0, y - y0) for x, y in n.polygon.exterior.coords]
        if len(pts) >= 3:
            dd.polygon(pts, fill=_hex_rgba(ROLE_COLOR.get(n.role, "#cccccc"), 90),
                       outline=(40, 40, 40, 255))
    # 2) 노드 동그라미(글자 없이)
    for n in st.nodes.values():
        cx, cy = n.cx - x0, n.cy - y0
        hl = (n.id == highlight)
        dd.ellipse([cx - R, cy - R, cx + R, cy + R],
                   fill=_hex_rgba(ROLE_COLOR.get(n.role, "#cccccc"), 255),
                   outline=(230, 30, 30, 255) if hl else (40, 40, 40, 255),
                   width=5 if hl else 2)
    # 3) 연결선 — 동그라미 위에(via별 색·흰 테두리)
    _EC = {"door": (21, 101, 192, 255), "open": (242, 169, 0, 255),
           "corridor": (120, 120, 120, 255), "entrance": (200, 0, 0, 255)}
    _hl_set = set(highlight_edge) if highlight_edge else None
    for e in st.edges:
        a, b = st.nodes.get(e["a"]), st.nodes.get(e["b"])
        if a and b:
            p1, p2 = (a.cx - x0, a.cy - y0), (b.cx - x0, b.cy - y0)
            if _hl_set is not None and {e["a"], e["b"]} == _hl_set:
                dd.line([p1, p2], fill=(255, 0, 255, 255), width=12)  # 선택 halo
            dd.line([p1, p2], fill=(255, 255, 255, 235), width=7)
            dd.line([p1, p2], fill=_EC.get(e.get("via"), (20, 20, 20, 255)), width=4)
    # 4) id·역할 글자 — 맨 위(연결선 위에 읽히게)
    for n in st.nodes.values():
        cx, cy = n.cx - x0, n.cy - y0
        try:
            dd.text((cx, cy), _disp_id(n.id), fill=(255, 255, 255, 255),
                    font=_pil_font(), anchor="mm")
            dd.text((cx, cy + R + 2), n.role, fill=(20, 20, 20, 255),
                    font=_pil_font(), anchor="ma")
        except Exception:
            pass
    out = Image.alpha_composite(crop, ov).convert("RGB")
    nw, nh = x1 - x0, y1 - y0
    if out.width > max_w:
        out = out.resize((max_w, max(1, int(out.height * max_w / out.width))))
    return out, (x0, y0, nw, nh), (out.width, out.height)


def _cv_to_orig(x, y, mp):
    """캔버스 표시좌표 → 원본 px. mp=(x0,y0,native_w,native_h,disp_w,disp_h)."""
    x0, y0, nw, nh, dw, dh = mp
    return (x0 + (x / dw) * nw, y0 + (y / dh) * nh)


def parse_canvas(json_data, mp):
    """fabric objects → (regions: 폴리곤 꼭짓점들[원본px], lines: [(p1,p2)원본px]).
    rect=드래그 박스, path/polygon=점찍기, line=연결선."""
    regions, lines = [], []
    for o in (json_data or {}).get("objects", []):
        t = o.get("type")
        if t == "rect":
            L, T = o.get("left", 0), o.get("top", 0)
            W = o.get("width", 0) * o.get("scaleX", 1)
            H = o.get("height", 0) * o.get("scaleY", 1)
            regions.append([_cv_to_orig(x, y, mp) for x, y in
                            ((L, T), (L + W, T), (L + W, T + H), (L, T + H))])
        elif t in ("path", "polygon"):
            pts = []
            if o.get("path"):
                for seg in o["path"]:
                    if len(seg) >= 3 and isinstance(seg[1], (int, float)):
                        pts.append((seg[1], seg[2]))
            elif o.get("points"):
                L, T = o.get("left", 0), o.get("top", 0)
                pts = [(L + p.get("x", 0), T + p.get("y", 0)) for p in o["points"]]
            if len(pts) >= 3:
                regions.append([_cv_to_orig(x, y, mp) for x, y in pts])
        elif t == "line":
            lines.append((_cv_to_orig(o.get("x1", 0), o.get("y1", 0), mp),
                          _cv_to_orig(o.get("x2", 0), o.get("y2", 0), mp)))
    return regions, lines


def _nearest_node(st: State, pt):
    """원본좌표 pt에서 가장 가까운 영역 노드 id(연결선 끝→방 매핑)."""
    from shapely.geometry import Point
    p = Point(*pt)
    best, bd = None, 1e18
    for nid, n in st.nodes.items():
        if n.polygon is not None:
            dd = n.polygon.distance(p)
            if dd < bd:
                best, bd = nid, dd
    return best


def _nearest_edge(st: State, pt, t_lo=0.1, t_hi=0.9):
    """원본좌표 pt에서 가장 가까운 연결선(엣지)과 그 수직거리(원본px). 선 클릭→삭제 선택용.
    선의 양 끝(노드 동그라미)은 제외 — 투영비 t가 [t_lo,t_hi](기본 중앙 80%)인 선만
    후보. 동그라미 근처 클릭은 엣지가 아니라 노드선택(연결)으로 빠지게 한다.
    (5%는 짧은 선에서 끝 영역이 좁아 동그라미를 눌러도 선이 잡혀 10% 유지.)"""
    px, py = pt
    best, bd = None, 1e18
    for e in st.edges:
        a, b = st.nodes.get(e["a"]), st.nodes.get(e["b"])
        if not (a and b):
            continue
        dx, dy = b.cx - a.cx, b.cy - a.cy
        L2 = dx * dx + dy * dy
        if L2 == 0:
            continue
        t = ((px - a.cx) * dx + (py - a.cy) * dy) / L2
        if t < t_lo or t > t_hi:           # 양 끝(동그라미) 근처 → 엣지 후보 제외
            continue
        qx, qy = a.cx + t * dx, a.cy + t * dy
        d = ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5
        if d < bd:
            best, bd = e, d
    return best, bd


def _overlay_clicks(bg, pts, mp, mode):
    """배경 이미지 위에 현재 클릭점(빨강) + 연결선 미리보기를 그려 반환(원본 px → 표시 px)."""
    from PIL import ImageDraw
    x0, y0, nw, nh, dw, dh = mp
    im = bg.copy()
    d = ImageDraw.Draw(im)
    dp = [((ox - x0) / nw * dw, (oy - y0) / nh * dh) for ox, oy in pts]
    if mode == "polygon" and len(dp) >= 2:
        d.line(dp, fill=(230, 30, 30), width=2)
    if mode in ("rect", "line") and len(dp) == 1:
        pass
    for px, py in dp:
        d.ellipse([px - 5, py - 5, px + 5, py + 5], fill=(230, 30, 30))
    return im


# ─────────────────────────────────────────────────────────────────────────────
# Streamlit 화면 (admin.py에서 호출)
# ─────────────────────────────────────────────────────────────────────────────
def _rerun(st_mod) -> None:
    (getattr(st_mod, "rerun", None) or st_mod.experimental_rerun)()


def _patch_canvas_image_to_url() -> None:
    """drawable-canvas 0.9.3 ↔ streamlit 1.58 호환 shim.
    streamlit이 elements.image.image_to_url를 제거(→lib.image_utils, 시그니처도 변경)해
    캔버스가 죽음. 옛 시그니처로 데이터URI 반환하는 함수를 복원한다(멱등)."""
    try:
        import streamlit.elements.image as _img
    except Exception:
        return
    if hasattr(_img, "image_to_url"):
        return

    def image_to_url(image, width=-1, clamp=False, channels="RGB",
                     output_format="PNG", image_id=""):
        # 1순위: 새 streamlit 함수에 위임 → 정식 /media/ URL(캔버스 프런트가 로드).
        try:
            from streamlit.elements.lib.image_utils import image_to_url as _new
            from streamlit.elements.lib.layout_utils import LayoutConfig
            return _new(image, LayoutConfig(), clamp, channels, output_format, image_id)
        except Exception:
            pass
        # 폴백: 데이터 URI(런타임 밖 등)
        import base64
        from PIL import Image as _PILImage
        im = image
        if not isinstance(im, _PILImage.Image):
            try:
                import numpy as _np
                im = (_PILImage.fromarray(im) if isinstance(im, _np.ndarray)
                      else _PILImage.open(im))
            except Exception:
                return ""
        b = io.BytesIO()
        im.save(b, format="PNG")
        return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()

    _img.image_to_url = image_to_url


def _draw_buf(bundle, unit_id):
    return bundle["draw"].setdefault(
        unit_id, {"active": False, "base": "복도", "pts": [], "last": None})


def render_editor() -> None:
    import streamlit as st
    try:                                  # drawable-canvas는 streamlit 1.58 프런트 비호환(배경 404)
        from streamlit_image_coordinates import streamlit_image_coordinates as _img_coords
    except Exception:  # noqa: BLE001
        _img_coords = None

    st.title("✏️ 위상 편집")
    st.caption("원본 위에 영역(반투명 박스)을 그려 완전 기하 → 위상은 결정적 추출. "
               "그리기·연결·역할은 **자동 저장**. (좌측 앱 메뉴는 접어도 됨)")

    with st.expander("⚙ 데이터 소스", expanded=False):
        src = st.text_input("라벨 디렉터리",
                            value=str(config.DATA_DIR / "raw" / "linked_demo"))
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

    # ── 상단 바: 도면·세대·상태·액션 (사이드바 아님 → 메뉴 접어도 동작) ──
    t1, t2, t3, t4 = st.columns([3, 3, 3, 2])
    sel = t1.selectbox(f"도면 ({len(ids)})", ids)
    rp = next(p for p in plans if p.plan_id == sel)
    skey = f"topoedit::{sel}"
    if skey not in st.session_state:
        dr0, png0 = load_plan(rp)
        st.session_state[skey] = {"dr": dr0, "png": png0,
                                  "units": segment_units(dr0), "states": {}}
    bundle = st.session_state[skey]
    dr, png, units = bundle["dr"], bundle["png"], bundle["units"]
    if not units:
        st.warning("세대 분리 결과 없음(방 부족).")
        return

    def _uid(k):
        return f"{rp.plan_id}_u{min(units[k])}"

    ui = t2.selectbox(
        f"세대 ({len(units)})", range(len(units)),
        format_func=lambda k: f"세대 {k + 1} · {len(units[k])}방 · "
                              f"[{led.get(_uid(k), {}).get('status', '미검수')}]")
    unit_id = _uid(ui)
    states = bundle["states"]
    if unit_id not in states:
        svg = load_svg_text(unit_id)
        states[unit_id] = (state_from_svg(svg, dr, unit_id, rp.house) if svg
                           else init_state(dr, unit_id, rp.house, units[ui]))
    stt = states[unit_id]

    nroom = sum(1 for n in stt.nodes.values() if n.base not in CONNECTOR_BASES)
    nconn = len(stt.nodes) - nroom
    if getattr(dr, "scale", None):
        tot_m2 = sum(n.area_px for n in stt.nodes.values()
                     if n.polygon is not None) * dr.scale * dr.scale
        t3.metric("방·연결·엣지 · 면적", f"{nroom}·{nconn}·{len(stt.edges)} · {tot_m2:.0f}㎡")
    else:
        t3.metric("방 · 연결공간 · 엣지", f"{nroom} · {nconn} · {len(stt.edges)}")
    if t4.button("💾 검증완료", use_container_width=True):
        save_svg(stt, dr, status="검증완료", curator="admin")
        st.toast("검증완료로 저장")
    if t4.button("🗑 처음부터", use_container_width=True):
        delete_record(unit_id)
        states.pop(unit_id, None)
        _rerun(st)

    # ── 도구(가로 라디오) — 상단 ──
    tool = st.radio("도구", ["👁 보기", "✒️ 영역 그리기", "🔗 연결", "🏷 역할", "📏 면적보정"],
                    horizontal=True, key=f"tool_{unit_id}")

    bg, mp_xy, (dw, dh) = bake_background(dr, png, stt)
    if bg is None:
        st.warning("배경 생성 실패(방 없음).")
        return
    mp = (mp_xy[0], mp_xy[1], mp_xy[2], mp_xy[3], dw, dh)

    # ── 도면(좌, 크게) · 컨트롤(우 패널) ──
    canvas_col, panel = st.columns([4, 1])

    if tool.startswith("👁"):
        with canvas_col:
            st.image(bg)
        if panel.button("🔎 위상 추출 미리보기", use_container_width=True):
            regions, doors, links = parse_svg(to_svg(stt, dr))
            G = extract_topology(regions, doors, links)
            ndd = sum(1 for *_x, v in G.edges(data="via") if v == "door")
            panel.info(f"노드 {G.number_of_nodes()} · 엣지 {G.number_of_edges()} "
                       f"(문 {ndd}·수동 {G.number_of_edges() - ndd})")
    elif tool.startswith("🏷"):
        buf = bundle.setdefault("clk", {}).setdefault(
            unit_id, {"pts": [], "last": None, "sel": None})
        rsel = buf.get("role_sel")
        if rsel is not None and rsel not in stt.nodes:
            rsel = buf["role_sel"] = None
        # 캔버스 — 동그라미(노드) 클릭으로 선택, 선택 노드 빨강 강조(연결과 동일 패턴)
        if _img_coords is not None:
            with canvas_col:
                disp = bake_background(dr, png, stt, highlight=rsel)[0]
                val = _img_coords(disp, key=f"ic_{unit_id}_role")
            if (val and val.get("x") is not None
                    and (val["x"], val["y"]) != buf.get("role_last")):
                buf["role_last"] = (val["x"], val["y"])
                node = _nearest_node(stt, _cv_to_orig(val["x"], val["y"], mp))
                if node is not None:
                    buf["role_sel"] = node
                    buf["rs_synced"] = None          # 상단 selectbox 재동기화 유도
                _rerun(st)
        else:
            with canvas_col:
                st.image(bg)
        # 상단: 선택 노드 역할 변경/삭제 (연결의 '선택 삭제'처럼). 역할변경은 on_change
        # 콜백으로 — selectbox key 함정(외부 변경 되돌림) 회피. stt는 session_state 영속.
        top_key = f"rtop_{unit_id}"
        if rsel is not None:
            n = stt.nodes[rsel]
            if buf.get("rs_synced") != rsel:         # 선택 바뀜 → 위젯을 현재 역할로 동기화
                st.session_state[top_key] = n.role
                buf["rs_synced"] = rsel

            def _apply_top_role(_nid=rsel):
                set_role(stt, _nid, st.session_state[top_key])
                write_svg(stt, dr)
            head = f"**선택: {_disp_id(rsel)} · {n.role}**"
            if n.area_px:
                head += f" · {_area_label(n.area_px, dr)}"
            panel.markdown(head)
            cc1, cc2 = panel.columns([3, 1], vertical_alignment="bottom")
            cc1.selectbox("역할 변경", ROLES, key=top_key, on_change=_apply_top_role)
            if cc2.button("🗑", key=f"rtopdel_{unit_id}", use_container_width=True,
                          help="이 노드 삭제(도형 자체가 틀렸을 때)"):
                remove_node(stt, rsel)
                buf["role_sel"] = None
                write_svg(stt, dr)
                _rerun(st)
            panel.markdown("---")
        else:
            panel.info("도면의 **동그라미(노드) 클릭** 또는 아래 목록에서 선택 → 위에서 역할 변경")
        # 자동 역할 제안
        sug = suggest_roles(stt, dr)
        if sug:
            if panel.button(f"🤖 자동 역할 제안 적용 ({len(sug)}개)",
                            use_container_width=True):
                for k, v in sug.items():
                    set_role(stt, k, v)
                buf["rs_synced"] = None              # 선택노드 역할 바뀌었을 수 있음
                write_svg(stt, dr)
                _rerun(st)
            panel.caption("제안(이름·기구·면적): "
                          + ", ".join(f"{_disp_id(k)}→{v}" for k, v in list(sug.items())[:10]))
        else:
            panel.caption("자동 제안 없음(신호와 이미 일치 / 신호 부족)")
        # 노드 목록 — 읽기전용 정보 테이블(선택·변경·삭제는 도면 클릭→위에서). 🔹=선택중.
        import pandas as pd
        panel.caption(f"노드 {len(stt.nodes)}개 — 도면에서 클릭해 선택 → 위에서 역할변경/삭제")
        rrows = [{
            "": "🔹" if nid == rsel else "",
            "id": _disp_id(nid),
            "역할": n.role,
            "면적": _area_label(n.area_px, dr) if n.area_px else "",
            "기구": ",".join(n.fixtures) if n.fixtures else "",
        } for nid, n in stt.nodes.items()]
        panel.dataframe(pd.DataFrame(rrows), hide_index=True,
                        use_container_width=True)
    elif tool.startswith("📏"):                            # 면적보정(scale)
        with canvas_col:
            st.image(bg)
        info = sheet_scale_info(rp.plan_id)
        cur_mm = ((dr.scale * 1000) if getattr(dr, "scale", None)
                  else (info["mm_per_px"] if info else None))
        panel.caption(
            f"축척 **{round(cur_mm, 3) if cur_mm else '—'} mm/px**\n\n"
            f"{(info['confidence'] if info else '없음')} · 침실중앙 "
            f"{(info.get('bedroom_med_m2') or '—') if info else '—'}㎡\n\n"
            f"{'면적 ㎡ 적용중' if getattr(dr, 'scale', None) else 'scale 없어 px²'}")
        new_mm = panel.number_input("scale 수동(mm/px)", min_value=0.0,
                                    value=float(cur_mm) if cur_mm else 0.0,
                                    step=0.1, format="%.4f", key=f"mm_{unit_id}")
        if panel.button("적용", use_container_width=True, key=f"asc_{unit_id}"):
            from plan2graph import scale_ocr
            ok = new_mm > 0
            scale_ocr.update_scale_row(rp.plan_id, new_mm if ok else None,
                                       "ok" if ok else "quarantined", "manual")
            bundle["dr"].scale = (new_mm / 1000.0) if ok else None
            st.toast("축척 저장")
            _rerun(st)
        if panel.button("📍 OCR 추정", use_container_width=True, key=f"ocr_{unit_id}"):
            import types
            from plan2graph import scale_ocr
            with st.spinner("치수선 OCR 추정 중..."):
                st.session_state[f"est_{unit_id}"] = scale_ocr.estimate_scale(
                    types.SimpleNamespace(png_bytes=png, dr=dr))
        est = st.session_state.get(f"est_{unit_id}")
        if est:
            panel.caption(f"OCR 추정 **{est.get('scale_mm_per_px')} mm/px** · "
                          f"{est.get('confidence')} · 침실 {est.get('bedroom_med_m2')}㎡ "
                          f"— 맞으면 위에 넣고 [적용]")
    else:
        if _img_coords is None:
            st.warning("streamlit-image-coordinates 미설치")
            return
        buf = bundle.setdefault("clk", {}).setdefault(
            unit_id, {"pts": [], "last": None, "sel": None})
        if tool.startswith("🔗"):                          # 연결: 노드 → 노드(종류 자동조인)
            sel = buf.get("sel")
            if sel is not None and sel in stt.nodes:
                panel.info(f"**{_disp_id(sel)} {stt.nodes[sel].role}** 선택됨 → "
                           f"② 연결할 노드 클릭 (취소: 같은 노드 다시 클릭)")
            else:
                buf["sel"] = sel = None
                panel.info("① 노드(동그라미) 클릭 → ② 연결할 노드 클릭 "
                           "(찍는 순서 무관)\n\n"
                           "연결 종류(문/트임)는 **탐지된 문으로 자동 판별** "
                           "— 두 노드 사이에 문이 있으면 door, 없으면 open\n\n"
                           "삭제: **연결선을 클릭**하면 선택(자홍색)→목록 강조→삭제 버튼")
            esel = buf.get("edge_sel")
            if esel and not any({e["a"], e["b"]} == set(esel) for e in stt.edges):
                esel = buf["edge_sel"] = None      # 사라진 엣지 선택 정리
            with canvas_col:
                disp = bake_background(dr, png, stt, highlight=sel,
                                       highlight_edge=esel)[0]
                val = _img_coords(disp, key=f"ic_{unit_id}_line")
            if val and val.get("x") is not None and (val["x"], val["y"]) != buf["last"]:
                buf["last"] = (val["x"], val["y"])
                pt = _cv_to_orig(val["x"], val["y"], mp)
                if sel is None:
                    # 연결선에 충분히 가까우면 그 엣지 선택(삭제용). 아파트는 방이
                    # 붙어 있어 선이 방 위를 지나므로, 노드보다 '선 근접'을 우선.
                    # 연결 시작은 선에서 떨어진 방 본체를 클릭. 표시 14px→원본px 환산.
                    pick = 14.0 * (mp[2] / mp[4] if mp[4] else 1.0)
                    edge, ed = _nearest_edge(stt, pt)
                    if edge is not None and ed <= pick:
                        buf["edge_sel"] = (edge["a"], edge["b"])
                        buf["sel"] = None
                    else:
                        node = _nearest_node(stt, pt)
                        if node is not None:
                            buf["sel"] = node
                            buf["edge_sel"] = None
                else:
                    node = _nearest_node(stt, pt)
                    if node is not None and node != sel:
                        add_edge(stt, sel, node, derive_via(stt, dr, sel, node))
                        write_svg(stt, dr)
                    buf["sel"] = None
                _rerun(st)
            if stt.edges:
                if esel:                            # 선 클릭으로 선택된 연결 → 큰 삭제 버튼
                    a, b = esel
                    if panel.button(
                            f"🗑 선택 연결 삭제: {_disp_id(a)} {stt.nodes[a].role} – "
                            f"{_disp_id(b)} {stt.nodes[b].role}",
                            type="primary", use_container_width=True,
                            key=f"delsel_{unit_id}"):
                        remove_edge(stt, a, b)
                        write_svg(stt, dr)
                        buf["edge_sel"] = None
                        _rerun(st)
                # 읽기전용 정보 테이블(선택·삭제는 도면서 선 클릭→위에서). 🔹=선택중, 색=종류.
                import pandas as pd
                _VIA_LAB = {"door": "문", "open": "트임"}
                panel.caption(f"연결 {len(stt.edges)}개 — 도면에서 선을 클릭해 선택 → "
                              f"위에서 삭제 (🔵문 · 🟠트임)")
                erows = []
                for e in stt.edges:
                    is_sel = esel is not None and {e["a"], e["b"]} == set(esel)
                    erows.append({
                        "": "🔹" if is_sel else "",
                        "A": f"{_disp_id(e['a'])} {stt.nodes[e['a']].role}",
                        "B": f"{_disp_id(e['b'])} {stt.nodes[e['b']].role}",
                        "종류": _VIA_LAB.get(e.get("via"), str(e.get("via") or "")),
                    })
                sty = pd.DataFrame(erows).style.map(   # 종류 글자색=도면 선색
                    lambda v: "color:#1565C0" if v == "문"
                    else ("color:#F2A900" if v == "트임" else ""), subset=["종류"])
                panel.dataframe(sty, hide_index=True, use_container_width=True)
        else:                                              # ✒️ 영역 그리기(폴리곤)
            cv_base = panel.selectbox("그릴 공간 종류(=역할)", DRAW_BASES, key="cbase")
            panel.caption(f"여기서 고른 종류가 그대로 **역할**이 됨(나중에 🏷에서 변경).\n\n"
                          f"영역 모서리들 클릭 후 **[✓ 완성]** (사각형=네 모서리)"
                          f"\n\n클릭점 {len(buf['pts'])}개")
            with canvas_col:
                disp = _overlay_clicks(bg, buf["pts"], mp, "polygon")
                val = _img_coords(disp, key=f"ic_{unit_id}_poly")
            if val and val.get("x") is not None and (val["x"], val["y"]) != buf["last"]:
                buf["last"] = (val["x"], val["y"])
                buf["pts"].append(_cv_to_orig(val["x"], val["y"], mp))
                _rerun(st)
            if panel.button("✓ 완성", type="primary", use_container_width=True):
                if len(buf["pts"]) >= 3:
                    add_drawn_region(stt, cv_base, buf["pts"], dr)
                    write_svg(stt, dr)
                buf["pts"] = []
                _rerun(st)
            if panel.button("↩ 클릭 취소", use_container_width=True):
                buf["pts"] = []
                _rerun(st)


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
