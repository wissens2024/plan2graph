"""dedup_review — 에디터 '🔁 중복 검수' 화면(페이지 + API).

같은 도면(사본)들을 한 화면에 묶어 보여주고, 서로 다른 보정(변종)을 나란히 비교해
맞는 하나를 골라 그룹 전체에 복사(전파)한다.

edit_server 에 얹는 방식: 라우팅만 위임받는 자립 모듈. 기존 에디터 화면·저장 경로는
건드리지 않는다(에디터 UI 회귀 위험 차단).

    GET  /dedup                        검수 화면
    GET  /api/dedup/groups?...         그룹 목록(요약 포함)
    GET  /api/dedup/group/<sig>        그룹 상세(변종별 대표·멤버)
    GET  /api/dedup/find/<plan_id>     이 도면이 속한 그룹 sig
    GET  /api/dedup/thumb/<plan_id>    원본 PNG를 세대 영역만 잘라 축소(썸네일)
    POST /api/dedup/propagate          전파 실행

인덱스는 scripts/dedup_index.py 가 만든 data/staging/dedup_index.json.
전파는 좌표 평행이동·미러 변환(plan2graph.dedup.transform_graph) + 정렬검증.
정렬오차가 tol 을 넘으면 그 대상은 **자동 스킵**(잘못 묶인 그룹 보호).
"""
import json
import os
import shutil
import threading
import time

from plan2graph.dedup import (alignment_error, corr_cells, same_correction, shape_sig,
                              transform_graph)

_IDX = None
_IDX_MTIME = None
_ALIGN_TOL = 2.0          # 전파 허용 정렬오차(px). 같은 도면이면 실측 0.0px.

# 원본 시트 PNG는 장당 ~17MB — 카드 24장이면 400MB라 그대로는 못 쓴다. 세대 bbox만
# 잘라 축소해 캐시한다(장당 ~50KB). PAD_F 는 클라이언트 viewBox 여백과 **반드시 동일**해야
# 오버레이 폴리곤과 배경이 어긋나지 않는다.
PAD_F = 0.05
_THUMB_W = 520
_DECODE_SEM = threading.Semaphore(4)      # 대용량 PNG 동시 디코딩 제한(메모리·CPU 보호)


def _index_path(ctx):
    return ctx["INDEX"]


def load_index(ctx, force=False):
    """dedup_index.json 지연 로드(mtime 바뀌면 자동 재로드)."""
    global _IDX, _IDX_MTIME
    p = _index_path(ctx)
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return None
    if force or _IDX is None or _IDX_MTIME != mt:
        with open(p, encoding="utf-8") as fh:
            _IDX = json.load(fh)
        _IDX_MTIME = mt
    return _IDX


def _roles_str(roles):
    return " ".join(f"{k}×{v}" if v > 1 else k
                    for k, v in sorted(roles.items(), key=lambda kv: (-kv[1], kv[0])))


def _roles_diff(a, b):
    """다수안 a 대비 b의 차이 → (b에 더 있는 것, b에 빠진 것)."""
    plus, minus = [], []
    for k in sorted(set(a) | set(b)):
        d = b.get(k, 0) - a.get(k, 0)
        if d > 0:
            plus.append(f"+{k}{'×'+str(d) if d > 1 else ''}")
        elif d < 0:
            minus.append(f"−{k}{'×'+str(-d) if d < -1 else ''}")
    return plus, minus


# ─────────────────────────────────────────────────────────────────────────────
# 썸네일 — 원본 시트 PNG에서 세대 영역만 잘라 축소
# ─────────────────────────────────────────────────────────────────────────────
def _pad_box(b):
    """그래프 bbox_px → 여백 포함 크롭 사각형 (x0, y0, x1, y1). 클라이언트 viewBox와 동일 계산."""
    pad = max(b[2], b[3]) * PAD_F
    return (b[0] - pad, b[1] - pad, b[0] + b[2] + pad, b[1] + b[3] + pad)


def thumb_png(ctx, gid, w=_THUMB_W):
    """세대 영역 크롭 썸네일 bytes. 디스크 캐시. 없으면 None."""
    cache_dir = os.path.join(os.path.dirname(ctx["EDITS"]), "_dedup_thumb")
    cache = os.path.join(cache_dir, f"{gid}_{w}.png")
    if os.path.exists(cache):
        with open(cache, "rb") as fh:
            return fh.read()
    gp = os.path.join(ctx["EDITS"], gid + ".json")
    if not os.path.exists(gp):
        gp = os.path.join(ctx["GRAPHS"], gid + ".json")
    if not os.path.exists(gp):
        return None
    try:
        with open(gp, encoding="utf-8") as fh:
            b = (json.load(fh) or {}).get("bbox_px")
        if not b or len(b) < 4:
            return None
        raw = ctx["PNG"](gid)
        if not raw:
            return None
        import io
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None          # 대형 도면 시트 = 정상 입력(폭탄 경고 아님)
        with _DECODE_SEM:
            im = Image.open(io.BytesIO(raw)).convert("RGBA")
            x0, y0, x1, y1 = (int(round(v)) for v in _pad_box(b))
            # 경계를 넘어가면 PIL이 투명으로 채운다 → 좌표 정합이 유지된다(클램프 금지).
            crop = im.crop((x0, y0, x1, y1))
            cw = max(1, x1 - x0)
            if cw > w:
                crop = crop.resize((w, max(1, round((y1 - y0) * w / cw))), Image.LANCZOS)
            buf = io.BytesIO()
            crop.save(buf, "PNG", optimize=True)
            im.close()
        data = buf.getvalue()
        os.makedirs(cache_dir, exist_ok=True)
        tmp = cache + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, cache)
        return data
    except Exception:  # noqa: BLE001
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 전파
# ─────────────────────────────────────────────────────────────────────────────
def propagate(ctx, sig, source, mode="fill", limit=None):
    """source 의 보정을 그룹 sig 의 형제 도면에 복사.

    mode="fill"  : 아직 보정 없는 형제에만 채운다. 기존 사람 보정은 절대 안 건드림.
    mode="unify" : 위 + **다르게 보정된** 형제까지 이 보정으로 덮어씀(백업 후).
                   이미 같은 보정인 형제는 건너뛴다 — 내용이 같은데 덮어쓰면 사람 보정
                   이력이 propagated 로 바뀌고 백업만 쌓이기 때문.

    반환 {written, skipped, overwritten, backup, errors}
    """
    idx = load_index(ctx)
    if not idx:
        return {"error": "dedup_index.json 없음 — scripts/dedup_index.py 먼저 실행"}
    grp = (idx.get("groups") or {}).get(sig)
    if not grp:
        return {"error": f"그룹 없음: {sig}"}
    GRAPHS, EDITS = ctx["GRAPHS"], ctx["EDITS"]
    src_edit = os.path.join(EDITS, source + ".json")
    if not os.path.exists(src_edit):
        return {"error": f"원본 보정본이 없음: {source}"}
    try:
        with open(os.path.join(GRAPHS, source + ".json"), encoding="utf-8") as fh:
            src_parsed = json.load(fh)
        with open(src_edit, encoding="utf-8") as fh:
            src_corr = json.load(fh)
    except Exception as e:  # noqa: BLE001
        return {"error": f"원본 로드 실패: {e}"}
    _s, t_src = shape_sig(src_parsed)
    if not t_src:
        return {"error": "원본 도형 서명 실패"}

    have = {m for m in grp["members"] if os.path.exists(os.path.join(EDITS, m + ".json"))}
    targets = [m for m in grp["members"] if m != source]
    if mode == "fill":
        targets = [m for m in targets if m not in have]
    if limit:
        targets = targets[:int(limit)]

    src_cells, _roles = corr_cells(src_corr, t_src)     # unify 시 '이미 같은 보정' 판별용
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(os.path.dirname(EDITS), "_dedup_backup", f"{stamp}_{sig[:8]}")
    written, skipped, overwritten, errors = [], [], [], []
    for tid in targets:
        tp = os.path.join(GRAPHS, tid + ".json")
        if not os.path.exists(tp):
            skipped.append([tid, "원본 없음"])
            continue
        try:
            with open(tp, encoding="utf-8") as fh:
                tgt_parsed = json.load(fh)
            _s2, t_tgt = shape_sig(tgt_parsed)
            err = alignment_error(src_parsed, tgt_parsed, t_src, t_tgt)
            if not (err <= _ALIGN_TOL):        # inf/NaN 도 여기서 걸림
                skipped.append([tid, f"정렬오차 {err:.1f}px"])
                continue
            dst = os.path.join(EDITS, tid + ".json")
            if mode == "unify" and os.path.exists(dst):
                try:
                    with open(dst, encoding="utf-8") as fh:
                        cur, _r = corr_cells(json.load(fh), t_tgt)
                    if same_correction(src_cells, cur, 10.0):
                        skipped.append([tid, "이미 같은 보정"])
                        continue
                except Exception:  # noqa: BLE001
                    pass                       # 못 읽으면 그냥 덮어쓴다(백업은 아래서 뜸)
            g = transform_graph(src_corr, t_src, t_tgt, tid)
            meta = g.setdefault("meta", {})
            # meta.propagated 는 옛 표본 전파(2026-08-05, 12건)와 공통인 유일한 플래그 —
            # 옛 것은 correction_source 에 '출처 plan_id'를, 새 것은 문자열 'propagated'를
            # 넣어 같은 키가 두 의미로 쓰인다. 판별은 항상 meta.propagated 로 할 것.
            meta["propagated"] = True
            meta["correction_source"] = "propagated"
            meta["propagated_from"] = source
            meta["propagated_at"] = stamp
            meta["dedup_group"] = sig
            g["corrected"] = True
            if os.path.exists(dst):            # 사람 보정 덮어쓰기 = 반드시 백업
                os.makedirs(backup, exist_ok=True)
                shutil.copy2(dst, os.path.join(backup, tid + ".json"))
                overwritten.append(tid)
            with open(dst, "w", encoding="utf-8") as fh:
                json.dump(g, fh, ensure_ascii=False)
            written.append(tid)
        except Exception as e:  # noqa: BLE001
            errors.append([tid, str(e)])

    # 인덱스 즉시 반영(화면이 바로 갱신되게). 정확한 재계산은 dedup_index.py 재실행.
    if written:
        done = have | set(written) | {source}
        grp["corrected"] = sorted(m for m in grp["members"] if m in done)
        grp["pending"] = len(grp["members"]) - len(grp["corrected"])
        if mode == "unify":
            keep = next((v for v in (grp.get("variants") or [])
                         if source in v.get("members", [])), None)
            if keep:
                keep["members"] = list(grp["corrected"])
                keep["count"] = len(keep["members"])
                grp["variants"] = [keep]
                grp["status"] = "agree" if keep["count"] > 1 else "single"
        grp["stale"] = True
        try:
            with open(_index_path(ctx), "w", encoding="utf-8") as fh:
                json.dump(_IDX, fh, ensure_ascii=False)
            global _IDX_MTIME
            _IDX_MTIME = os.path.getmtime(_index_path(ctx))
        except Exception:  # noqa: BLE001
            pass
    return {"written": len(written), "skipped": skipped[:50], "n_skipped": len(skipped),
            "overwritten": len(overwritten), "errors": errors[:20],
            "backup": backup if overwritten else None}


# ─────────────────────────────────────────────────────────────────────────────
# 라우팅
# ─────────────────────────────────────────────────────────────────────────────
def handle_get(path, qs, ctx):
    """(status, body, ctype) 또는 None(=내 담당 아님)."""
    if path == "/dedup":
        return 200, page_html(ctx), "text/html; charset=utf-8"
    if not path.startswith("/api/dedup"):
        return None
    if path.startswith("/api/dedup/thumb/"):     # 인덱스 없이도 동작(그림만 보는 경로)
        gid = path[len("/api/dedup/thumb/"):]
        data = thumb_png(ctx, gid, int(qs.get("w", [_THUMB_W])[0]))
        if data is None:
            return 404, b"", "image/png"
        return 200, data, "image/png"
    idx = load_index(ctx)
    if idx is None:
        return 200, json.dumps({"error": "dedup_index.json 없음 — "
                                "서버에서 `python scripts/dedup_index.py` 실행 필요"},
                               ensure_ascii=False), "application/json"

    if path == "/api/dedup/groups":
        status = (qs.get("status", ["conflict"])[0] or "conflict")
        sort = qs.get("sort", ["corrected"])[0]
        n = int(qs.get("n", ["60"])[0])
        offset = int(qs.get("offset", ["0"])[0])
        house_f = qs.get("house", [""])[0]
        scope_f = qs.get("scope", [""])[0]
        rows, seen = [], {"conflict": 0, "agree": 0, "single": 0, "none": 0}
        for s, r in idx["groups"].items():
            if house_f and r.get("house") != house_f:
                continue
            if scope_f and r.get("scope") != scope_f:
                continue
            seen[r.get("status")] = seen.get(r.get("status"), 0) + 1   # 필터 적용 후 요약
            if status != "all" and r.get("status") != status:
                continue
            rows.append({"sig": s, "n": r["n"], "corrected": len(r.get("corrected") or []),
                         "house": r.get("house"), "scope": r.get("scope"),
                         "variants": len(r.get("variants") or []),
                         "pending": r.get("pending", 0),
                         "entrance": r.get("n_entrance", 0), "verts": r.get("verts", 0),
                         "status": r.get("status"), "stale": r.get("stale", False)})
        key = {"corrected": lambda x: (-x["corrected"], -x["variants"]),
               "variants": lambda x: (-x["variants"], -x["corrected"]),
               "pending": lambda x: (-x["pending"], -x["corrected"]),
               "members": lambda x: (-x["n"],)}.get(sort, lambda x: (-x["corrected"],))
        rows.sort(key=key)
        total = len(rows)
        return 200, json.dumps({"total": total, "offset": offset, "n": n,
                                "items": rows[offset:offset + n],
                                "summary": {"n_groups": sum(seen.values()),
                                             "n_conflict": seen.get("conflict", 0),
                                             "n_agree": seen.get("agree", 0),
                                             "n_single": seen.get("single", 0),
                                             "n_none": seen.get("none", 0),
                                             "built_at": idx.get("built_at")}},
                               ensure_ascii=False), "application/json"

    if path.startswith("/api/dedup/group/"):
        sig = path[len("/api/dedup/group/"):]
        r = idx["groups"].get(sig)
        if not r:
            return 404, json.dumps({"error": "그룹 없음"}, ensure_ascii=False), "application/json"
        variants = list(r.get("variants") or [])
        base = variants[0]["roles"] if variants else {}
        out = []
        for i, v in enumerate(variants):
            plus, minus = _roles_diff(base, v["roles"]) if i else ([], [])
            out.append({"rep": v["members"][0], "count": v["count"], "held": v.get("held", 0),
                        "members": v["members"][:200], "n_rooms": v.get("n_rooms"),
                        "roles": _roles_str(v["roles"]), "plus": plus, "minus": minus})
        uncorrected = [m for m in r["members"] if m not in set(r.get("corrected") or [])]
        return 200, json.dumps({"sig": sig, "n": r["n"], "status": r.get("status"),
                                "pending": r.get("pending", 0),
                                "corrected": len(r.get("corrected") or []),
                                "entrance": r.get("n_entrance", 0),
                                "verts": r.get("verts", 0),
                                "house": r.get("house"), "scope": r.get("scope"),
                                "parsed_ent": r.get("parsed_ent", 1),
                                "stale": r.get("stale", False),
                                "variants": out,
                                "uncorrected": uncorrected[:200]},
                               ensure_ascii=False), "application/json"

    if path.startswith("/api/dedup/find/"):
        pid = path[len("/api/dedup/find/"):]
        sig = (idx.get("sig_of") or {}).get(pid)
        return 200, json.dumps({"sig": sig}, ensure_ascii=False), "application/json"
    return 404, json.dumps({"error": "404"}), "application/json"


def handle_post(path, raw, ctx):
    if path != "/api/dedup/propagate":
        return None
    try:
        b = json.loads(raw or "{}")
        out = propagate(ctx, b.get("sig"), b.get("source"),
                        b.get("mode", "fill"), b.get("limit"))
    except Exception as e:  # noqa: BLE001
        out = {"error": f"propagate: {e}"}
    return 200, json.dumps(out, ensure_ascii=False), "application/json"


def page_html(ctx):
    return _PAGE.replace("__COL__", json.dumps(ctx.get("ROLE_COLOR") or {}, ensure_ascii=False))


# 클라이언트 URL은 전부 **상대경로**. nginx가 /editor/ 하위로 프록시하므로(docs/nginx_editor.md)
# 절대경로(/api/...)를 쓰면 도메인 루트로 나가 404가 된다. 로컬 터널(:8600/dedup)에서도 동일 동작.
_PAGE = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>중복 도면 검수</title>
<style>
:root{--bg:#0f1319;--panel:#171d26;--line:#2a3340;--fg:#dde3ec;--dim:#8b95a5;--acc:#4a9eff;
      --warn:#ffb454;--bad:#ff6b6b;--ok:#4ade80}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:13px/1.5 system-ui,"맑은 고딕",sans-serif}
header{display:flex;gap:14px;align-items:center;flex-wrap:wrap;padding:10px 14px;
       background:var(--panel);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:9}
h1{font-size:15px;margin:0}
a{color:var(--acc)}
.badge{background:#1f2733;border:1px solid var(--line);border-radius:12px;padding:2px 9px;font-size:12px}
.badge b{color:var(--warn)}
select,input,button{background:#1f2733;color:var(--fg);border:1px solid var(--line);
                    border-radius:6px;padding:5px 8px;font:inherit}
button{cursor:pointer}
button:hover{border-color:var(--acc)}
button.primary{background:#1d4ed8;border-color:#1d4ed8;color:#fff}
button.danger{background:#7f1d1d;border-color:#991b1b;color:#fee}
button:disabled{opacity:.45;cursor:not-allowed}
main{display:grid;grid-template-columns:270px 1fr;height:calc(100vh - 49px)}
#list{overflow:auto;border-right:1px solid var(--line);background:#131820}
#list .row{padding:7px 10px;border-bottom:1px solid #1c2430;cursor:pointer;font-size:12px}
#list .row:hover{background:#1a212c}
#list .row.sel{background:#1e3a5f}
#list .row .sig{color:var(--dim);font-family:ui-monospace,monospace;font-size:11px}
#list .row .num b{color:var(--warn)}
#detail{overflow:auto;padding:14px}
.gh{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px}
.card.top{border-color:#2f6feb}
.card h3{margin:0 0 4px;font-size:13px;display:flex;justify-content:space-between;gap:8px}
.card .pid{font-family:ui-monospace,monospace;font-size:11px;color:var(--acc);
           word-break:break-all;cursor:pointer}
.card .roles{font-size:11px;color:var(--dim);margin:6px 0;line-height:1.45}
.card .diff{font-size:11px;margin:4px 0}
.diff .p{color:var(--ok)} .diff .m{color:var(--bad)}
svg.thumb{width:100%;height:210px;background:#fff;border-radius:6px;display:block}
.tools{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}
.tools button{font-size:11px;padding:4px 7px}
.note{color:var(--dim);font-size:12px;margin:8px 0}
.warn{color:var(--warn)}
#toast{position:fixed;right:16px;bottom:16px;background:#1f2733;border:1px solid var(--acc);
       border-radius:8px;padding:10px 14px;max-width:460px;display:none;white-space:pre-wrap;z-index:20}
</style></head><body>
<header>
  <h1>🔁 중복 도면 검수</h1>
  <span id="sum" class="badge">…</span>
  <label>주거 <select id="fhouse">
    <option value="APT">APT(아파트)</option>
    <option value="">전체</option>
    <option value="DEH">DEH(단독)</option>
    <option value="ROW">ROW(연립)</option>
  </select></label>
  <label>평면 <select id="fscope">
    <option value="unit">1세대 평면도</option>
    <option value="">전체</option>
    <option value="floor">층 평면도</option>
    <option value="unknown">판정보류(현관2+·미보정)</option>
  </select></label>
  <label>상태 <select id="fstatus">
    <option value="conflict">불일치(보정이 서로 다름)</option>
    <option value="single">보정 1건(비교 불가)</option>
    <option value="agree">일치</option>
    <option value="all">전체</option>
  </select></label>
  <label>정렬 <select id="fsort">
    <option value="corrected">보정 많은 순</option>
    <option value="variants">변종 많은 순</option>
    <option value="pending">미보정 많은 순</option>
    <option value="members">사본 많은 순</option>
  </select></label>
  <input id="q" placeholder="도면번호로 그룹 찾기" size="30">
  <label style="font-size:12px"><input type="checkbox" id="bg" checked> 원본 PNG</label>
  <a href="./" target="_blank">에디터 →</a>
</header>
<main>
  <div id="list"></div>
  <div id="detail"><p class="note">왼쪽에서 그룹을 고르세요.</p></div>
</main>
<div id="toast"></div>
<script>
const COL=__COL__;
const SCOPE_KO={unit:'1세대 평면도',floor:'층 평면도',unknown:'판정보류'};
const $=s=>document.querySelector(s);
let GROUPS=[],SEL=null,OFF=0,TOTAL=0;
function colorOf(r){return COL[r]||'#9aa3b2';}
function toast(m,ms){const t=$('#toast');t.textContent=m;t.style.display='block';
  clearTimeout(t._h);t._h=setTimeout(()=>t.style.display='none',ms||5000);}

async function loadList(reset){
  if(reset)OFF=0;
  const st=$('#fstatus').value,so=$('#fsort').value;
  const hs=$('#fhouse').value,sc=$('#fscope').value;
  const r=await(await fetch(`api/dedup/groups?status=${st}&sort=${so}&house=${hs}&scope=${sc}`
    +`&offset=${OFF}&n=80`)).json();
  if(r.error){$('#list').innerHTML='<p class="note warn" style="padding:10px">'+r.error+'</p>';return;}
  const s=r.summary;
  $('#sum').innerHTML=`이 조건: 고유도면 ${s.n_groups.toLocaleString()} · `
    +`불일치 <b>${s.n_conflict.toLocaleString()}</b> · 일치 ${s.n_agree.toLocaleString()} · `
    +`단일 ${s.n_single.toLocaleString()} · 미보정 ${s.n_none.toLocaleString()}`;
  TOTAL=r.total;
  if(reset)GROUPS=[];
  GROUPS=GROUPS.concat(r.items);
  renderList();
}
function renderList(){
  const h=GROUPS.map((g,i)=>`<div class="row${SEL===g.sig?' sel':''}" data-sig="${g.sig}">
    <div><b>${i+1}.</b> <span class="sig">${g.sig.slice(0,10)}</span>${g.stale?' ⟳':''}</div>
    <div class="num">사본 ${g.n} · 보정 ${g.corrected} · 변종 <b>${g.variants}</b> · 미보정 ${g.pending}
      ${g.entrance>1?` · <span class="warn" title="현관 ${g.entrance}개 = 세대분리 실패 의심">현관${g.entrance}</span>`:''}
      ${g.verts&&g.verts<20?` · <span class="warn" title="서명이 좌표 ${g.verts}개로만 만들어짐 — 다른 도면이 섞였을 수 있음">서명약함</span>`:''}</div>
  </div>`).join('');
  $('#list').innerHTML=h+(GROUPS.length<TOTAL
    ?`<div style="padding:10px"><button id="more">더 보기 (${GROUPS.length}/${TOTAL})</button></div>`:'');
  $('#list').querySelectorAll('.row').forEach(e=>e.onclick=()=>openGroup(e.dataset.sig));
  const m=$('#more');if(m)m.onclick=()=>{OFF+=80;loadList(false);};
}

// PAD_F 는 서버 dedup_review.PAD_F 와 같아야 배경 크롭과 폴리곤이 정확히 겹친다.
const PAD_F=0.05;
function drawThumb(svg,g){
  while(svg.firstChild)svg.removeChild(svg.firstChild);
  const b=g.bbox_px||[0,0,1000,1000],pad=Math.max(b[2],b[3])*PAD_F;
  const box=[b[0]-pad,b[1]-pad,b[2]+2*pad,b[3]+2*pad];
  svg.setAttribute('viewBox',box.join(' '));
  const NS='http://www.w3.org/2000/svg';
  const mk=(t,a)=>{const e=document.createElementNS(NS,t);
    for(const k in a)e.setAttribute(k,a[k]);svg.appendChild(e);return e;};
  const bgOn=$('#bg').checked;
  if(bgOn){   // 세대 영역만 잘라 축소한 원본(장당 ~50KB). 크롭 사각형 = viewBox 와 동일.
    const img=mk('image',{x:box[0],y:box[1],width:box[2],height:box[3],
      preserveAspectRatio:'none'});
    img.setAttribute('href','api/dedup/thumb/'+g.plan_id);
  }
  const rooms=Object.entries(g.rooms||{}).filter(([,r])=>r.polygon&&r.polygon.length>=3)
    .sort((a,c)=>(c[1].area_px||0)-(a[1].area_px||0));
  const fs=Math.max(b[2],b[3])/44;
  for(const [,r] of rooms){
    mk('polygon',{points:r.polygon.map(p=>p.join(',')).join(' '),
      fill:colorOf(r.role),'fill-opacity':bgOn?.34:.6,
      stroke:'#222','stroke-width':Math.max(b[2],b[3])/500});
  }
  for(const [,r] of rooms){
    if(!r.centroid)continue;
    const t=mk('text',{x:r.centroid[0],y:r.centroid[1],'font-size':fs,'text-anchor':'middle',
      fill:'#111',stroke:'#fff','stroke-width':fs/6,'paint-order':'stroke'});
    t.textContent=r.role||'';
  }
}

async function openGroup(sig){
  SEL=sig;renderList();
  const d=$('#detail');d.innerHTML='<p class="note">불러오는 중…</p>';
  const g=await(await fetch('api/dedup/group/'+sig)).json();
  if(g.error){d.innerHTML='<p class="note warn">'+g.error+'</p>';return;}
  d.innerHTML=`<div class="gh">
      <b style="font-family:ui-monospace,monospace">${sig}</b>
      <span class="badge">사본 ${g.n}</span>
      <span class="badge">변종 <b>${g.variants.length}</b></span>
      <span class="badge">미보정 ${g.pending}</span>
      <span class="badge">${g.house} · ${SCOPE_KO[g.scope]||g.scope}</span>
      ${g.scope==='unknown'?`<span class="badge warn" title="parsed 현관 ${g.parsed_ent}개 — 전실 오라벨일 수 있어 단정 못 함">평면 구분 미확정</span>`:''}
      ${g.verts&&g.verts<20?`<span class="badge warn">서명 약함(좌표 ${g.verts}개) — 다른 도면이 섞였는지 눈으로 확인</span>`:''}
      ${g.stale?'<span class="badge warn">전파됨 · 인덱스 재계산 필요</span>':''}
    </div>
    <p class="note">아래 카드가 <b>같은 도면인데 서로 다르게 보정된 결과</b>입니다.
      맞는 것을 고르고 버튼을 누르면 나머지 사본에 같은 보정이 복사됩니다.
      도면번호를 누르면 에디터가 새 탭에서 열립니다.</p>
    <div class="cards" id="cards"></div>`;
  const wrap=$('#cards');
  const show=g.variants.slice(0,24);
  for(const v of show){
    const c=document.createElement('div');
    c.className='card'+(v===g.variants[0]?' top':'');
    c.innerHTML=`<h3><span>${v===g.variants[0]?'다수안':'변종'} · ${v.count}건${v.held?` · 보류${v.held}`:''}</span>
        <span style="color:var(--dim);font-weight:400">방 ${v.n_rooms}</span></h3>
      <div class="pid" title="에디터에서 열기">${v.rep}</div>
      <svg class="thumb"></svg>
      <div class="roles">${v.roles}</div>
      ${(v.plus.length||v.minus.length)?`<div class="diff">다수안 대비
         <span class="p">${v.plus.join(' ')}</span> <span class="m">${v.minus.join(' ')}</span></div>`:''}
      <div class="tools">
        <button class="fill">미보정 ${g.pending}건 채우기</button>
        <button class="unify danger" title="미보정 ${g.pending}건 + 다르게 보정된 ${g.corrected-v.count}건">
          그룹 전체 통일 (${g.pending+g.corrected-v.count}건)</button>
      </div>`;
    wrap.appendChild(c);
    c.querySelector('.pid').onclick=()=>window.open('./?gid='+v.rep,'_blank');
    fetch('api/graph/'+v.rep).then(r=>r.json()).then(r=>{
      if(r.graph)drawThumb(c.querySelector('svg'),r.graph);});
    c.querySelector('.fill').onclick=()=>run(sig,v.rep,'fill',c);
    c.querySelector('.unify').onclick=()=>run(sig,v.rep,'unify',c);
  }
  if(g.variants.length>show.length)
    wrap.insertAdjacentHTML('afterend',
      `<p class="note">…외 ${g.variants.length-show.length}개 변종(1건짜리 소수안). 위에서 고르면 전부 통일됩니다.</p>`);
}

async function run(sig,src,mode,card){
  const msg=mode==='unify'
    ?`이 그룹의 모든 사본을 「${src}」 보정으로 통일합니다.\n다른 알바가 한 보정은 덮어씁니다(백업 후).\n진행할까요?`
    :`아직 보정 안 된 형제 도면에만 「${src}」 보정을 복사합니다.\n기존 보정은 건드리지 않습니다.\n진행할까요?`;
  if(!confirm(msg))return;
  card.querySelectorAll('button').forEach(b=>b.disabled=true);
  const r=await(await fetch('api/dedup/propagate',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({sig,source:src,mode})})).json();
  card.querySelectorAll('button').forEach(b=>b.disabled=false);
  if(r.error){toast('❌ '+r.error,8000);return;}
  toast(`✅ ${r.written}건 복사${r.overwritten?` (덮어쓴 ${r.overwritten}건 백업: ${r.backup})`:''}`
    +(r.n_skipped?`\n⏭ 스킵 ${r.n_skipped}건 — ${(r.skipped[0]||[]).join(': ')}`:'')
    +(r.errors&&r.errors.length?`\n⚠ 오류 ${r.errors.length}건`:''),9000);
  loadList(true);openGroup(sig);
}

$('#fstatus').onchange=()=>loadList(true);
$('#fhouse').onchange=()=>loadList(true);
$('#fscope').onchange=()=>loadList(true);
$('#fsort').onchange=()=>loadList(true);
$('#bg').onchange=()=>{if(SEL)openGroup(SEL);};
$('#q').onkeydown=async e=>{
  if(e.key!=='Enter')return;
  const pid=$('#q').value.trim();if(!pid)return;
  const r=await(await fetch('api/dedup/find/'+encodeURIComponent(pid))).json();
  if(r.sig){openGroup(r.sig);toast('그룹 '+r.sig);}else toast('그 도면번호의 그룹을 못 찾음');
};
loadList(true);
</script></body></html>"""
