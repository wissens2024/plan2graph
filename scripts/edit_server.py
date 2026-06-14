#!/usr/bin/env python3
"""edit_server — AI-Hub 그래프 '정보 보정' 웹 주석 에디터 (ADR-0008).

SVG 폐기. 편집 대상 = 그래프 JSON 그 자체(= 최종 산출 스키마). 원본 PNG를 불변 배경으로
깔고([[inspect-original-first]]), 그 위 추출 폴리곤에 **의미**만 보정한다:
  · 방 클릭 → 역할 지정(거실/안방/알파룸/…)   · 문 클릭 → 여는 방향 90° 회전
  · 두 방 클릭 → 인접(문/개방) 토글            · 현관 지정
모든 편집은 브라우저에서 즉시 반영(서버 왕복 0), 도면당 1회 저장. Streamlit rerun 지연 없음.

폴더 분리(ADR-0008):
  data/staging/gline/graphs/    = 원본(자동변환) · 읽기전용
  data/staging/gline/corrected/ = 작업(사람 보정) · 저장 위치 (graphs/ 밖 = 회계캐시 무효화 회피)
  data/staging/gline/png/       = 원본 PNG 추출 캐시
  data/staging/gline/_png_index.json = sig→(zip,entry) 캐시(1회 빌드)

변환 게이트 표시: plan_quality.classify(=convert_plan GATE-0)로 '사용가능/보정필요+사유'를
인라인 표시 → 고침→재판정 닫힌 루프.

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
    GRAPHS = os.path.expanduser("~/plan2graph/data/staging/gline/graphs")
    ROLES = ["거실", "주방", "현관", "침실", "안방", "화장실", "욕실", "발코니",
             "드레스룸", "다목적공간", "복도", "전실", "기타", "알파룸"]
    ROLE_COLOR = {}

_BASE = os.path.dirname(GRAPHS)                 # data/staging/gline
CORRECTED = os.path.join(_BASE, "corrected")    # 작업본(graphs/ 밖)
PNG_CACHE = os.path.join(_BASE, "png")          # PNG 추출 캐시
PNG_INDEX = os.path.join(_BASE, "_png_index.json")
for d in (CORRECTED, PNG_CACHE):
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
    """APT_FP_<sig>_<...>_u<n> → sig (= APT_FP_ 다음 토큰)."""
    p = plan_id.split("_")
    return p[2] if len(p) >= 3 and p[1] == "FP" else None


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
    cp = os.path.join(CORRECTED, gid + ".json")
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
# HTML (의미주석 UI — PNG 배경 + 폴리곤 오버레이, 전부 클라이언트 사이드)
# ─────────────────────────────────────────────────────────────────────────────
def _html():
    pal = json.dumps(PALETTE, ensure_ascii=False)
    col = json.dumps(ROLE_COLOR, ensure_ascii=False)
    return r"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>정보 보정 에디터 (ADR-0008)</title>
<style>
 *{box-sizing:border-box} body{font-family:system-ui,sans-serif;margin:0;display:flex;height:100vh;overflow:hidden}
 #side{width:240px;padding:10px;border-right:1px solid #e5e7eb;overflow:auto;flex-shrink:0;font-size:13px}
 #main{flex:1;position:relative;background:#f4f4f5}
 svg{width:100%;height:100vh;display:block;cursor:grab}
 image{opacity:.92}
 .room{fill-opacity:.30;stroke:#111;stroke-width:2;cursor:pointer}
 .room.sel{stroke:#f59e0b;stroke-width:5}
 .room.adj{stroke:#7c3aed;stroke-width:5;stroke-dasharray:8 4}
 .rlabel{font-size:20px;fill:#111;font-weight:700;pointer-events:none;paint-order:stroke;stroke:#fff;stroke-width:4}
 .door-leaf{stroke:#dc2626;stroke-width:4;fill:none} .door-arc{stroke:#f59e0b;stroke-width:2.5;fill:none;stroke-dasharray:5 4}
 .door-q{fill:none;stroke:#dc2626;stroke-width:3;stroke-dasharray:4 3} .door-hit{fill:rgba(0,0,0,.001);cursor:pointer}
 .ent{fill:none;stroke:#d9534f;stroke-width:5}
 select,button{width:100%;padding:7px;margin:4px 0;cursor:pointer;font-size:13px}
 #pal button{width:auto;display:inline-block;margin:2px;padding:5px 8px;border:1px solid #ccc;border-radius:4px}
 .hint{font-size:11px;color:#6b7280;line-height:1.5} h3,h4{margin:6px 0}
 #stat{padding:8px;border-radius:6px;font-size:13px;font-weight:700;margin:6px 0}
 .ok{background:#dcfce7;color:#166534} .bad{background:#fee2e2;color:#991b1b} .na{background:#f3f4f6;color:#555}
 #toast{position:absolute;top:10px;left:50%;transform:translateX(-50%);background:#111;color:#fff;padding:6px 12px;border-radius:6px;font-size:13px;opacity:0;transition:.2s;pointer-events:none}
 #pngwarn{position:absolute;bottom:8px;left:8px;background:#fef3c7;color:#92400e;padding:4px 8px;border-radius:5px;font-size:12px;display:none}
 .mode{display:flex;gap:4px} .mode button{flex:1}
 .mode button.on{background:#111;color:#fff}
</style></head><body>
<div id="side">
 <h3>📝 정보 보정 <span class=hint>ADR-0008</span></h3>
 <div class="mode">
   <button id="mRole" class="on" title="방 클릭→역할">역할</button>
   <button id="mAdj" title="두 방 클릭→인접">인접</button>
 </div>
 <div class=hint id="modehint">방을 클릭하고 아래 역할 선택(숫자키도 가능)</div>
 <h4>도면</h4>
 <select id="sel" size="10"></select>
 <div class="mode"><button id="prev">◀ 이전</button><button id="next">다음 ▶</button></div>
 <div id="stat" class="na">—</div>
 <button id="save">💾 저장 (corrected/)</button>
 <h4>역할 팔레트</h4>
 <div id="pal"></div>
 <hr>
 <div class=hint>
  <b>역할 모드</b>: 방 클릭→선택→팔레트(또는 1~9,0,a..f 단축키)<br>
  <b>인접 모드</b>: 방 A→방 B 클릭 = 문↔개방↔없음 순환<br>
  <b>문</b> 클릭 = 여는 방향 90°<br>
  <b>E</b>키 = 선택 방을 현관 지정<br>
  빨강=문 · 빨강점선?=방향미상 · 보라점선=인접 · 빨강테=현관<br>
  휠=확대 · 드래그=이동
 </div>
</div>
<div id="main"><svg id="svg"></svg><div id="toast"></div><div id="pngwarn">⚠ 원본 PNG 없음(인덱싱중일 수 있음) — 해석만 표시</div></div>
<script>
const NS='http://www.w3.org/2000/svg', svg=document.getElementById('svg');
const PALETTE=__PAL__, ROLE_COLOR=__COL__;
let G=null,GID=null,dirty=false,mode='role',sel=null,adjA=null,vb=null;
function el(t,a,p){const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);if(p)p.appendChild(e);return e;}
function toast(s){const t=document.getElementById('toast');t.textContent=s;t.style.opacity=1;clearTimeout(t._);t._=setTimeout(()=>t.style.opacity=0,1400);}
function setDirty(d){dirty=d;document.getElementById('save').textContent=d?'💾 저장* (corrected/)':'💾 저장 (corrected/)';}
function colorOf(r){return ROLE_COLOR[r]||'#bcbcbc';}
async function loadList(){
  const ids=await (await fetch('api/graphs?n=300')).json();
  const s=document.getElementById('sel');const cur=s.value;s.innerHTML='';
  ids.forEach(o=>{const op=document.createElement('option');op.value=o.id;op.textContent=(o.corrected?'✔ ':'· ')+o.id.replace('APT_FP_','');s.appendChild(op);});
  if(cur)s.value=cur;
  s.onchange=()=>{vb=null;loadGraph(s.value);};
  if(!GID&&ids.length){s.value=ids[0].id;loadGraph(ids[0].id);}
}
async function loadGraph(id){
  const r=await (await fetch('api/graph/'+id)).json();
  if(r.error){toast('로드 실패: '+r.error);return;}
  G=r.graph;GID=id;sel=null;adjA=null;setDirty(false);
  document.getElementById('sel').value=id;
  showStatus(r.status);render();
}
function showStatus(st){
  const e=document.getElementById('stat');
  if(!st||st.clean===null){e.className='na';e.textContent='판정불가';return;}
  if(st.clean){e.className='ok';e.textContent='✅ 사용가능 (변환 통과)';}
  else{e.className='bad';e.textContent='❌ 보정필요: '+(st.reasons||[]).join(', ');}
}
function bbox(){const b=G.bbox_px||[0,0,1000,1000];const pad=Math.max(b[2],b[3])*0.06;
  return [b[0]-pad,b[1]-pad,b[2]+2*pad,b[3]+2*pad];}
function render(){
  while(svg.firstChild)svg.removeChild(svg.firstChild);
  if(!vb)vb=bbox();
  svg.setAttribute('viewBox',vb.join(' '));
  const img=el('image',{href:'api/png/'+GID,x:0,y:0},svg);
  img.setAttribute('onerror',"document.getElementById('pngwarn').style.display='block'");
  img.addEventListener('load',()=>document.getElementById('pngwarn').style.display='none');
  for(const id in (G.rooms||{})){const r=G.rooms[id],pg=r.polygon;if(!pg||pg.length<3)continue;
    const cls='room'+(sel===id?' sel':'')+(adjA===id?' adj':'');
    const po=el('polygon',{points:pg.map(p=>p[0]+','+p[1]).join(' '),class:cls,fill:colorOf(r.role),'data-id':id},svg);
    po.addEventListener('click',ev=>{ev.stopPropagation();onRoom(id);});
    const c=r.centroid||pg[0];
    const t=el('text',{x:c[0],y:c[1],'text-anchor':'middle',class:'rlabel'},svg);t.textContent=r.role||'?';
  }
  (G.edges||[]).forEach(e=>{const a=G.rooms[e.from||e[0]],b=G.rooms[e.to||e[1]];
    const ca=a&&a.centroid,cb=b&&b.centroid;if(!ca||!cb)return;const via=e.via||e[2];
    el('line',{x1:ca[0],y1:ca[1],x2:cb[0],y2:cb[1],stroke:via==='door'?'#dc2626':'#3b82f6',
      'stroke-width':2,'stroke-dasharray':via==='door'?'':'6 5',opacity:.5},svg);});
  (G.doors||[]).forEach((d,i)=>drawDoor(d,i));
  for(const id in (G.rooms||{})){if((G.rooms[id].role||'')==='현관'){const c=G.rooms[id].centroid;if(c)el('circle',{cx:c[0],cy:c[1],r:18,class:'ent'},svg);}}
}
function drawDoor(d,i){
  const o=d.orientation||{},has=(o.swing_dir_deg!=null&&o.hinge),h=o.hinge||d.position||[0,0],R=o.radius_px||(d.width_px||40);
  if(has){const a=o.swing_dir_deg*Math.PI/180,lx=h[0]+R*Math.cos(a),ly=h[1]+R*Math.sin(a);
    el('line',{x1:h[0],y1:h[1],x2:lx,y2:ly,class:'door-leaf'},svg);
    const a2=a-Math.PI/2;el('path',{d:'M '+lx+' '+ly+' A '+R+' '+R+' 0 0 0 '+(h[0]+R*Math.cos(a2))+' '+(h[1]+R*Math.sin(a2)),class:'door-arc'},svg);
    el('circle',{cx:h[0],cy:h[1],r:5,fill:'#dc2626'},svg);
  }else{const p=d.position||h;el('circle',{cx:p[0],cy:p[1],r:10,class:'door-q'},svg);
    const t=el('text',{x:p[0],y:p[1]-13,'text-anchor':'middle',fill:'#dc2626','font-size':22},svg);t.textContent='?';}
  const p=d.position||h;const hit=el('circle',{cx:p[0],cy:p[1],r:Math.max(R*0.8,18),class:'door-hit'},svg);
  hit.addEventListener('click',ev=>{ev.stopPropagation();const oo=d.orientation||{};
    if(!oo.hinge)oo.hinge=(d.position?[d.position[0],d.position[1]]:[h[0],h[1]]);
    if(oo.radius_px==null)oo.radius_px=R;oo.swing_dir_deg=(((oo.swing_dir_deg||0)+90)%360);d.orientation=oo;
    setDirty(true);render();toast('문 여는방향 → '+oo.swing_dir_deg.toFixed(0)+'°');});
}
function onRoom(id){
  if(mode==='role'){sel=id;render();}
  else{if(adjA===null){adjA=id;render();toast('A='+(G.rooms[id].role||id)+' — B 클릭');}
    else if(adjA===id){adjA=null;render();}
    else{toggleAdj(adjA,id);adjA=null;render();}}
}
function toggleAdj(a,b){
  G.edges=G.edges||[];
  const idx=G.edges.findIndex(e=>{const f=e.from||e[0],t=e.to||e[1];return (f==a&&t==b)||(f==b&&t==a);});
  if(idx<0){G.edges.push({from:a,to:b,via:'door'});toast('인접 추가: 문');}
  else{const e=G.edges[idx];const via=e.via||e[2];
    if(via==='door'){if(Array.isArray(e))e[2]='open';else e.via='open';toast('인접: 개방');}
    else{G.edges.splice(idx,1);toast('인접 제거');}}
  setDirty(true);
}
function setRole(role){if(!sel){toast('먼저 방을 클릭');return;}G.rooms[sel].role=role;setDirty(true);render();toast(role);}
const palBox=document.getElementById('pal');
PALETTE.forEach((r,i)=>{const b=document.createElement('button');
  const key=i<9?(i+1):(i===9?'0':String.fromCharCode(97+i-10));
  b.textContent=r+' ('+key+')';b.style.borderLeft='6px solid '+colorOf(r);
  b.onclick=()=>setRole(r);palBox.appendChild(b);});
document.addEventListener('keydown',ev=>{
  if(ev.target.tagName==='SELECT')return;
  if(ev.key==='e'||ev.key==='E'){if(sel){G.rooms[sel].role='현관';setDirty(true);render();toast('현관 지정');}return;}
  let i=-1;if(ev.key>='1'&&ev.key<='9')i=+ev.key-1;else if(ev.key==='0')i=9;
  else if(ev.key>='a'&&ev.key<='f')i=10+ev.key.charCodeAt(0)-97;
  if(i>=0&&i<PALETTE.length)setRole(PALETTE[i]);
});
document.getElementById('mRole').onclick=()=>{mode='role';adjA=null;document.getElementById('mRole').classList.add('on');document.getElementById('mAdj').classList.remove('on');document.getElementById('modehint').textContent='방 클릭→역할 선택(숫자키)';render();};
document.getElementById('mAdj').onclick=()=>{mode='adj';sel=null;document.getElementById('mAdj').classList.add('on');document.getElementById('mRole').classList.remove('on');document.getElementById('modehint').textContent='방 A→방 B 클릭 = 문↔개방↔없음';render();};
function move(d){const s=document.getElementById('sel');const n=s.selectedIndex+d;if(n>=0&&n<s.options.length){s.selectedIndex=n;vb=null;loadGraph(s.value);}}
document.getElementById('prev').onclick=()=>move(-1);
document.getElementById('next').onclick=()=>move(1);
document.getElementById('save').onclick=async()=>{if(!G)return;
  const r=await (await fetch('api/graph/'+GID,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(G)})).json();
  setDirty(false);if(r.status)showStatus(r.status);toast('저장됨 → corrected/');loadList();};
let pan=null;
svg.addEventListener('mousedown',ev=>{if(ev.target.tagName==='polygon'||ev.target.classList.contains('door-hit'))return;pan=[ev.clientX,ev.clientY,vb.slice()];});
window.addEventListener('mousemove',ev=>{if(!pan||!vb)return;const sc=vb[2]/svg.clientWidth;
  vb[0]=pan[2][0]-(ev.clientX-pan[0])*sc;vb[1]=pan[2][1]-(ev.clientY-pan[1])*sc;svg.setAttribute('viewBox',vb.join(' '));});
window.addEventListener('mouseup',()=>pan=null);
svg.addEventListener('wheel',ev=>{ev.preventDefault();if(!vb)return;const f=ev.deltaY>0?1.1:0.9;
  const mx=vb[0]+vb[2]*ev.offsetX/svg.clientWidth,my=vb[1]+vb[3]*ev.offsetY/svg.clientHeight;
  vb[0]=mx-(mx-vb[0])*f;vb[1]=my-(my-vb[1])*f;vb[2]*=f;vb[3]*=f;svg.setAttribute('viewBox',vb.join(' '));},{passive:false});
loadList();
</script></body></html>""".replace("__PAL__", pal).replace("__COL__", col)


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
            n = int(parse_qs(u.query).get("n", ["300"])[0])
            done = set()
            if os.path.isdir(CORRECTED):
                done = {f[:-5] for f in os.listdir(CORRECTED) if f.endswith(".json")}
            ids = []
            try:
                with os.scandir(GRAPHS) as it:
                    for e in it:
                        nm = e.name
                        if nm.startswith("APT_") and nm.endswith(".json"):
                            ids.append(nm[:-5])
            except FileNotFoundError:
                pass
            ids.sort()
            out = [{"id": i, "corrected": i in done} for i in ids[:n]]
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
        if u.path.startswith("/api/graph/"):
            gid = u.path[len("/api/graph/"):].replace("/", "_")
            ln = int(self.headers.get("Content-Length", 0))
            g = json.loads(self.rfile.read(ln).decode("utf-8"))
            g["corrected"] = True
            with open(os.path.join(CORRECTED, gid + ".json"), "w", encoding="utf-8") as f:
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
    print(f"  원본={GRAPHS}\n  작업={CORRECTED}\n  PNG캐시={PNG_CACHE}")
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
