#!/usr/bin/env python3
"""edit_server — AI-Hub 그래프 '정보 보정' 웹 주석 에디터 (ADR-0008).

SVG 폐기. 편집 대상 = 그래프 JSON 그 자체(= 최종 산출 스키마). 원본 PNG를 불변 배경으로
깔고([[inspect-original-first]]), 그 위 추출 폴리곤에 **의미**만 보정한다:
  · 역할(role) 지정     · 인접(door/open) 토글     · 과분할 노드 합치기(merge)
  · 잘못된 노드 삭제     · 현관 지정
문/치수/여닫이는 배경 PNG에 이미 그려져 있으므로 오버레이로 다시 그리지 않는다.
모든 편집은 브라우저에서 즉시 반영(역할/인접/삭제=서버 왕복 0, 합치기만 shapely 1콜),
도면당 1회 저장. 알바용이라 '조작 지연 0'이 설계 기준.

폴더 분리(ADR-0008):
  data/staging/corrected/graphs/    = 원본(자동변환) · 읽기전용
  data/staging/corrected/edits/ = 작업(사람 편집) · 저장 위치 (graphs/ 밖 = 회계캐시 무효화 회피)
  data/staging/corrected/png/       = 원본 PNG 추출 캐시
  data/staging/corrected/_png_index.json = sig→(zip,entry) 캐시(1회 빌드)

실행:  PYTHONPATH=src python scripts/edit_server.py --port 8600
보기:  nginx /editor/ 또는  ssh -fN -L 8600:localhost:8600 ju@sse.aines.kr → http://localhost:8600
"""
import argparse
import json
import os
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

try:
    from plan2graph import topoedit
    GRAPHS = str(topoedit.GRAPHS_DIR)
    ROLES = list(topoedit.ROLES)
    ROLE_COLOR = dict(topoedit.ROLE_COLOR)
except Exception:  # noqa: BLE001
    GRAPHS = os.path.expanduser("~/plan2graph/data/staging/corrected/graphs")
    ROLES = ["거실", "주방", "현관", "침실", "안방", "화장실", "욕실", "발코니",
             "드레스룸", "다목적공간", "복도", "전실", "기타", "알파룸"]
    ROLE_COLOR = {}

_BASE = os.path.dirname(GRAPHS)                 # data/staging/corrected
EDITS = os.path.join(_BASE, "edits")    # 사람 편집본(graphs/ 밖)
PNG_CACHE = os.path.join(_BASE, "png")          # PNG 추출 캐시
PNG_INDEX = os.path.join(_BASE, "_png_index.json")
for d in (EDITS, PNG_CACHE):
    os.makedirs(d, exist_ok=True)

# ── 역할 팔레트(키보드 단축키 1..0,a..) — 자주 쓰는 순. topoedit.ROLES에서 추림 ──
PALETTE = ["거실", "안방", "침실", "주방", "화장실", "욕실", "현관", "발코니",
           "드레스룸", "알파룸", "다목적공간", "복도", "전실", "실외기실", "파우더룸", "기타"]
PALETTE = [r for r in PALETTE if r in ROLES] or PALETTE


# ─────────────────────────────────────────────────────────────────────────────
# 원본 PNG 인덱스 (sig → (zip, entry)) — 1회 빌드 후 디스크 캐시. 백그라운드 로드.
# ─────────────────────────────────────────────────────────────────────────────
_PNG_IDX = None            # dict sig -> [zip, entry] ; None=미빌드
_PNG_IDX_STATE = "idle"    # idle|building|ready|error


def _build_png_index():
    global _PNG_IDX, _PNG_IDX_STATE
    _PNG_IDX_STATE = "building"
    try:
        if os.path.exists(PNG_INDEX):
            with open(PNG_INDEX, encoding="utf-8") as f:
                _PNG_IDX = json.load(f)
            _PNG_IDX_STATE = "ready"
            return
        from plan2graph import aihub_source as A
        idx = {}
        for r in A.scan():
            zp, entry = r["png"]
            idx[r["sig"]] = [zp, entry]
        _PNG_IDX = idx
        with open(PNG_INDEX, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False)
        _PNG_IDX_STATE = "ready"
    except Exception as e:  # noqa: BLE001
        _PNG_IDX = {}
        _PNG_IDX_STATE = "error"
        print(f"[png-index] 빌드 실패: {e}")


def _sig_of(plan_id):
    """{HOUSE}_FP_<sig>_u<n> → sig. sig는 '_' 포함(crc_size). '_FP_'와 '_u<n>' 사이 전체."""
    import re
    m = re.search(r"_FP_(.+?)_u\d+$", plan_id)
    if m:
        return m.group(1)
    m = re.search(r"_FP_(.+)$", plan_id)
    return m.group(1) if m else None


def _png_bytes(plan_id):
    """plan_id → 원본 sheet PNG bytes (디스크 캐시). 없으면 None."""
    sig = _sig_of(plan_id)
    if not sig:
        return None
    cache = os.path.join(PNG_CACHE, sig + ".png")
    if os.path.exists(cache):
        return open(cache, "rb").read()
    if not _PNG_IDX or sig not in _PNG_IDX:
        return None
    zp, entry = _PNG_IDX[sig]
    try:
        with zipfile.ZipFile(zp) as zf:
            data = zf.read(entry)
        with open(cache, "wb") as f:
            f.write(data)
        return data
    except Exception:  # noqa: BLE001
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 그래프 로드/보강/판정
# ─────────────────────────────────────────────────────────────────────────────
def _graph_path(gid):
    cp = os.path.join(EDITS, gid + ".json")
    return cp if os.path.exists(cp) else os.path.join(GRAPHS, gid + ".json")


def _enrich_doors(g):
    """문 orientation 없으면 polygon(arc)에서 추론(geomgraph 재사용)."""
    try:
        from plan2graph import geomgraph
        from shapely.geometry import Polygon
    except Exception:  # noqa: BLE001
        return g
    for d in (g.get("doors") or []):
        if d.get("orientation"):
            continue
        poly = d.get("polygon")
        if not poly or len(poly) < 5:
            continue
        try:
            o = geomgraph._door_orientation(Polygon([tuple(p) for p in poly]))
        except Exception:  # noqa: BLE001
            o = None
        if o:
            d["orientation"] = o
    return g


def _status(g):
    """plan_quality.classify (= convert_plan GATE-0) → {clean, reasons}. 변환가능 지표."""
    try:
        from plan2graph.plan_quality import classify
        clean, reasons = classify(g)
        return {"clean": bool(clean), "reasons": list(reasons)}
    except Exception as e:  # noqa: BLE001
        return {"clean": None, "reasons": [f"판정불가: {e}"]}


# ─────────────────────────────────────────────────────────────────────────────
# 노드 합치기 — 순수함수: (graph, ids) → 합쳐진 graph. shapely unary_union.
#   과분할(한 공간이 N개 노드로 쪼개짐)을 1개로. 첫 id의 역할/속성을 기준(keep)으로 유지.
# ─────────────────────────────────────────────────────────────────────────────
def _merge_nodes(g, ids):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    rooms = g.get("rooms") or {}
    ids = [str(i) for i in ids if str(i) in rooms]
    # 순서 보존 dedup
    seen, uids = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            uids.append(i)
    ids = uids
    if len(ids) < 2:
        return g, "노드 2개 이상을 선택하세요"

    keep = ids[0]                       # 기준 노드(역할·privacy 등 유지)
    drop = set(ids[1:])
    kept = rooms[keep]

    # 폴리곤 union (buffer(0)로 자가교차 정리)
    polys = []
    for i in ids:
        pg = rooms[i].get("polygon")
        if pg and len(pg) >= 3:
            try:
                polys.append(Polygon([tuple(p) for p in pg]).buffer(0))
            except Exception:  # noqa: BLE001
                pass
    if polys:
        merged = unary_union(polys)
        # 과분할 조각은 벽선 두께만큼(보통 1~8px) 떨어져 union이 MultiPolygon이 되곤 한다.
        # 모폴로지 클로징(buffer +g→ -g)으로 머리카락 갭만 메워 단일 폴리곤으로 만든다.
        if merged.geom_type != "Polygon":
            span = max(merged.bounds[2] - merged.bounds[0], merged.bounds[3] - merged.bounds[1], 1)
            for gpx in (1.5, 3, 6, 12, max(span * 0.03, 12)):
                closed = merged.buffer(gpx, join_style=2).buffer(-gpx, join_style=2)
                if closed.geom_type == "Polygon" and not closed.is_empty:
                    merged = closed
                    break
            else:
                merged = max(merged.geoms, key=lambda p: p.area)   # 끝내 분리면 최대 조각
        if merged.geom_type == "Polygon" and not merged.is_empty:
            kept["polygon"] = [[round(x, 1), round(y, 1)] for x, y in merged.exterior.coords]
            c = merged.centroid
            kept["centroid"] = [round(c.x, 1), round(c.y, 1)]
            kept["area_px"] = round(merged.area, 1)
            minx, miny, maxx, maxy = merged.bounds
            kept["bbox_px"] = [round(minx, 1), round(miny, 1),
                               round(maxx - minx, 1), round(maxy - miny, 1)]
            kept["perimeter_px"] = round(merged.length, 1)

    # 리스트 속성 병합(순서 보존 dedup)
    for fld in ("fixtures", "door_ids", "window_ids", "wall_ids"):
        out, s = [], set()
        for i in ids:
            for v in (rooms[i].get(fld) or []):
                if v not in s:
                    s.add(v)
                    out.append(v)
        kept[fld] = out
    kept["n_windows"] = len(kept.get("window_ids") or [])
    kept["has_window"] = bool(kept.get("window_ids"))

    # 드롭 노드 제거
    for i in drop:
        rooms.pop(i, None)

    # 엣지 재연결: drop→keep, self-loop 제거, 무방향 중복 제거(via는 door 우선)
    keep_val = int(keep) if keep.lstrip("-").isdigit() else keep

    def remap(x):
        return keep_val if str(x) in drop else x

    best = {}   # frozenset({a,b}) -> edge
    for e in (g.get("edges") or []):
        f, t = remap(e.get("from")), remap(e.get("to"))
        if str(f) == str(t):
            continue
        e = dict(e)
        e["from"], e["to"] = f, t
        k = frozenset((str(f), str(t)))
        prev = best.get(k)
        if prev is None or (e.get("via") == "door" and prev.get("via") != "door"):
            best[k] = e
    g["edges"] = list(best.values())

    g["n_rooms"] = len(rooms)
    g["n_edges"] = len(g["edges"])
    return g, None


# ─────────────────────────────────────────────────────────────────────────────
# HTML (의미주석 UI — PNG 배경 + 폴리곤 오버레이, 전부 클라이언트 사이드)
# ─────────────────────────────────────────────────────────────────────────────
def _html():
    pal = json.dumps(PALETTE, ensure_ascii=False)
    col = json.dumps(ROLE_COLOR, ensure_ascii=False)
    return _HTML.replace("__PAL__", pal).replace("__COL__", col)


_HTML = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>정보 보정 에디터</title>
<style>
 :root{
   --bg:#0f1115; --panel:#171a21; --panel2:#1e222b; --line:#2a2f3a; --line2:#363c49;
   --txt:#e6e8ee; --muted:#9aa3b2; --accent:#3b82f6; --accent2:#22d3ee;
   --ok-bg:#0f3a25; --ok-fg:#4ade80; --bad-bg:#3a1620; --bad-fg:#fb7185; --na-bg:#23272f; --na-fg:#9aa3b2;
   --sel:#f59e0b; --adj:#a855f7; --merge:#22d3ee; --del:#ef4444; --split:#84cc16;
 }
 *{box-sizing:border-box} html,body{margin:0;height:100%}
 body{font-family:"Pretendard",system-ui,-apple-system,"Segoe UI",sans-serif;
   background:var(--bg);color:var(--txt);display:flex;height:100vh;overflow:hidden;font-size:13px}

 /* ── Sidebar ── */
 #side{width:288px;flex-shrink:0;background:var(--panel);border-right:1px solid var(--line);
   display:flex;flex-direction:column;height:100vh}
 #side .scroll{flex:1;overflow:auto;padding:12px 14px}
 .brand{display:flex;align-items:center;gap:8px;font-size:15px;font-weight:800;letter-spacing:-.2px;
   padding:14px 14px 10px;border-bottom:1px solid var(--line)}
 .brand .dot{width:9px;height:9px;border-radius:50%;background:var(--accent2);box-shadow:0 0 10px var(--accent2)}

 /* plan_id 복사 바 */
 .idbar{display:flex;align-items:center;gap:6px;margin:2px 0 10px;background:var(--panel2);
   border:1px solid var(--line);border-radius:8px;padding:7px 8px}
 .idbar code{flex:1;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11.5px;color:var(--accent2);
   user-select:all;white-space:nowrap;overflow:auto;scrollbar-width:none}
 .idbar code::-webkit-scrollbar{display:none}
 .icobtn{border:1px solid var(--line2);background:#222733;color:var(--txt);border-radius:6px;
   padding:4px 7px;cursor:pointer;font-size:12px;line-height:1;flex-shrink:0}
 .icobtn:hover{background:#2c3340;border-color:var(--accent)}
 .icobtn:active{transform:scale(.94)}

 /* 진행도 */
 .prog{margin:4px 0 12px}
 .prog .row{display:flex;justify-content:space-between;font-size:11.5px;color:var(--muted);margin-bottom:5px}
 .prog .row b{color:var(--txt)}
 .bar{height:6px;background:var(--panel2);border-radius:99px;overflow:hidden}
 .bar>i{display:block;height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));width:0%}

 /* 검색 + 리스트 */
 .lbl{font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;margin:14px 0 6px}
 #search{width:100%;padding:8px 10px;border-radius:8px;border:1px solid var(--line);background:var(--panel2);
   color:var(--txt);font-size:12.5px;outline:none}
 #search:focus{border-color:var(--accent)}
 #list{margin-top:6px;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:var(--panel2)}
 #list .opt{padding:7px 10px;cursor:pointer;border-bottom:1px solid var(--line);font-size:12px;
   white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:flex;align-items:center;gap:6px}
 #list .opt:last-child{border-bottom:none}
 #list .opt:hover{background:#222733}
 #list .opt.on{background:#1d3a5c;color:#fff}
 #list .opt .ck{flex-shrink:0;width:14px;text-align:center;font-size:11px}
 #list .opt.done .ck{color:var(--ok-fg)}
 .nav{display:flex;gap:8px;margin-top:8px}
 .nav button{flex:1;padding:8px;border-radius:8px;border:1px solid var(--line2);background:var(--panel2);
   color:var(--txt);cursor:pointer;font-size:12.5px;font-weight:600}
 .nav button:hover{background:#2a303c;border-color:var(--accent)}

 /* 상태 pill */
 #stat{margin:12px 0;padding:10px 12px;border-radius:9px;font-size:12.5px;font-weight:700;line-height:1.4}
 #stat.ok{background:var(--ok-bg);color:var(--ok-fg)}
 #stat.bad{background:var(--bad-bg);color:var(--bad-fg)}
 #stat.na{background:var(--na-bg);color:var(--na-fg)}
 #stat .why{font-weight:500;font-size:11.5px;margin-top:4px;opacity:.92}

 /* 모드 세그먼트 */
 .seg{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin:6px 0 10px}
 .seg button{padding:9px 6px;border-radius:9px;border:1px solid var(--line2);background:var(--panel2);
   color:var(--txt);cursor:pointer;font-size:12.5px;font-weight:700;display:flex;align-items:center;
   justify-content:center;gap:5px;transition:.12s}
 .seg button .k{font-size:10px;opacity:.55;font-weight:600}
 .seg button:hover{border-color:var(--accent)}
 .seg button.on{color:#0b0d12}
 .seg button[data-m=role].on{background:var(--sel);border-color:var(--sel)}
 .seg button[data-m=adj].on{background:var(--adj);border-color:var(--adj);color:#fff}
 .seg button[data-m=merge].on{background:var(--merge);border-color:var(--merge)}
 .seg button[data-m=del].on{background:var(--del);border-color:var(--del);color:#fff}
 .seg button[data-m=split]{grid-column:1/-1}
 .seg button[data-m=split].on{background:var(--split);border-color:var(--split);color:#0b0d12}

 /* 모드별 동적 패널 */
 #ctx{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:10px;margin-bottom:8px;min-height:56px}
 #ctx .ctitle{font-size:11px;font-weight:700;color:var(--muted);margin-bottom:7px}
 .pal{display:flex;flex-wrap:wrap;gap:6px}
 .pal .chip{display:flex;align-items:center;gap:6px;padding:5px 9px 5px 7px;border-radius:7px;
   border:1px solid var(--line2);background:#222733;cursor:pointer;font-size:12px;font-weight:600;user-select:none}
 .pal .chip:hover{border-color:#fff}
 .pal .chip .sw{width:11px;height:11px;border-radius:3px;flex-shrink:0}
 .pal .chip .k{font-size:9.5px;color:var(--muted);font-weight:700}
 .bigbtn{width:100%;padding:10px;border-radius:9px;border:none;cursor:pointer;font-size:13px;font-weight:800;margin-top:8px}
 .bigbtn.go{background:var(--merge);color:#0b0d12}
 .bigbtn.go:disabled{opacity:.4;cursor:not-allowed}
 .bigbtn.ghost{background:#222733;color:var(--txt);border:1px solid var(--line2);font-weight:600;margin-top:6px}
 .chiprow{display:flex;flex-wrap:wrap;gap:5px;margin:4px 0}
 .chiprow .t{padding:3px 8px;border-radius:6px;background:#0e2630;border:1px solid var(--merge);
   color:var(--merge);font-size:11px;font-weight:700}
 .chiprow .t.keep{background:var(--merge);color:#0b0d12}

 .help{font-size:11px;color:var(--muted);line-height:1.7;margin-top:6px}
 .help kbd{background:#222733;border:1px solid var(--line2);border-radius:4px;padding:1px 5px;
   font-family:ui-monospace,monospace;font-size:10.5px;color:var(--txt)}
 details.legend{margin-top:10px}
 details.legend summary{cursor:pointer;font-size:11.5px;color:var(--muted);font-weight:700;list-style:none}
 details.legend summary::-webkit-details-marker{display:none}
 details.legend summary:before{content:"▸ ";color:var(--accent)}
 details.legend[open] summary:before{content:"▾ "}

 /* 저장 바(고정) */
 .savebar{padding:12px 14px;border-top:1px solid var(--line);display:flex;gap:8px;background:var(--panel)}
 #save{flex:1;padding:11px;border-radius:9px;border:none;cursor:pointer;font-size:13.5px;font-weight:800;
   background:var(--accent);color:#fff}
 #save.dirty{background:var(--sel);color:#0b0d12;animation:pulse 1.6s infinite}
 @keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(245,158,11,.4)}50%{box-shadow:0 0 0 6px rgba(245,158,11,0)}}
 #undo{padding:11px 13px;border-radius:9px;border:1px solid var(--line2);background:var(--panel2);
   color:var(--txt);cursor:pointer;font-size:13px}
 #undo:disabled{opacity:.35;cursor:not-allowed}

 /* ── Canvas ── */
 #main{flex:1;position:relative;background:
   radial-gradient(circle at 1px 1px,#1c2027 1px,transparent 0) 0 0/22px 22px,#0b0d12}
 svg{width:100%;height:100vh;display:block;cursor:grab}
 svg.panning{cursor:grabbing}
 image{opacity:.95}
 .room{stroke-width:2;cursor:pointer;transition:fill-opacity .08s}
 .room{fill-opacity:.26;stroke:#0b0d12;stroke-opacity:.55}
 .room:hover{fill-opacity:.44}
 .room.sel{stroke:var(--sel);stroke-width:5;stroke-opacity:1;fill-opacity:.5}
 .room.adjA{stroke:var(--adj);stroke-width:5;stroke-opacity:1;stroke-dasharray:9 5;fill-opacity:.5}
 .room.msel{stroke:var(--merge);stroke-width:5;stroke-opacity:1;fill-opacity:.5}
 .room.mkeep{stroke:var(--merge);stroke-width:6;stroke-opacity:1;fill-opacity:.58;stroke-dasharray:none}
 .room.delhover:hover{stroke:var(--del);stroke-width:5;stroke-opacity:1;fill:var(--del);fill-opacity:.4}
 .room.ssel{stroke:var(--split);stroke-width:5;stroke-opacity:1;fill-opacity:.5}
 .cut{pointer-events:none}
 .cut .line{stroke:var(--split);stroke-width:3.5;stroke-linecap:round}
 .cut .ext{stroke:var(--split);stroke-width:1.5;stroke-dasharray:7 7;opacity:.55}
 .cut .prev{stroke:var(--split);stroke-width:2.5;stroke-dasharray:6 6;opacity:.85}
 .cut .pt{fill:#fff;stroke:var(--split);stroke-width:2.5}
 .cut .swatch{stroke:#0b0d12;stroke-width:2.5}
 .cut .sl{font-size:17px;font-weight:800;fill:#fff;text-anchor:middle;dominant-baseline:central;
   paint-order:stroke;stroke:#0b0d12;stroke-width:4;stroke-linejoin:round}
 .rlabel{font-size:19px;fill:#fff;font-weight:800;pointer-events:none;
   paint-order:stroke;stroke:#0b0d12;stroke-width:4.5;stroke-linejoin:round}
 .rbadge{pointer-events:none}
 .rbadge circle{fill:var(--merge);stroke:#0b0d12;stroke-width:2}
 .rbadge text{fill:#0b0d12;font-size:15px;font-weight:800;text-anchor:middle;dominant-baseline:central}
 .edge{pointer-events:none}

 #toast{position:absolute;top:16px;left:50%;transform:translateX(-50%) translateY(-8px);
   background:#0b0d12;color:#fff;padding:9px 16px;border-radius:9px;font-size:13px;font-weight:600;
   border:1px solid var(--line2);opacity:0;transition:.18s;pointer-events:none;box-shadow:0 8px 28px rgba(0,0,0,.5)}
 #toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
 #pngwarn{position:absolute;bottom:14px;left:14px;background:#3a2e10;color:#fcd34d;padding:6px 11px;
   border-radius:7px;font-size:12px;display:none;border:1px solid #6b5616}
 #hud{position:absolute;bottom:14px;right:14px;display:flex;gap:8px}
 #hud button{background:var(--panel);border:1px solid var(--line2);color:var(--txt);border-radius:8px;
   padding:7px 11px;cursor:pointer;font-size:12px;font-weight:600}
 #hud button:hover{border-color:var(--accent)}
 .modetag{position:absolute;top:14px;left:14px;padding:6px 12px;border-radius:99px;font-size:12px;
   font-weight:800;background:var(--panel);border:1px solid var(--line2)}
</style></head><body>
<div id="side">
 <div class="brand"><span class="dot"></span>정보 보정 에디터</div>
 <div class="scroll">
   <div class="idbar">
     <code id="pid" title="현재 도면 ID — 클릭하면 전체 선택">—</code>
     <button class="icobtn" id="copyId" title="ID 복사">📋</button>
   </div>
   <div class="prog">
     <div class="row"><span>보정 진행</span><span><b id="pdone">0</b> / <span id="ptot">0</span></span></div>
     <div class="bar"><i id="pbar"></i></div>
   </div>

   <div id="stat" class="na">—</div>

   <div class="lbl">편집 모드</div>
   <div class="seg">
     <button data-m="role" class="on">🎨 역할 <span class="k">R</span></button>
     <button data-m="adj">🔗 인접 <span class="k">A</span></button>
     <button data-m="merge">⛓ 합치기 <span class="k">M</span></button>
     <button data-m="del">🗑 삭제 <span class="k">D</span></button>
     <button data-m="split">✂ 나누기 <span class="k">S</span></button>
   </div>
   <div id="ctx"></div>

   <div class="lbl">도면 찾기</div>
   <input id="search" placeholder="ID 일부로 검색 (예: cb4a)">
   <div id="list"></div>
   <div class="nav"><button id="prev">◀ 이전</button><button id="next">다음 ▶</button></div>

   <details class="legend"><summary>범례 · 조작</summary>
   <div class="help">
     <span style="color:var(--sel)">●</span> 선택 ·
     <span style="color:var(--adj)">●</span> 인접A ·
     <span style="color:var(--merge)">●</span> 합치기 ·
     <span style="color:var(--del)">●</span> 삭제 ·
     <span style="color:var(--split)">●</span> 나누기<br>
     <kbd>휠</kbd> 확대 · <kbd>드래그</kbd> 이동 · <kbd>F</kbd> 맞춤<br>
     <kbd>R</kbd><kbd>A</kbd><kbd>M</kbd><kbd>D</kbd><kbd>S</kbd> 모드 · <kbd>Ctrl+Z</kbd> 되돌리기 · <kbd>Ctrl+S</kbd> 저장<br>
     <b>배경 = 원본 도면</b> — 문·치수·여닫이는 PNG에서 직접 확인.
   </div></details>
 </div>
 <div class="savebar">
   <button id="undo" disabled title="되돌리기 (Ctrl+Z)">↶</button>
   <button id="save">💾 저장</button>
 </div>
</div>
<div id="main">
  <svg id="svg"></svg>
  <div class="modetag" id="modetag">🎨 역할</div>
  <div id="toast"></div>
  <div id="pngwarn">⚠ 원본 PNG 없음(인덱싱중일 수 있음) — 해석만 표시</div>
  <div id="hud"><button id="fit">맞춤 (F)</button></div>
</div>
<script>
const NS='http://www.w3.org/2000/svg', svg=document.getElementById('svg');
const PALETTE=__PAL__, ROLE_COLOR=__COL__;
const MODES={role:'🎨 역할',adj:'🔗 인접',merge:'⛓ 합치기',del:'🗑 삭제',split:'✂ 나누기'};
let G=null,GID=null,dirty=false,mode='role',sel=null,adjA=null,mergeSel=[],vb=null;
let splitSel=null,cutPts=[],splitRoles=null;
let LIST=[],undoStack=[];
function evToUser(ev){const pt=svg.createSVGPoint();pt.x=ev.clientX;pt.y=ev.clientY;
  const u=pt.matrixTransform(svg.getScreenCTM().inverse());return [u.x,u.y];}
function defaultRoles(role){role=role||'';
  if(role.indexOf('거실')>=0)return['거실','복도'];
  if(role.indexOf('드레스')>=0)return['드레스룸','파우더룸'];
  return[role||'기타','복도'];}

function el(t,a,p){const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);if(p)p.appendChild(e);return e;}
function toast(s){const t=document.getElementById('toast');t.textContent=s;t.classList.add('show');
  clearTimeout(t._);t._=setTimeout(()=>t.classList.remove('show'),1500);}
function colorOf(r){return ROLE_COLOR[r]||'#9aa3b2';}
function setDirty(d){dirty=d;const b=document.getElementById('save');
  b.classList.toggle('dirty',d);b.textContent=d?'💾 저장 *':'💾 저장';}
function pushUndo(){try{undoStack.push(JSON.stringify(G));if(undoStack.length>40)undoStack.shift();
  document.getElementById('undo').disabled=false;}catch(e){}}
function undo(){if(!undoStack.length)return;G=JSON.parse(undoStack.pop());
  document.getElementById('undo').disabled=!undoStack.length;sel=null;adjA=null;mergeSel=[];
  setDirty(true);render();showStatusLocal();toast('되돌림');}
function showStatusLocal(){/* 로컬 변경 후엔 게이트 재판정은 저장 시 — 표시는 유지 */}

// ── 목록/검색 ───────────────────────────────────────────────────────────────
async function loadList(q){
  const r=await(await fetch('api/graphs?n=250'+(q?'&q='+encodeURIComponent(q):''))).json();
  LIST=r.items;
  document.getElementById('pdone').textContent=r.corrected;
  document.getElementById('ptot').textContent=r.total;
  document.getElementById('pbar').style.width=(r.total?100*r.corrected/r.total:0).toFixed(1)+'%';
  renderList();
  if(!GID&&LIST.length)loadGraph(LIST[0].id);
}
function renderList(){
  const box=document.getElementById('list');box.innerHTML='';
  LIST.forEach(o=>{const d=document.createElement('div');
    d.className='opt'+(o.corrected?' done':'')+(o.id===GID?' on':'');
    d.innerHTML='<span class="ck">'+(o.corrected?'✔':'·')+'</span>'+o.id.replace('APT_FP_','');
    d.title=o.id;d.onclick=()=>{vb=null;loadGraph(o.id);};box.appendChild(d);});
}
async function loadGraph(id){
  const r=await(await fetch('api/graph/'+id)).json();
  if(r.error){toast('로드 실패: '+r.error);return;}
  G=r.graph;GID=id;sel=null;adjA=null;mergeSel=[];undoStack=[];
  document.getElementById('undo').disabled=true;setDirty(false);
  document.getElementById('pid').textContent=id;
  showStatus(r.status);renderList();render();
}
function showStatus(st){
  const e=document.getElementById('stat');
  if(!st||st.clean===null){e.className='na';e.textContent='판정 불가';return;}
  if(st.clean){e.className='ok';e.innerHTML='✅ 변환 통과 — 사용가능';}
  else{e.className='bad';e.innerHTML='❌ 보정 필요<div class="why">'+(st.reasons||[]).join(' · ')+'</div>';}
}

// ── 캔버스 ──────────────────────────────────────────────────────────────────
function bbox(){const b=G.bbox_px||[0,0,1000,1000];const pad=Math.max(b[2],b[3])*0.06;
  return [b[0]-pad,b[1]-pad,b[2]+2*pad,b[3]+2*pad];}
function render(){
  while(svg.firstChild)svg.removeChild(svg.firstChild);
  if(!vb)vb=bbox();
  svg.setAttribute('viewBox',vb.join(' '));
  const img=el('image',{href:'api/png/'+GID,x:0,y:0,preserveAspectRatio:'none'},svg);
  const probe=new Image();
  probe.onload=()=>{img.setAttribute('width',probe.naturalWidth);img.setAttribute('height',probe.naturalHeight);
    document.getElementById('pngwarn').style.display='none';};
  probe.onerror=()=>{document.getElementById('pngwarn').style.display='block';};
  probe.src='api/png/'+GID;

  // 엣지(비상호작용 — 클릭 가로채지 않게 pointer-events:none + 맨 아래)
  const eg=el('g',{class:'edge'},svg);
  (G.edges||[]).forEach(e=>{const a=G.rooms[e.from],b=G.rooms[e.to];
    const ca=a&&a.centroid,cb=b&&b.centroid;if(!ca||!cb)return;const via=e.via;
    el('line',{x1:ca[0],y1:ca[1],x2:cb[0],y2:cb[1],stroke:via==='door'?'#fb7185':'#38bdf8',
      'stroke-width':2.2,'stroke-dasharray':via==='door'?'':'7 6',opacity:.6},eg);});

  // 방: 큰 것 먼저 → 작은 것이 위에(겹침/중첩서 작은방 클릭 보장)
  const entries=Object.entries(G.rooms||{}).filter(([id,r])=>r.polygon&&r.polygon.length>=3);
  entries.sort((x,y)=>(y[1].area_px||0)-(x[1].area_px||0));
  for(const [id,r] of entries){
    let cls='room';
    if(mode==='role'&&sel===id)cls+=' sel';
    if(mode==='adj'&&adjA===id)cls+=' adjA';
    if(mode==='merge'){const i=mergeSel.indexOf(id);if(i===0)cls+=' mkeep';else if(i>0)cls+=' msel';}
    if(mode==='del')cls+=' delhover';
    if(mode==='split'&&splitSel===id)cls+=' ssel';
    const po=el('polygon',{points:r.polygon.map(p=>p[0]+','+p[1]).join(' '),class:cls,
      fill:colorOf(r.role),'data-id':id},svg);
    po.addEventListener('click',ev=>{ev.stopPropagation();if(justPanned)return;onRoom(id,ev);});
  }
  // 라벨(맨 위, 비상호작용)
  for(const [id,r] of entries){const c=r.centroid||r.polygon[0];
    const t=el('text',{x:c[0],y:c[1],'text-anchor':'middle',class:'rlabel'},svg);t.textContent=r.role||'?';
    if(mode==='merge'){const i=mergeSel.indexOf(id);if(i>=0){
      const g=el('g',{class:'rbadge'},svg);el('circle',{cx:c[0]+30,cy:c[1]-20,r:13},g);
      const bt=el('text',{x:c[0]+30,y:c[1]-20},g);bt.textContent=(i+1);}}
  }
  if(mode==='split')drawCut();
  document.getElementById('modetag').textContent=MODES[mode];
}
// 컷 오버레이(선택방·점·컷선·연장·좌우 역할 스와치). 전부 pointer-events:none.
function drawCut(){
  if(!splitSel||!cutPts.length)return;
  const g=el('g',{class:'cut'},svg);
  const span=Math.max((vb&&vb[2])||1000,(vb&&vb[3])||1000);
  if(cutPts.length===1){
    const p=cutPts[0];
    el('line',{class:'prev',id:'cutprev',x1:p[0],y1:p[1],x2:p[0],y2:p[1]},g);
  }
  if(cutPts.length===2){
    const [p0,p1]=cutPts;let dx=p1[0]-p0[0],dy=p1[1]-p0[1];const L=Math.hypot(dx,dy)||1;
    const ux=dx/L,uy=dy/L,ext=span*0.5;
    el('line',{class:'ext',x1:p0[0]-ux*ext,y1:p0[1]-uy*ext,x2:p1[0]+ux*ext,y2:p1[1]+uy*ext},g);
    el('line',{class:'line',x1:p0[0],y1:p0[1],x2:p1[0],y2:p1[1]},g);
    // 좌(roles[0], _side>0): 오프셋(-dy,dx) ↔ 서버 _side와 일치. 우(roles[1]): (dy,-dx).
    const roles=splitRoles||defaultRoles(G.rooms[splitSel].role);
    const off=Math.max(L*0.32,span*0.06),mx=(p0[0]+p1[0])/2,my=(p0[1]+p1[1])/2;
    const sides=[[-uy,ux,roles[0],'좌'],[uy,-ux,roles[1],'우']];
    sides.forEach(([nx,ny,role,tag])=>{const cx=mx+nx*off,cy=my+ny*off;
      el('circle',{class:'swatch',cx,cy,r:16,fill:colorOf(role)},g);
      const t=el('text',{class:'sl',x:cx,y:cy},g);t.textContent=tag;});
  }
  cutPts.forEach(p=>el('circle',{class:'pt',cx:p[0],cy:p[1],r:6},g));
}

// ── 방 클릭 디스패치 ─────────────────────────────────────────────────────────
function onRoom(id,ev){
  if(!G.rooms[id])return;
  if(mode==='split'){
    if(!splitSel){splitSel=id;cutPts=[];splitRoles=null;render();renderCtx();
      toast('컷 시작점→끝점을 클릭(방을 가로지르게)');}
    else if(cutPts.length<2&&ev){cutPts.push(evToUser(ev));
      if(cutPts.length===2)splitRoles=defaultRoles(G.rooms[splitSel].role);
      render();renderCtx();}
    return;
  }
  if(mode==='role'){sel=id;render();
    toast('선택: '+(G.rooms[id].role||id)+' — 역할 클릭/숫자키');}
  else if(mode==='adj'){
    if(adjA===null){adjA=id;render();toast('A = '+(G.rooms[id].role||id)+' — 인접할 B 클릭');}
    else if(adjA===id){adjA=null;render();toast('취소');}
    else{toggleAdj(adjA,id);adjA=null;render();}
  }else if(mode==='merge'){
    const i=mergeSel.indexOf(id);
    if(i>=0)mergeSel.splice(i,1);else mergeSel.push(id);
    render();renderCtx();
  }else if(mode==='del'){
    pushUndo();const role=G.rooms[id].role||id;delete G.rooms[id];
    G.edges=(G.edges||[]).filter(e=>String(e.from)!==id&&String(e.to)!==id);
    setDirty(true);render();toast('삭제: '+role);
  }
}
function toggleAdj(a,b){
  pushUndo();G.edges=G.edges||[];
  const idx=G.edges.findIndex(e=>{const f=String(e.from),t=String(e.to);
    return (f===String(a)&&t===String(b))||(f===String(b)&&t===String(a));});
  if(idx<0){G.edges.push({from:a,to:b,via:'door'});toast('인접 추가 → 문');}
  else{const e=G.edges[idx];
    if(e.via==='door'){e.via='open';e.door_id=null;toast('인접 → 개방');}
    else{G.edges.splice(idx,1);toast('인접 제거');}}
  setDirty(true);
}
function setRole(role){if(!sel){toast('먼저 방을 클릭하세요');return;}
  pushUndo();G.rooms[sel].role=role;setDirty(true);render();toast('역할 → '+role);}

async function doMerge(){
  if(mergeSel.length<2){toast('합칠 노드를 2개 이상 선택');return;}
  pushUndo();
  const r=await(await fetch('api/merge',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({graph:G,ids:mergeSel})})).json();
  if(r.error){toast('합치기 실패: '+r.error);undoStack.pop();return;}
  const n=mergeSel.length;G=r.graph;mergeSel=[];setDirty(true);render();renderCtx();
  toast(n+'개 → 1개로 합침');
}

async function doSplit(){
  if(!splitSel||cutPts.length!==2){toast('방 선택 후 컷 2점을 찍으세요');return;}
  const roles=splitRoles||defaultRoles(G.rooms[splitSel].role);
  pushUndo();
  const r=await(await fetch('api/split',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({graph:G,room_id:splitSel,cut:cutPts,roles})})).json();
  if(r.error){toast('나누기 실패: '+r.error);undoStack.pop();return;}
  G=r.graph;if(r.status)showStatus(r.status);
  splitSel=null;cutPts=[];splitRoles=null;setDirty(true);render();renderCtx();
  toast('나눔: '+roles[0]+' / '+roles[1]);
}
function resetSplit(){splitSel=null;cutPts=[];splitRoles=null;render();renderCtx();}

// ── 모드별 컨텍스트 패널 ────────────────────────────────────────────────────
function renderCtx(){
  const c=document.getElementById('ctx');
  if(mode==='role'){
    let h='<div class="ctitle">방 선택 후 역할 지정 (숫자/문자키 가능)</div><div class="pal">';
    PALETTE.forEach((r,i)=>{const key=i<9?(i+1):(i===9?'0':String.fromCharCode(97+i-10));
      h+='<span class="chip" data-r="'+r+'"><span class="sw" style="background:'+colorOf(r)+'"></span>'
        +r+' <span class="k">'+key+'</span></span>';});
    h+='</div><div class="help">선택 방에 <kbd>E</kbd> = 현관 지정</div>';
    c.innerHTML=h;
    c.querySelectorAll('.chip').forEach(ch=>ch.onclick=()=>setRole(ch.dataset.r));
  }else if(mode==='adj'){
    c.innerHTML='<div class="ctitle">두 방을 차례로 클릭</div>'
      +'<div class="help">방 <b>A</b> → 방 <b>B</b> 클릭 시:<br>'
      +'없음 → <span style="color:var(--bad-fg)">문(door)</span> → '
      +'<span style="color:var(--accent2)">개방(open)</span> → 없음 …으로 순환.<br>'
      +(adjA?'현재 A = <b style="color:var(--adj)">'+(G.rooms[adjA]?.role||adjA)+'</b> — B를 클릭':'A를 클릭하세요')+'</div>';
  }else if(mode==='merge'){
    let h='<div class="ctitle">과분할 노드 합치기 — 클릭으로 다중 선택</div>';
    if(mergeSel.length){h+='<div class="chiprow">';
      mergeSel.forEach((id,i)=>h+='<span class="t'+(i===0?' keep':'')+'">'+(i+1)+'. '+(G.rooms[id]?.role||id)+'</span>');
      h+='</div><div class="help">①번 노드의 역할/속성을 유지합니다.</div>';}
    else h+='<div class="help">합칠 노드들을 클릭하세요(2개 이상). 첫 선택이 기준.</div>';
    h+='<button class="bigbtn go" id="mgo"'+(mergeSel.length<2?' disabled':'')+'>⛓ '+mergeSel.length+'개 합치기 (Enter)</button>';
    if(mergeSel.length)h+='<button class="bigbtn ghost" id="mclr">선택 해제 (Esc)</button>';
    c.innerHTML=h;
    const go=document.getElementById('mgo');if(go)go.onclick=doMerge;
    const cl=document.getElementById('mclr');if(cl)cl.onclick=()=>{mergeSel=[];render();renderCtx();};
  }else if(mode==='del'){
    c.innerHTML='<div class="ctitle">잘못된 노드 삭제</div>'
      +'<div class="help">삭제할 방을 클릭하면 노드와 연결 엣지가 제거됩니다.<br>'
      +'실수 시 <kbd>Ctrl+Z</kbd>로 되돌리기.</div>';
  }else if(mode==='split'){
    let h='<div class="ctitle">방 나누기 — 컷으로 연결공간(복도/파우더룸) 복원</div>';
    if(!splitSel){h+='<div class="help">길쭉한 방(거실·드레스룸 등)을 클릭해 선택하세요.<br>'
      +'문·엣지는 컷 후 기하로 자동 재분배됩니다.</div>';}
    else if(cutPts.length<2){h+='<div class="help">선택: <b style="color:var(--split)">'
      +(G.rooms[splitSel]?.role||splitSel)+'</b><br>방을 가로지르도록 <b>'+cutPts.length+'/2</b> 점 클릭.<br>'
      +'(선분은 자동 연장 — 양 끝이 경계 근처면 OK)</div>'
      +'<button class="bigbtn ghost" id="sclr">선택 취소 (Esc)</button>';}
    else{const roles=splitRoles||defaultRoles(G.rooms[splitSel].role);
      const opt=(sel)=>PALETTE.map(r=>'<option value="'+r+'"'+(r===sel?' selected':'')+'>'+r+'</option>').join('');
      h+='<div class="help">컷 방향 기준 좌/우 역할을 지정하세요.</div>'
        +'<div style="display:flex;gap:6px;align-items:center;margin:6px 0">'
        +'<span class="cut-sw" style="display:inline-block;width:13px;height:13px;border-radius:3px;background:'+colorOf(roles[0])+'"></span>'
        +'<b style="color:var(--split);width:16px">좌</b><select id="sL" style="flex:1">'+opt(roles[0])+'</select></div>'
        +'<div style="display:flex;gap:6px;align-items:center;margin:6px 0">'
        +'<span class="cut-sw" style="display:inline-block;width:13px;height:13px;border-radius:3px;background:'+colorOf(roles[1])+'"></span>'
        +'<b style="color:var(--split);width:16px">우</b><select id="sR" style="flex:1">'+opt(roles[1])+'</select></div>'
        +'<button class="bigbtn go" id="sgo" style="background:var(--split)">✂ 나누기 실행 (Enter)</button>'
        +'<button class="bigbtn ghost" id="sclr">다시 (Esc)</button>';}
    c.innerHTML=h;
    const sL=document.getElementById('sL'),sR=document.getElementById('sR');
    if(sL)sL.onchange=()=>{splitRoles=[sL.value,sR.value];render();renderCtx();};
    if(sR)sR.onchange=()=>{splitRoles=[sL.value,sR.value];render();renderCtx();};
    const sgo=document.getElementById('sgo');if(sgo)sgo.onclick=doSplit;
    const scl=document.getElementById('sclr');if(scl)scl.onclick=resetSplit;
  }
}
function setMode(m){mode=m;sel=null;adjA=null;mergeSel=[];splitSel=null;cutPts=[];splitRoles=null;
  document.querySelectorAll('.seg button').forEach(b=>b.classList.toggle('on',b.dataset.m===m));
  renderCtx();render();}
document.querySelectorAll('.seg button').forEach(b=>b.onclick=()=>setMode(b.dataset.m));

// ── 키보드 ──────────────────────────────────────────────────────────────────
document.addEventListener('keydown',ev=>{
  const tag=ev.target.tagName;if(tag==='INPUT'||tag==='TEXTAREA')return;
  if((ev.ctrlKey||ev.metaKey)&&ev.key.toLowerCase()==='z'){ev.preventDefault();undo();return;}
  if((ev.ctrlKey||ev.metaKey)&&ev.key.toLowerCase()==='s'){ev.preventDefault();save();return;}
  if(ev.ctrlKey||ev.metaKey||ev.altKey)return;
  const k=ev.key.toLowerCase();
  if(k==='f'){fit();return;}
  if(k==='r'){setMode('role');return;} if(k==='m'){setMode('merge');return;} if(k==='d'){setMode('del');return;}
  if(k==='s'){setMode('split');return;}
  if(mode==='adj'||k==='a'){if(k==='a'&&mode!=='adj'){setMode('adj');return;}}
  if(mode==='merge'){if(ev.key==='Enter'){doMerge();return;}if(ev.key==='Escape'){mergeSel=[];render();renderCtx();return;}}
  if(mode==='split'){if(ev.key==='Enter'){doSplit();return;}if(ev.key==='Escape'){resetSplit();return;}}
  if(mode==='role'){
    if(k==='e'){if(sel){pushUndo();G.rooms[sel].role='현관';setDirty(true);render();toast('현관 지정');}return;}
    let i=-1;if(ev.key>='1'&&ev.key<='9')i=+ev.key-1;else if(ev.key==='0')i=9;
    else if(k>='a'&&k<='f')i=10+k.charCodeAt(0)-97;
    if(i>=0&&i<PALETTE.length)setRole(PALETTE[i]);
  }
});

// ── prev/next/검색/복사/저장 ─────────────────────────────────────────────────
function move(d){const i=LIST.findIndex(o=>o.id===GID);const n=i+d;
  if(n>=0&&n<LIST.length){vb=null;loadGraph(LIST[n].id);}}
document.getElementById('prev').onclick=()=>move(-1);
document.getElementById('next').onclick=()=>move(1);
let st;document.getElementById('search').oninput=e=>{clearTimeout(st);
  st=setTimeout(()=>loadList(e.target.value.trim()),220);};
document.getElementById('copyId').onclick=async()=>{
  try{await navigator.clipboard.writeText(GID);toast('복사됨: '+GID);}
  catch(e){const r=document.createRange();r.selectNode(document.getElementById('pid'));
    getSelection().removeAllRanges();getSelection().addRange(r);
    try{document.execCommand('copy');toast('복사됨');}catch(_){toast('복사 실패 — 수동 선택하세요');}}
};
async function save(){if(!G)return;
  const r=await(await fetch('api/graph/'+GID,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(G)})).json();
  setDirty(false);if(r.status)showStatus(r.status);toast('저장됨 → edits/');loadList(document.getElementById('search').value.trim());}
document.getElementById('save').onclick=save;
document.getElementById('undo').onclick=undo;

// ── 팬/줌 ──────────────────────────────────────────────────────────────────
function fit(){vb=bbox();svg.setAttribute('viewBox',vb.join(' '));}
document.getElementById('fit').onclick=fit;
let down=null,justPanned=false;
svg.addEventListener('mousedown',ev=>{if(!vb)return;down=[ev.clientX,ev.clientY,vb.slice()];justPanned=false;});
window.addEventListener('mousemove',ev=>{if(!down)return;const dx=ev.clientX-down[0],dy=ev.clientY-down[1];
  if(!justPanned&&Math.abs(dx)+Math.abs(dy)<3)return;justPanned=true;svg.classList.add('panning');
  const sc=down[2][2]/svg.clientWidth;vb[0]=down[2][0]-dx*sc;vb[1]=down[2][1]-dy*sc;
  svg.setAttribute('viewBox',vb.join(' '));});
window.addEventListener('mouseup',()=>{down=null;svg.classList.remove('panning');
  if(justPanned)setTimeout(()=>justPanned=false,0);});
svg.addEventListener('wheel',ev=>{ev.preventDefault();if(!vb)return;const f=ev.deltaY>0?1.1:0.9;
  const mx=vb[0]+vb[2]*ev.offsetX/svg.clientWidth,my=vb[1]+vb[3]*ev.offsetY/svg.clientHeight;
  vb[0]=mx-(mx-vb[0])*f;vb[1]=my-(my-vb[1])*f;vb[2]*=f;vb[3]*=f;svg.setAttribute('viewBox',vb.join(' '));},{passive:false});

// 나누기: 배경(PNG/빈 곳) 클릭으로도 컷 점 찍기(폴리곤은 onRoom 처리) + 미리보기 선
svg.addEventListener('click',ev=>{
  if(mode!=='split'||!splitSel||cutPts.length>=2||justPanned)return;
  if(ev.target.tagName==='polygon')return;
  cutPts.push(evToUser(ev));
  if(cutPts.length===2)splitRoles=defaultRoles(G.rooms[splitSel].role);
  render();renderCtx();});
svg.addEventListener('mousemove',ev=>{
  if(mode!=='split'||!splitSel||cutPts.length!==1)return;
  const ln=document.getElementById('cutprev');if(!ln)return;
  const u=evToUser(ev);ln.setAttribute('x2',u[0]);ln.setAttribute('y2',u[1]);});

renderCtx();loadList('');
</script></body></html>"""


# ─────────────────────────────────────────────────────────────────────────────
class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        if p in ("/", "/index.html"):
            return self._send(200, _html(), "text/html; charset=utf-8")
        if p == "/api/graphs":
            qs = parse_qs(u.query)
            n = int(qs.get("n", ["250"])[0])
            q = (qs.get("q", [""])[0] or "").lower()
            done = set()
            if os.path.isdir(EDITS):
                done = {f[:-5] for f in os.listdir(EDITS) if f.endswith(".json")}
            ids = []
            try:
                with os.scandir(GRAPHS) as it:
                    for e in it:
                        nm = e.name
                        if nm.startswith("APT_") and nm.endswith(".json"):
                            ids.append(nm[:-5])
            except FileNotFoundError:
                pass
            total = len(ids)
            if q:
                ids = [i for i in ids if q in i.lower()]
            ids.sort()
            items = [{"id": i, "corrected": i in done} for i in ids[:n]]
            out = {"total": total, "corrected": len(done), "items": items}
            return self._send(200, json.dumps(out, ensure_ascii=False))
        if p.startswith("/api/graph/"):
            gid = p[len("/api/graph/"):]
            gp = _graph_path(gid)
            if not os.path.exists(gp):
                return self._send(404, json.dumps({"error": "not found"}))
            g = json.load(open(gp, encoding="utf-8"))
            g = _enrich_doors(g)
            return self._send(200, json.dumps({"graph": g, "status": _status(g)}, ensure_ascii=False))
        if p.startswith("/api/png/"):
            gid = p[len("/api/png/"):]
            data = _png_bytes(gid)
            if data is None:
                return self._send(404, b"", "image/png")
            return self._send(200, data, "image/png")
        return self._send(404, json.dumps({"error": "404"}))

    def do_POST(self):
        u = urlparse(self.path)
        ln = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(ln).decode("utf-8") if ln else "{}"
        if u.path == "/api/merge":
            try:
                body = json.loads(raw)
                g, err = _merge_nodes(body.get("graph") or {}, body.get("ids") or [])
                if err:
                    return self._send(200, json.dumps({"error": err}, ensure_ascii=False))
                return self._send(200, json.dumps({"graph": g, "status": _status(g)}, ensure_ascii=False))
            except Exception as e:  # noqa: BLE001
                return self._send(200, json.dumps({"error": f"merge: {e}"}, ensure_ascii=False))
        if u.path == "/api/split":
            try:
                body = json.loads(raw)
                from plan2graph.graph_edit import split_room
                g, err = split_room(body.get("graph") or {}, body.get("room_id"),
                                    body.get("cut") or [], body.get("roles") or [])
                if err:
                    return self._send(200, json.dumps({"error": err}, ensure_ascii=False))
                return self._send(200, json.dumps({"graph": g, "status": _status(g)}, ensure_ascii=False))
            except Exception as e:  # noqa: BLE001
                return self._send(200, json.dumps({"error": f"split: {e}"}, ensure_ascii=False))
        if u.path.startswith("/api/graph/"):
            gid = u.path[len("/api/graph/"):].replace("/", "_")
            g = json.loads(raw)
            g["corrected"] = True
            with open(os.path.join(EDITS, gid + ".json"), "w", encoding="utf-8") as f:
                json.dump(g, f, ensure_ascii=False)
            return self._send(200, json.dumps({"ok": True, "status": _status(g)}, ensure_ascii=False))
        return self._send(404, json.dumps({"error": "404"}))

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8600)
    ap.add_argument("--build-png-index", action="store_true", help="PNG 인덱스만 빌드하고 종료")
    a = ap.parse_args()
    if a.build_png_index:
        _build_png_index()
        print(f"[png-index] {_PNG_IDX_STATE} ({len(_PNG_IDX or {})} sigs) → {PNG_INDEX}")
        return
    threading.Thread(target=_build_png_index, daemon=True).start()   # 백그라운드 인덱스
    print(f"정보 보정 에디터 → http://localhost:{a.port}")
    print(f"  원본={GRAPHS}\n  작업={EDITS}\n  PNG캐시={PNG_CACHE}")
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
