#!/usr/bin/env python3
"""edit_server — 보정(편집) 웹 에디터 프로토타입 (의존성 0: stdlib http.server).

Streamlit이 약한 '클릭→즉시반영' 인터랙션을 웹 SVG로 시연:
  · 문 클릭 → 여는 방향(swing_dir_deg) 90° 회전, 즉시 다시 그림 (서버 왕복 없음)
  · 문 드래그 → 위치 이동
기존 gline 그래프(JSON)를 읽어 SVG로 렌더. 저장은 graphs/_edits/<id>.json (원본 미수정).

실행(서버):  PYTHONPATH=src python scripts/edit_server.py --port 8600
보기(로컬):  ssh -fN -L 8600:localhost:8600 ju@sse.aines.kr  →  http://localhost:8600
"""
import argparse
import glob
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
try:
    from plan2graph import topoedit
    GRAPHS = str(topoedit.GRAPHS_DIR)
except Exception:
    GRAPHS = os.path.expanduser("~/plan2graph/data/staging/gline/graphs")
EDITS = os.path.join(GRAPHS, "_edits")
os.makedirs(EDITS, exist_ok=True)

HTML = r"""<!doctype html><html><head><meta charset="utf-8"><title>보정 에디터(프로토타입)</title>
<style>
 *{box-sizing:border-box} body{font-family:system-ui,sans-serif;margin:0;display:flex;height:100vh}
 #side{width:260px;padding:12px;border-right:1px solid #e5e7eb;overflow:auto}
 #main{flex:1} svg{width:100%;height:100vh;background:#fafafa}
 .room{fill:#f3f4f6;stroke:#222;stroke-width:3}
 .wall-ext{stroke:#111;stroke-width:7} .wall-int{stroke:#666;stroke-width:3}
 .win{stroke:#2563eb;stroke-width:6}
 .door-leaf{stroke:#dc2626;stroke-width:4;fill:none}
 .door-arc{stroke:#f59e0b;stroke-width:2.5;fill:none;stroke-dasharray:5 4}
 .door-hit{fill:rgba(0,0,0,.001);cursor:pointer}
 .wall-hit{stroke:rgba(0,0,0,.001);stroke-width:16;cursor:move}
 .wpt{fill:#9ca3af;stroke:#fff;stroke-width:1.5;cursor:grab}
 text{font-size:22px;fill:#374151;pointer-events:none}
 select{width:100%} button{width:100%;padding:9px;margin:6px 0;cursor:pointer}
 .hint{font-size:12px;color:#6b7280;line-height:1.5}
 h3{margin:4px 0}
</style></head><body>
<div id="side">
 <h3>📝 보정 에디터 <span class=hint>프로토타입</span></h3>
 <div class=hint>도면 선택:</div>
 <select id="sel" size="18"></select>
 <button id="save">💾 저장 (_edits/)</button>
 <div id="status" class=hint></div>
 <hr>
 <div class=hint><b>문 클릭</b> = 여는 방향 90° 회전<br>
 <b>문 드래그</b> = 위치 이동<br>
 모두 <b>즉시 반영</b> · 서버 왕복 없음<br><br>
 <b>벽 드래그</b> = 평행이동 · <b>벽 끝점(회색점)</b> = 그 점만 이동<br><br>
 빨강=문 · 주황=swing 호 · 파랑=창 · 검정=외벽 · 회색점=벽 끝점</div>
</div>
<div id="main"><svg id="svg"></svg></div>
<script>
const NS='http://www.w3.org/2000/svg', svg=document.getElementById('svg');
let G=null,GID=null,dirty=false,dragItem=null,last=null,justDragged=false;
function el(t,a){const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);return e;}
function setStatus(s){document.getElementById('status').textContent=s+(dirty?'  ·  변경됨*':'');}
function svgPt(ev){const p=svg.createSVGPoint();p.x=ev.clientX;p.y=ev.clientY;
  const r=p.matrixTransform(svg.getScreenCTM().inverse());return [r.x,r.y];}
async function loadList(){
  const ids=await (await fetch('api/graphs?n=80')).json();
  const sel=document.getElementById('sel');sel.innerHTML='';
  ids.forEach(id=>{const o=document.createElement('option');o.value=id;o.textContent=id.replace('APT_FP_','');sel.appendChild(o);});
  sel.onchange=()=>loadGraph(sel.value);
  if(ids.length){sel.value=ids[0];loadGraph(ids[0]);}
}
async function loadGraph(id){G=await (await fetch('api/graph/'+id)).json();GID=id;dirty=false;render();setStatus('로드: '+id.replace('APT_FP_',''));}
function render(){
  while(svg.firstChild)svg.removeChild(svg.firstChild);
  const b=G.bbox_px||[0,0,1000,1000],pad=50;
  svg.setAttribute('viewBox',(b[0]-pad)+' '+(b[1]-pad)+' '+(b[2]+2*pad)+' '+(b[3]+2*pad));
  for(const id in (G.rooms||{})){const r=G.rooms[id],pg=r.polygon;if(!pg||pg.length<3)continue;
    svg.appendChild(el('polygon',{points:pg.map(p=>p[0]+','+p[1]).join(' '),class:'room'}));
    const c=r.centroid||pg[0];const t=el('text',{x:c[0],y:c[1],'text-anchor':'middle'});t.textContent=r.role||'';svg.appendChild(t);}
  (G.walls||[]).forEach(w=>drawWall(w));
  (G.windows||[]).forEach(w=>{const p=w.position;if(!p)return;const half=(w.width_px||30)/2;
    const deg=(typeof w.orientation==='number'?w.orientation:(w.orientation_deg||0))*Math.PI/180;
    const dx=half*Math.cos(deg),dy=half*Math.sin(deg);
    svg.appendChild(el('line',{x1:p[0]-dx,y1:p[1]-dy,x2:p[0]+dx,y2:p[1]+dy,class:'win'}));});
  (G.doors||[]).forEach((d,i)=>drawDoor(d,i));
}
function drawWall(w){const s=w.segment;if(!s||s.length<2)return;
  svg.appendChild(el('line',{x1:s[0][0],y1:s[0][1],x2:s[1][0],y2:s[1][1],class:w.type==='exterior'?'wall-ext':'wall-int'}));
  const hit=el('line',{x1:s[0][0],y1:s[0][1],x2:s[1][0],y2:s[1][1],class:'wall-hit'});
  hit.addEventListener('mousedown',ev=>{last=svgPt(ev);ev.stopPropagation();
    dragItem={label:'벽 이동',move:(dx,dy)=>{s[0][0]+=dx;s[0][1]+=dy;s[1][0]+=dx;s[1][1]+=dy;}};});
  svg.appendChild(hit);
  [0,1].forEach(k=>{const pt=el('circle',{cx:s[k][0],cy:s[k][1],r:7,class:'wpt'});
    pt.addEventListener('mousedown',ev=>{last=svgPt(ev);ev.stopPropagation();
      dragItem={label:'벽 끝점 이동',move:(dx,dy)=>{s[k][0]+=dx;s[k][1]+=dy;}};});
    svg.appendChild(pt);});
}
function drawDoor(d,i){
  const o=d.orientation||{},h=o.hinge||d.position||[0,0],deg=o.swing_dir_deg||0,R=o.radius_px||(d.width_px||40);
  const a=deg*Math.PI/180,lx=h[0]+R*Math.cos(a),ly=h[1]+R*Math.sin(a);
  svg.appendChild(el('line',{x1:h[0],y1:h[1],x2:lx,y2:ly,class:'door-leaf'}));
  const a2=a-Math.PI/2,sx=h[0]+R*Math.cos(a2),sy=h[1]+R*Math.sin(a2);
  svg.appendChild(el('path',{d:'M '+lx+' '+ly+' A '+R+' '+R+' 0 0 0 '+sx+' '+sy,class:'door-arc'}));
  svg.appendChild(el('circle',{cx:h[0],cy:h[1],r:5,fill:'#dc2626'}));
  const cx=d.position?d.position[0]:h[0],cy=d.position?d.position[1]:h[1];
  const hit=el('circle',{cx:cx,cy:cy,r:Math.max(R*0.8,20),class:'door-hit'});
  hit.addEventListener('mousedown',ev=>{last=svgPt(ev);ev.stopPropagation();
    dragItem={label:'문 이동',move:(dx,dy)=>{if(d.position){d.position[0]+=dx;d.position[1]+=dy;}
      const o2=d.orientation;if(o2&&o2.hinge){o2.hinge[0]+=dx;o2.hinge[1]+=dy;}}};});
  hit.addEventListener('click',ev=>{ev.stopPropagation();if(justDragged){justDragged=false;return;}
    const oo=d.orientation||{};oo.swing_dir_deg=(( (oo.swing_dir_deg||0)+90)%360);d.orientation=oo;
    dirty=true;render();setStatus('문 '+(d.id||i)+' 여는방향 → '+oo.swing_dir_deg.toFixed(0)+'°');});
  svg.appendChild(hit);
}
window.addEventListener('mousemove',ev=>{if(!dragItem)return;const p=svgPt(ev);
  const dx=p[0]-last[0],dy=p[1]-last[1];last=p;if(Math.abs(dx)+Math.abs(dy)<0.01)return;
  dragItem.move(dx,dy);dirty=true;justDragged=true;render();});
window.addEventListener('mouseup',()=>{if(dragItem){setStatus(dragItem.label||'이동');dragItem=null;}});
document.getElementById('save').onclick=async()=>{if(!G)return;
  await fetch('api/graph/'+GID,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(G)});
  dirty=false;setStatus('저장됨 → _edits/'+GID+'.json');};
loadList();
</script></body></html>"""


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
        if u.path in ("/", "/index.html"):
            return self._send(200, HTML, "text/html; charset=utf-8")
        if u.path == "/api/graphs":
            n = int(parse_qs(u.query).get("n", ["80"])[0])
            ids = [os.path.basename(f)[:-5]
                   for f in sorted(glob.glob(os.path.join(GRAPHS, "APT_*.json")))[:n]]
            return self._send(200, json.dumps(ids))
        if u.path.startswith("/api/graph/"):
            gid = u.path[len("/api/graph/"):]
            ep = os.path.join(EDITS, gid + ".json")
            p = ep if os.path.exists(ep) else os.path.join(GRAPHS, gid + ".json")
            if not os.path.exists(p):
                return self._send(404, "{}")
            return self._send(200, open(p, encoding="utf-8").read())
        return self._send(404, "{}")

    def do_POST(self):
        u = urlparse(self.path)
        if u.path.startswith("/api/graph/"):
            gid = u.path[len("/api/graph/"):].replace("/", "_")
            ln = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(ln).decode("utf-8")
            with open(os.path.join(EDITS, gid + ".json"), "w", encoding="utf-8") as f:
                f.write(data)
            return self._send(200, json.dumps({"ok": True, "saved": gid}))
        return self._send(404, "{}")

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8600)
    a = ap.parse_args()
    print(f"보정 에디터 → http://localhost:{a.port}  (graphs={GRAPHS}, edits={EDITS})")
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
