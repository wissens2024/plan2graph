"""Plan2Graph 관리자 콘솔 (Streamlit) — 데이터셋 시각 검증·교정.

목적: 데이터셋 자동 생성보다 **사람이 눈으로 확인**하는 것이 핵심.
  ✅ 채택 큐: 데이터셋에 들어간 세대가 실제로 올바른지 도면+그래프+레코드로 검수.
  ⚠ 격리 큐: 문제 도면에 교정 알고리즘을 적용해, 데이터셋 레코드가 제대로 만들어지는지
            미리보고 승인/제외 결정.

실행:  streamlit run admin.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph import review  # noqa: E402
from plan2graph import scale_ocr  # noqa: E402
from plan2graph import legal_harvest, law_api  # noqa: E402

st.set_page_config(page_title="Plan2Graph 관리자", layout="wide")
# 상·하 여백을 줄여 사이드바 스크롤(페이징·옵션 잠식) 최소화
st.markdown("""<style>
.block-container{padding-top:1.6rem;padding-bottom:1rem;}
section[data-testid="stSidebar"] .block-container,
section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"]{padding-top:1rem;}
section[data-testid="stSidebar"] [data-testid="stVerticalBlock"]{gap:.35rem;}
/* 모든 타이틀 폰트 ~50% 축소 (st.title=h1·header=h2·subheader=h3) */
h1{font-size:1.4rem!important;}
h2{font-size:1.15rem!important;}
h3{font-size:1.0rem!important;}
</style>""", unsafe_allow_html=True)
SPLIT = "Training"
GRAPHS = config.PROCESSED_DIR / "graphs"
FLAGGED = config.PROCESSED_DIR / "flagged"

LEGEND = (
    "**범례** — 선(연결): 🔴문 · 🟢발코니(미닫이창) · 🟠개방통로(문·창 없는 트임) · 🟣인접병합 · ⚫현관↔외부 "
    "(점선=교정 미리보기)　|　방 채움색=세대(연결요소) 구분　|　그래프 노드색=위계 🔴public·🔵private·🟢service, 外=외부"
)
ORIGIN_NOTE = ("ℹ️ program·adjacency·위상은 **원본 라벨이 아니라 우리가 기하추론으로 생성**한 것"
               "(원본엔 방 폴리곤·이름만 있음).")


def _cc_set_scale(gid: str, scale):
    """CubiCasa 그래프 1개 meta.scale 갱신(scale>0 적용 / None이면 격리=px2) + area_m2 재계산."""
    from plan2graph import sources
    p = sources.graphs_dir("cubicasa5k") / f"{gid}.json"
    if not p.exists():
        return
    rec = json.loads(p.read_text(encoding="utf-8"))
    m = rec["meta"]
    if scale and scale > 0:
        m["scale"] = round(float(scale), 5); m["area_unit"] = "m2"; m["scale_confidence"] = "manual"
        tot = 0.0
        for nd in rec["layout"]["nodes"]:
            if nd.get("area_px2") is not None:
                nd["area_m2"] = round(nd["area_px2"] * scale * scale, 2); tot += nd["area_m2"]
        m["floor_area_m2"] = round(tot, 1)
    else:
        m["scale"] = None; m["area_unit"] = "px2"; m["scale_confidence"] = "quarantined"
        m["floor_area_m2"] = None
        for nd in rec["layout"]["nodes"]:
            nd.pop("area_m2", None)
    p.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")


def _pager(skey: str, npages: int, loc: str) -> int:
    """본문 페이지 내비(⏮ ◀ X/N ▶ ⏭). 같은 skey로 상·하단 동기화. 반환=현재 0-base 페이지.
    하단 '다음 ▶'을 누르면 rerun으로 화면이 위에서부터 다시 그려진다(전수 조사용)."""
    st.session_state.setdefault(skey, 0)
    cur = max(0, min(int(st.session_state[skey]), npages - 1))
    st.session_state[skey] = cur
    c = st.columns([1, 1, 2, 1, 1])
    if c[0].button("⏮", key=f"{skey}_{loc}_f", disabled=cur <= 0,
                   use_container_width=True, help="처음"):
        st.session_state[skey] = 0; st.rerun()
    if c[1].button("◀ 이전", key=f"{skey}_{loc}_p", disabled=cur <= 0, use_container_width=True):
        st.session_state[skey] = cur - 1; st.rerun()
    c[2].markdown(f"<div style='text-align:center;padding-top:.4rem'><b>{cur+1} / {npages}</b></div>",
                  unsafe_allow_html=True)
    if c[3].button("다음 ▶", key=f"{skey}_{loc}_n", disabled=cur >= npages - 1, use_container_width=True):
        st.session_state[skey] = cur + 1; st.rerun()
    if c[4].button("⏭", key=f"{skey}_{loc}_l", disabled=cur >= npages - 1,
                   use_container_width=True, help="끝"):
        st.session_state[skey] = npages - 1; st.rerun()
    return cur


def _inspect_3mode(recs, mode, res, ncol, render, caption_fn):
    """검수 3-모드 공용 표시. render(rec, overlay: bool)→PIL 이미지.
      나란히 = 원본 | 라벨오버레이 를 같은 도면에 좌우 동시(원본을 항상 먼저 봄)
      겹쳐보기 = 원본 위에 라벨을 겹친 1장
      원본만   = 순수 원본 1장 (해석 개입 없음)."""
    if mode == "나란히":
        for r in recs:
            try:
                o = render(r, False); o.thumbnail((res, res))
                v = render(r, True);  v.thumbnail((res, res))
            except Exception as e:  # noqa: BLE001
                st.warning(f"{caption_fn(r)} — 표시 실패: {e}")
                continue
            st.markdown(f"**{caption_fn(r)}**")
            c = st.columns(2)
            c[0].image(o, use_container_width=True, caption="① 원본 도면(raw PNG)")
            c[1].image(v, use_container_width=True, caption="② 라벨 오버레이(해석)")
            st.divider()
    else:
        cols = st.columns(ncol)
        for i, r in enumerate(recs):
            try:
                img = render(r, mode == "겹쳐보기")
                img.thumbnail((res, res))
                cols[i % ncol].image(img, use_container_width=True, caption=caption_fn(r))
            except Exception as e:  # noqa: BLE001
                cols[i % ncol].warning(f"{r.get('key', r.get('id', '?'))} 표시 실패: {e}")


def _graph_review(source_id, recs, render_original, graph_id_of):
    """원본 ∥ 그래프 단건 검수 + 정상/격리·결정버튼 (DATASET_DESIGN §7 공통 불변식).
    글로벌 출처(cubicasa/rplan)가 '변환됨' 레코드를 사람이 도면+그래프로 검증·판정.
      render_original(rec, overlay) → PIL · graph_id_of(rec) → 'CC_..'/'RPLAN_..'"""
    import json as _json
    from plan2graph import sources
    if not recs:
        st.info("변환된(converted) 레코드가 없습니다. 어댑터 변환 후 검수하세요.")
        return
    key = f"gri_{source_id}"
    st.session_state.setdefault(key, 0)
    st.session_state[key] = max(0, min(st.session_state[key], len(recs) - 1))
    i = st.session_state[key]
    n1, n2, n3 = st.columns([1, 2, 1])
    if n1.button("◀ 이전", key=f"{key}_p", use_container_width=True):
        st.session_state[key] -= 1; st.rerun()
    if n3.button("다음 ▶", key=f"{key}_n", use_container_width=True):
        st.session_state[key] += 1; st.rerun()
    n2.progress((i + 1) / len(recs), text=f"{i + 1} / {len(recs)}")

    r = recs[i]
    gid = graph_id_of(r)
    gpath = sources.graphs_dir(source_id) / f"{gid}.json"
    if not gpath.exists():
        st.warning(f"그래프 레코드 없음: {gpath}")
        return
    rec = _json.loads(gpath.read_text(encoding="utf-8"))
    v1, v2 = st.columns([1, 1])
    with v1:
        st.caption("① 원본 도면")
        try:
            img = render_original(r, True); img.thumbnail((1400, 1400))
            st.image(img, use_container_width=True)
        except Exception as e:  # noqa: BLE001
            st.warning(f"원본 표시 실패: {e}")
    with v2:
        st.caption("② 변환된 위상 그래프")
        st.pyplot(review.render_graph_fig(review.record_to_graph(rec), title=gid,
                  node_size=2200, font_size=12, layout="spatial"),
                  use_container_width=True)
    st.caption(LEGEND)
    m = rec["meta"]; cst = rec["constraints"]
    st.markdown(f"**방 {m.get('n_rooms')} · 문 {m.get('n_doors')} · 무결성 "
                f"{'✅통과' if rec['validation'].get('passed') else '❌위반'}** · "
                f"role=`{m.get('role')}` tier=`{m.get('tier')}` status=`{m.get('status')}`")
    st.caption(ORIGIN_NOTE)
    cc1, cc2 = st.columns(2)
    cc1.markdown("**program (방 구성)**"); cc1.json(cst["program"])
    cc2.markdown("**adjacency (인접 요구)**"); cc2.write(cst["adjacency"])

    note = st.text_input("결정 메모(선택)", key=f"{key}_note")
    led = sources.ledger_path(source_id)

    def _dec(action, status):
        review.record_decision_to(led, {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "graph_id": gid,
            "action": action, "result_status": status, "note": note})
        st.session_state[key] += 1; st.rerun()

    b1, b2, b3 = st.columns(3)
    if b1.button("✔ 승인(정상)", type="primary", key=f"{key}_ap", use_container_width=True):
        _dec("approve", "approved");
    if b2.button("⚠ 격리", key=f"{key}_q", use_container_width=True):
        _dec("quarantine", "quarantined")
    if b3.button("🗑 제외", key=f"{key}_x", use_container_width=True):
        _dec("exclude", "excluded")
    st.caption(f"원장: `{led}` · 결정 {len(review.load_ledger_from(led)):,}건")


def _status_cache_path(source_id):
    from plan2graph import sources
    return sources.graphs_dir(source_id).parent / "_status_cache.json"


def _status_summary(source_id):
    """출처 검수 현황 데이터(총/정상/격리/사유/by_id)만 집계 — 렌더링 없음.
    개별 페이지의 격리사유 필터와 '검수 현황(종합)' 페이지가 함께 쓴다. 화면 점유를
    없애기 위해 메트릭·차트는 그리지 않고, 종합 페이지에서만 시각화한다.

    RPLAN은 그래프 8만 개라 scan_status(전수 파싱)가 콜드 디스크에서 수십 초 걸린다.
    st.cache_data는 세션 캐시라 대시보드 재시작 때마다 날아가 매번 재스캔됐다 →
    집계 결과를 디스크에 작은 JSON으로 영속 캐시(파일 수가 키). 재시작 후에도 그 한
    파일만 읽어 즉시 뜬다. 파일 수 변동·재집계 버튼에서만 다시 스캔."""
    from plan2graph import dataset_status, sources
    gdir = sources.graphs_dir(source_id)
    nkey = len(list(gdir.glob("*.json"))) if gdir.is_dir() else 0  # 파일수 변동=자동 무효화

    @st.cache_data(show_spinner="검수 현황 집계(최초 1회)...")
    def _agg(sid, _n):
        cp = _status_cache_path(sid)
        if cp.exists():     # 디스크 영속 캐시 — 파일 수 일치 시 전수 파싱 생략
            try:
                c = json.loads(cp.read_text(encoding="utf-8"))
                if c.get("n") == _n:
                    return c["summary"]
            except Exception:  # noqa: BLE001
                pass
        summary = dataset_status.scan_status(sources.graphs_dir(sid))
        try:
            cp.write_text(json.dumps({"n": _n, "summary": summary}, ensure_ascii=False),
                          encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        return summary

    return _agg(source_id, nkey)


def _status_filter(recs, summary, graph_id_of):
    """사이드바 status/reason 필터 적용 → 필터된 recs. (graph_id_of(rec)→레코드 stem)"""
    from plan2graph import dataset_status
    by = summary["by_id"]
    f = st.sidebar.radio("상태 필터", ["전체", "✅ 정상", "⚠ 격리"], index=0, horizontal=True)
    if f == "전체":
        return recs
    want = "success" if f.startswith("✅") else "quarantine"
    out = [r for r in recs if by.get(graph_id_of(r), ("success", ""))[0] == want]
    if want == "quarantine" and summary["reasons"]:
        opts = list(summary["reasons"].keys())
        sel = st.sidebar.multiselect("격리 사유(택)", opts,
                                     format_func=dataset_status.reason_label, default=opts)
        ss = set(sel)
        out = [r for r in out
               if set((by.get(graph_id_of(r), ("", ""))[1] or "").split(",")) & ss]
    st.sidebar.caption(f"필터 결과: {len(out):,}개")
    return out


@st.cache_resource(show_spinner="zip 인덱스 구축 중... (최초 1회)")
def get_indices():
    return review.build_indices(("Training", "Validation"))  # 통합 풀


@st.cache_resource(max_entries=48, show_spinner="도면 로드 중...")
def get_sheet(sid: str):
    """도면을 메모리 캐시 — 재방문·슬라이더 조작 시 zip 재읽기 없음."""
    return review.load_sheet(sid, get_indices())  # split 자동 탐색


@st.cache_data(show_spinner=False)
def scale_ref_candidates(sid: str, cap: int = 12):
    """컴퓨터 스냅 기준선 — 시트 sid의 단위 그래프(벽/문)에서 픽셀 길이 후보를 추린다.

    좌표 검증결과: 그래프 wall/door px 좌표는 sheet.png_bytes(원본 시트)와 **동일한
    전체-시트 픽셀 공간**이다(walls bbox ⊂ 이미지 크기). 따라서 segment 픽셀길이를
    클릭거리(=factor 환산 후 원본px)와 그대로 비교해 같은 scale을 준다.

    반환: [(label, px_length, ((x1,y1),(x2,y2)) | None), ...] — 긴 벽 우선 + 문(width_px).
    endpoints는 원본-시트 px 좌표(그래프 px == 시트 png px). 문은 polygon/bbox에서
    width_px에 가장 가까운 길이의 선분을 대표 폭선으로 유도(불가 시 None=하이라이트 생략).
    근접 px는 dedup, cap개.
    """
    import math
    from plan2graph import topoedit

    def _door_seg(d, wpx):
        """문 polygon(또는 bbox)에서 width_px에 가장 가까운 길이의 대표 선분 유도."""
        poly = d.get("polygon")
        if poly and len(poly) >= 2:
            pts = [(float(p[0]), float(p[1])) for p in poly]
            # 닫힌 링이면 마지막 중복점 제거
            if len(pts) > 1 and pts[0] == pts[-1]:
                pts = pts[:-1]
            best, best_err = None, None
            n = len(pts)
            for i in range(n):
                (ax, ay), (bx, by) = pts[i], pts[(i + 1) % n]
                L = math.hypot(bx - ax, by - ay)
                if L < 1:
                    continue
                err = abs(L - wpx)
                if best_err is None or err < best_err:
                    best_err, best = err, ((ax, ay), (bx, by))
            # 폭과 충분히 가까운 변을 찾으면 사용
            if best is not None and best_err <= max(6.0, 0.25 * wpx):
                return best
        bb = d.get("bbox_px")
        if bb and len(bb) == 4:
            x, y, w, h = (float(v) for v in bb)
            # 문 폭선 = 짧은 변 방향으로 중앙을 가로지르는 선
            if w <= h:
                return ((x, y + h / 2.0), (x + w, y + h / 2.0))
            return ((x + w / 2.0, y), (x + w / 2.0, y + h))
        return None

    gdir = topoedit.GRAPHS_DIR
    walls, doors = [], []
    for gf in sorted(Path(gdir).glob(f"{sid}_u*.json")):
        uid = gf.stem.rsplit("_", 1)[-1]  # u0, u1, ...
        try:
            g = json.loads(gf.read_text(encoding="utf-8"))
        except Exception:
            continue
        for w in (g.get("walls") or []):
            seg = w.get("segment")
            if not seg or len(seg) != 2:
                continue
            (x1, y1), (x2, y2) = seg
            L = math.hypot(x2 - x1, y2 - y1)
            if L < 30:  # 잡선·노이즈 제외
                continue
            t = "외벽" if w.get("type") == "exterior" else "내벽"
            ep = ((float(x1), float(y1)), (float(x2), float(y2)))
            walls.append((f"{uid} {t} {w.get('id')} ({L:.0f}px)", round(L, 1), ep))
        for d in (g.get("doors") or []):
            wpx = d.get("width_px")
            if not wpx or wpx < 10:
                continue
            wpx = float(wpx)
            ep = _door_seg(d, wpx)
            doors.append((f"{uid} 문 {d.get('id')} (폭 {wpx:.0f}px)", round(wpx, 1), ep))
    walls.sort(key=lambda t: -t[1])  # 긴 벽 우선(명확한 기준선)
    out, seen = [], []
    for lab, px, ep in walls + doors:
        if any(abs(px - p) < 2.0 for p in seen):  # 근접 px dedup
            continue
        out.append((lab, px, ep)); seen.append(px)
        if len(out) >= cap:
            break
    return out


@st.cache_data(show_spinner=False)
def get_queue(which: str, _v: int = 0):
    return review.load_queue(which)


def _go(delta):
    st.session_state.i += delta


def _record(**kw):
    base = {"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "note": st.session_state.get("note", "")}
    base.update(kw)
    review.record_decision(base)


# ── 사이드바 ──────────────────────────────────────────────────────────────────
st.sidebar.markdown("#### 🏗 Plan2Graph 관리자")
_MENU = ["🧮 종합 현황",
         "🏢 AI-Hub 검수 (Parsed)", "🏠 CubiCasa 검수", "📐 RPLAN 검수",
         "🧩 AI-Hub 검수 (Corrected)", "🗂 데이터셋 도면", "📗 도면 생성", "✏️ 도면정보보정",
         "⚖️ 성능 비교",
         "📜 법령 DB"]
try:  # 동그라미 없는 클릭형 메뉴(streamlit-option-menu). 미설치 시 라디오로 폴백.
    from streamlit_option_menu import option_menu
    with st.sidebar:
        which = option_menu(
            None, _MENU, key="mainmenu",
            manual_select=st.session_state.pop("_goto_idx", None),  # 다른 메뉴서 전환용
            icons=["" for _ in _MENU],   # 라벨에 이미 이모지 → bootstrap 기본아이콘 숨김
            default_index=0,
            styles={
                "container": {"padding": "0", "background-color": "transparent"},
                "nav-link": {"font-size": "0.85rem", "padding": "4px 10px",
                             "margin": "1px 0", "--hover-color": "#eef2ff"},
                "nav-link-selected": {"background-color": "#4f46e5"},
            },
        )
except Exception:
    which = "🧮 종합 현황"

if not which:
    which = "🧮 종합 현황"

# ════════════════════════════════════════════════════════════════════════════
# 🧩 AI-Hub 검수 (G) — 원본 위에서 사람이 위상 직접 구축(자동추론 0) → staging/gline
# ════════════════════════════════════════════════════════════════════════════
if which.startswith("🧩"):
    from plan2graph import topoedit
    st.title("🧩 AI-Hub 검수 (G) — 위상+기하 보정")
    # 상단 데이터셋 합계(사용/보정필요/제외/보정완료)는 종합현황·버전표에 있어 중복 → 제거.
    # 대신 '사람 보정 건수'(SVG 보정완료 세대)를 편집기 상단 바에 표시(render_editor).
    topoedit.render_editor(show_title=False)
    st.stop()

# ════════════════════════════════════════════════════════════════════════════
# 🧮 검수 현황(종합) — AI-Hub·CubiCasa·RPLAN 변환 결과를 한 화면에서 비교
# ════════════════════════════════════════════════════════════════════════════
if which.startswith("🧮"):
    from plan2graph import dataset_status

    st.title("🧮 검수 현황(종합)")

    st.caption("**도면(받은 원본) 단위** · AI-Hub · CubiCasa5k · RPLAN 처분 비교. "
               "각 출처 합 = 다운로드 원본 수. 개별 검수는 각 도면검수 메뉴에서.")
    SRC = [("aihub", "🏢 AI-Hub"), ("cubicasa5k", "🏠 CubiCasa5k"), ("rplan", "📐 RPLAN")]
    if st.button("🔄 재집계(캐시 비움)", help="재변환·dedup 후 현황을 다시 집계(디스크 캐시도 삭제)"):
        for sid, _ in SRC:     # 디스크 영속 캐시까지 지워야 내용변경(재변환)이 반영됨
            try:
                _status_cache_path(sid).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        st.cache_data.clear(); st.rerun()

    def _disp_of_source(sid):
        """출처별 처분 집계 {use,fix,excl,total}. AI-Hub는 원본 manifest, 나머지는 그래프 상태."""
        if sid == "aihub":
            mp = config.DATA_DIR / "staging" / "aihub" / "manifest.jsonl"
            if not mp.exists():
                return {"use": 0, "fix": 0, "excl": 0, "total": 0}

            @st.cache_data(show_spinner="AI-Hub 회계 집계...")
            def _agg_aihub(_n):
                from collections import Counter
                c = Counter(json.loads(ln)["disposition"]
                            for ln in mp.read_text(encoding="utf-8").splitlines() if ln.strip())
                return {"use": c.get("use", 0), "fix": c.get("fix", 0),
                        "excl": c.get("excl", 0), "total": sum(c.values())}
            return _agg_aihub(mp.stat().st_size)
        return dataset_status.disposition_groups(_status_summary(sid))

    rows = [(name, _disp_of_source(sid)) for sid, name in SRC]
    tot = {k: sum(d[k] for _, d in rows) for k in ("use", "fix", "excl", "total")}

    g = st.columns(4)
    g[0].metric("받은 원본 (3출처 합)", f"{tot['total']:,}")
    g[1].metric("✅ 사용", f"{tot['use']:,}")
    g[2].metric("🛠 보정·복구 필요", f"{tot['fix']:,}")
    g[3].metric("🚫 제외", f"{tot['excl']:,}")
    st.caption("※ AI-Hub는 원본 도면 기준(그래프는 다세대 분할로 더 많음). 합 = 받은 원본.")
    st.divider()

    for name, d in rows:
        st.markdown(f"#### {name} — 받은 원본 **{d['total']:,}**")
        c = st.columns(3)
        rate = (d["use"] / d["total"] * 100) if d["total"] else 0
        c[0].metric("✅ 사용", f"{d['use']:,}", f"{rate:.1f}%")
        c[1].metric("🛠 보정·복구 필요", f"{d['fix']:,}")
        c[2].metric("🚫 제외", f"{d['excl']:,}")
        st.caption(f"사용 {d['use']:,} + 보정·복구 {d['fix']:,} + 제외 {d['excl']:,} = {d['total']:,} (=다운로드)")
        st.divider()

    # ── 파이프라인 진행 상황 — 현재 구조(ADR-0006/0007): 박스회귀 폐기 → 자체 소버린 엔진 ──
    st.markdown("### 🧭 파이프라인 진행 상황 — 데이터 → 통일그래프 → 한국형 엔진 → 도면+DXF")
    st.caption("현재 구조(ADR-0006/0007): 박스회귀 폐기 → 자체 소버린 엔진(DiffPlanner 골격 "
               "한국형 확장). ✅완료 · 🔄진행 · ⬜예정. 엔진은 서버 ~/diffplanner_work.")
    _DIFFP = Path("~/diffplanner_work").expanduser()
    _ckpt_root = _DIFFP / "ckpt_kr"

    def _eng_ckpt(arm):  # 해당 ARM 학습 체크포인트 존재?
        if not _ckpt_root.exists():
            return False
        return any(any((_ckpt_root / s / arm).glob("model*.pt"))
                   for s in ("node_diff", "adjacency_diff", "partitioning_diff"))

    _kor_data = _DIFFP / "dataset" / "dataset_json_korean" / "data_train.json"
    _ft = _eng_ckpt("finetune") or _eng_ckpt("korean_only")
    _stages = [
        ("1. 품질 게이트(온전/보정필요)", "plan_quality · 온전 64.7%", "✅",
         "src/plan2graph/plan_quality.py"),
        ("2. 통일 그래프(2층 스키마)", "geomgraph", "✅", "src/plan2graph/geomgraph.py"),
        ("3. 엔진 데이터 변환(온전만 · 13역할/18방)",
         ("준비됨" if _kor_data.exists() else "—"), "✅" if _kor_data.exists() else "⬜",
         "scripts/korean_to_engine.py"),
        ("4. 엔진 아키텍처(DiffPlanner 한국형 13/18)", "node·adjacency·partitioning", "✅",
         "~/diffplanner_work (gate2 patch)"),
        ("5. 엔진 학습(ARM-A 사전학습→파인튜닝 / ARM-B)",
         ("체크포인트 있음" if _ft else "학습 중/대기"), "✅" if _ft else "🔄",
         "gate2_train_runbook.sh → ckpt_kr"),
        ("6. 샘플링 → 도면+DXF(neuro-symbolic 완성)", "korean_sample → cadrender", "✅",
         "scripts/diffplanner_to_cadrender.py"),
        ("7. 비교(T∥G·ARM · FID·법규·완성도)", "⚖️ 성능 비교 화면", "⬜", "ADR-0007 ④"),
    ]
    st.table([{"단계": s, "상태": stt, "산출물": a, "위치": loc}
              for s, a, stt, loc in _stages])
    st.divider()

    # ── 데이터셋 버전(생성 학습용) — releases/<버전>/manifest.json 에서 직접 읽음(단일 출처) ──
    st.markdown("### 📦 데이터셋 버전 (생성 학습용)")
    st.caption("releases/<버전>/manifest.json 에서 직접 읽음 — GUI·문서·코드가 같은 출처라 숫자 일치. "
               "T-라인=위상그래프(v0~) · G-라인=기하 2층 스키마(g0~).")
    import glob as _glob
    _vrows = []
    for _v, _line, _rp in config.list_releases():
        try:
            _m = json.loads((_rp / "manifest.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        _vrows.append({"버전": _m.get("version", _v),
                       "라인": "T-라인" if _line == "tline" else "G-라인",
                       "스키마": _m.get("schema", "?"),
                       "출처": "자동" if _m.get("auto") else "보정포함",
                       "주택형": ",".join(_m.get("houses", [])),
                       "도면": _m.get("n_plans"), "세대": _m.get("n_units")})
    if _vrows:
        import pandas as _pd
        st.dataframe(_pd.DataFrame(_vrows), hide_index=True, use_container_width=True)
    else:
        st.info("아직 빌드된 버전이 없습니다. (scripts/build_geom.py 로 생성)")
    st.stop()

# ════════════════════════════════════════════════════════════════════════════
# 🏢 배제 도면 검수 — 그래프 대상에서 빠진 평면도를 실제 PNG로 육안 검증
# ════════════════════════════════════════════════════════════════════════════
if which.startswith("🏢"):
    import io as _io
    from PIL import Image as _PImage
    from plan2graph import inspect_excluded as _ix

    st.title("🏢 AI-Hub 검수 (T) — 자동변환 그래프")
    # 상단 데이터셋 합계(사용/보정필요/제외)는 종합현황에 있어 중복 → 제거. 검수 본연(원본 육안)만.
    # (_aim_t 정의는 아래 '변환 보정' 기능에서 쓰이므로 유지 — 메트릭 렌더만 제거)
    _aim_t = config.DATA_DIR / "staging" / "aihub" / "manifest.jsonl"
    st.caption("AI-Hub 도면(받은 원본 PNG)을 육안 검증 — 사용(dual+V2V 복구)·보정필요·제외(중복 사본·비-FP) 사유 확인. "
               "**도면 1장 = 여러 세대**라 도면수 ≠ 세대수 — 세대 = **실제 추출된 그래프**만: "
               "사용=변환세대 전개 · 보정필요=미변환(0) · 제외=0(중복은 원본과 같은 세대라 중복집계 안 함·비FP는 세대 없음).")
    if not config.RAW_SOURCE_ROOT.is_dir():
        st.error(f"원본 RAW 없음: {config.RAW_SOURCE_ROOT}\n"
                 "PLAN2GRAPH_RAW 환경변수를 SPA/STR zip이 있는 경로로 설정 후 재실행.")
        st.stop()

    # AI-Hub의 Training/Validation은 벤더가 다운로드를 나눠준 '포장 폴더'일 뿐 —
    # 도면이 비-FP·중복·OBJ만인지는 이미지 속성이라 포장과 무관하고, 우리 dataset의
    # train/val/test(release._bucket, 시드 해싱)와도 별개다. 검수엔 분리 가치가 없어
    # 둘을 합친 한 풀로 본다(합치면 두 포장에 걸친 중복까지 한 번에 잡힘).
    split = ("Training", "Validation")

    @st.cache_data(show_spinner="원천 PNG 지문 스캔(최초 1회)...")
    def _excl_cats(sp):
        return _ix.categorize(_ix.build_index(sp))

    @st.cache_data(show_spinner="비-FP 도면 스캔...")
    def _nonfp(sp):
        return _ix.nonfp_records(sp)

    @st.cache_data(show_spinner="중복 도면 스캔...")
    def _dup(sp):
        return _ix.duplicate_records(sp)

    @st.cache_data(show_spinner="OBJ/OCR-only 스캔...")
    def _objocr(sp):
        return _ix.objocr_only_records(sp)

    cats = _excl_cats(split)
    nonfp = _nonfp(split)
    dup = _dup(split)
    objocr = _objocr(split)

    # 렌더 인덱스: 지문(sig) → 표시용 레코드(zip/entry). 모든 분류 합침(중복은 대표 1개).
    ridx = {}
    for grp in (cats["dual"], cats["spa_only"], cats["str_only"], nonfp, objocr, dup):
        for r in grp:
            ridx.setdefault(r["sig"], r)

    # manifest(권위 회계) — 처분·사유·house. 콤보 카운트는 여기서(합=다운로드).
    _mpath = config.DATA_DIR / "staging" / "aihub" / "manifest.jsonl"
    if not _mpath.exists():
        st.error("AI-Hub manifest 없음. 서버에서 `python src/plan2graph/build_aihub.py` 먼저 실행.")
        st.stop()

    @st.cache_data(show_spinner="AI-Hub 회계(manifest) 로드...")
    def _aihub_manifest(_n):
        return [json.loads(ln) for ln in _mpath.read_text(encoding="utf-8").splitlines() if ln.strip()]

    rows = _aihub_manifest(_mpath.stat().st_size)
    AIHUB_LABEL = {
        ("use", "dual"): "✅ 사용 · dual(직접변환)",
        ("use", "dual_dedup_merge"): "✅ 사용 · dual(직접변환)",   # 중복라벨복구 → dual로 통합(별 카테고리 제거)
        ("use", "v2v_str_recovered"): "✅ 사용 · 방만→V2V STR복구",
        ("use", "v2v_spa_recovered"): "✅ 사용 · 구조만→V2V SPA복구",
        ("fix", "convert_failed"): "🛠 보정필요 · 변환실패(dual)",
        ("fix", "spa_only_pending"): "🛠 보정필요 · 방만(V2V 대기)",
        ("fix", "str_only_pending"): "🛠 보정필요 · 구조만(V2V 대기)",
        ("fix", "objocr"): "🛠 보정필요 · OBJ/OCR만(공간라벨 없음)",
        ("excl", "nonfp"): "🚫 제외 · 비-FP(평면도 아님)",
        ("excl", "duplicate"): "🚫 제외 · 중복(사본)",
    }
    # 처분 버킷 접두어(✅사용/🛠보정필요/🚫제외)는 G검수(🧩)와 동일. 중복키 라벨은 1개로 dedup.
    _AIHUB_ORDER = list(dict.fromkeys(AIHUB_LABEL.values()))

    def _albl(row):
        return AIHUB_LABEL.get((row["disposition"], row["reason"]),
                               f"{row['disposition']}·{row['reason']}")

    from collections import Counter as _C
    _cnt = _C(_albl(r) for r in rows)                    # 라벨별 도면수(행=raw PNG 1장)
    disp = [(lab, _cnt[lab]) for lab in _AIHUB_ORDER if _cnt.get(lab, 0)]
    keymap = {"📋 전체": len(rows)}                       # 분류 → 도면수
    keymap.update(dict(disp))
    # 분류 → 세대수 — 정본 규칙(dataset_status) 재사용(변환 그래프만 셈·중복은 0). 도면+세대 병기.
    from plan2graph import dataset_status
    _ukey = _C()
    for _r in rows:
        _ukey[_albl(_r)] += dataset_status.aihub_row_units(_r)
    keymap_unit = {"📋 전체": sum(_ukey.values())}        # 분류 → 세대수
    keymap_unit.update(dict(_ukey))
    # ── 상단 통일 컨트롤(사이드바→본문): 분류 | 거주형태 + 보기 모드. G검수(🧩)와 동일 구조. ──
    c_cat, c_house = st.columns(2)
    cat = c_cat.selectbox("분류", ["📋 전체"] + [lab for lab, _ in disp],
                          format_func=lambda k: f"{k} (도면 {keymap[k]:,} · 세대 {keymap_unit.get(k,0):,})")
    _HOUSE_KO = {"APT": "APT(아파트)", "DEH": "DEH(단독주택)", "ROW": "ROW(연립주택)"}
    house = c_house.selectbox("거주형태", ["(전체)", "APT", "DEH", "ROW"],
                              format_func=lambda k: _HOUSE_KO.get(k, k))
    view = st.radio(
        "보기 모드", ["그래프검수(원본∥그래프)", "나란히(원본 | 오버레이)", "겹쳐보기", "원본만"],
        index=0, horizontal=True,
        help="그래프검수=구버전 위상 검수 · 나란히/겹쳐/원본만=라벨 육안확인 · "
             "사람 위상 구축은 좌측 메뉴 🧩 AI-Hub 검수 (G)")
    # 보정(재변환) 도구 — 분류 콤보가 '보정필요(fix)'일 때만 노출(사용/제외/전체에선 숨김).
    _disp_by_label = {lab: dispo for (dispo, _r), lab in AIHUB_LABEL.items()}
    if _aim_t.exists() and _disp_by_label.get(cat) == "fix":
        with st.expander("🔧 변환 보정 (재변환) — 보정필요(fix) → 사용(use)", expanded=True):
            @st.cache_data(show_spinner=False)
            def _fix_reasons_t(_sz):
                from collections import Counter as _Ctr
                _c = _Ctr()
                for _l in _aim_t.read_text(encoding="utf-8").splitlines():
                    if not _l.strip():
                        continue
                    _r = json.loads(_l)
                    if _r.get("disposition") == "fix":
                        _c[_r.get("reason", "?")] += 1
                return dict(_c.most_common())
            _fr = _fix_reasons_t(_aim_t.stat().st_size)
            st.write("**보정 필요 사유별:** "
                     + (" · ".join(f"`{k}` {v:,}" for k, v in _fr.items()) or "—"))
            st.caption("convert_failed → **임계·규칙 재변환**(아래) · spa_only/str_only_pending → **V2V 검출**(별도·GPU).")
            st.markdown("**변환 파라미터** (이 값들이 변환 품질을 좌우 — 자유 텍스트 아님)")
            _k1, _k2, _k3 = st.columns(3)
            _gap = _k1.number_input("개방통로 최대간격(px)", 0.0, 300.0, float(config.OPEN_MAX_GAP_PX))
            _ratio = _k2.number_input("개방통로 비율 임계", 0.0, 1.0, float(config.OPEN_MIN_RATIO))
            _etc = _k3.number_input("최소 기타면적(px²)", 0.0, 100000.0, float(config.MIN_ETC_AREA_PX))
            _cvlog = config.PROJECT_ROOT / "logs" / "reconvert.log"
            if _cvlog.exists():
                _cl = _cvlog.read_text(errors="ignore").strip().splitlines()
                st.info(f"최근 재변환 로그: `{(_cl[-1] if _cl else '…')[:120]}`")
                if st.button("🔄 상태 새로고침", key="cv_ref"):
                    st.rerun()
            if st.button("🔧 재변환 실행 (백그라운드)", key="cv_go"):
                import subprocess as _sp
                _env = (f"P2G_OPEN_MAX_GAP_PX={_gap} P2G_OPEN_MIN_RATIO={_ratio} "
                        f"P2G_MIN_ETC_AREA_PX={_etc} PYEXE='{sys.executable}'")
                _cmd = (f"cd '{config.PROJECT_ROOT}' && {_env} setsid nohup "
                        f"bash scripts/reconvert_aihub.sh > logs/reconvert.log 2>&1 &")
                _sp.Popen(["bash", "-lc", _cmd])
                st.success("재변환 시작(백그라운드) — 임계 적용 재변환 → staging 통합 → manifest 재생성까지 자동. "
                           "무겁습니다(수십 분~). 끝나면 **다음 화면 로드에서 use/fix 자동 갱신**.")
            st.caption("재변환→통합→manifest까지 닫힘(별도 dir 빌드→성공 시만 교체·백업). "
                       "spa_only/str_only_pending은 V2V 검출이 별도 필요.")
    sel = [r for r in rows
           if (cat == "📋 전체" or _albl(r) == cat) and (house == "(전체)" or r.get("house") == house)]
    # 렌더 대상: manifest 행의 지문 → 렌더 인덱스(없으면 스킵). 카운트는 manifest(sel)가 권위.
    recs = [ridx[r["fingerprint"]] for r in sel if r["fingerprint"] in ridx]

    st.markdown(f"### {cat} — **{len(sel):,}개**" +
                (f"  ·  _렌더가능 {len(recs):,}_" if len(recs) != len(sel) else ""))
    @st.cache_data(show_spinner="라벨 인덱스 구성(최초 1회)...")
    def _lblidx(sp):
        return _ix.label_index(sp)

    if view.startswith("그래프검수"):    # ⚠격리/✅채택 흡수 — staging/aihub 그래프 검수·결정(ledger 기록)
        lblidx = _lblidx(split)
        items = []
        for r in sel:
            if r.get("became_graph") and r["fingerprint"] in ridx:
                rr = ridx[r["fingerprint"]]
                for gid in r.get("graph_ids", []):
                    items.append({"gid": gid, "rr": rr})
        if not items:
            st.info("이 분류엔 변환된 그래프가 없습니다(제외/복구대상). '✅ 사용' 분류에서 그래프검수하세요.")
        else:
            _graph_review("aihub", items,
                          lambda it, ov: _ix.render(it["rr"], lblidx, overlay=ov),
                          lambda it: it["gid"])
        st.stop()
    mode = ("나란히" if view.startswith("나란히")
            else "겹쳐보기" if view == "겹쳐보기" else "원본만")
    need_overlay = mode != "원본만"
    if need_overlay:
        st.markdown("**라벨 색**: :green[●] 방(SPA) · :red[●] 문 · :orange[●] 창 · :blue[●] 벽(STR) "
                    "— *그려진 게 라벨된 것. 안 그려진 종류 = 라벨 없음(배제 사유).*")

    lblidx = _lblidx(split) if need_overlay else {}
    res = st.sidebar.select_slider("표시 해상도(px)", options=[1200, 1600, 2000, 2600, 3200, 4200],
                                   value=2000, help="원본 PNG는 ~17MB 고해상. 클릭→전체화면 시 이 해상도로 보임.")
    ncol = st.sidebar.radio("열 수(겹쳐/원본만)", [1, 2], index=1, horizontal=True)
    PER = 4 if mode == "나란히" else ncol * 2
    st.sidebar.caption("이미지 클릭 → 우상단 ⛶ 전체화면이면 더 크게 보입니다.")
    npages = max(1, (len(recs) + PER - 1) // PER)
    st.sidebar.caption(f"한 페이지 {PER}장 · 총 {npages:,}페이지 · 해상도 {res}px")
    pg = _pager("pg_aihub", npages, "top")

    def _cap(r):
        if r.get("group"):   # 중복: 사본마다 i/N + 원본/제외
            return (f"🔁중복그룹#{r['group']} · {r['label']} {r['i']}/{r['n']} · "
                    f"key={r['key']} · {'✅원본(채택)' if r['kept'] else '❌중복(제외)'}")
        return f"{r['house']} · {r['key']} · 라벨={r['labels']}"

    _inspect_3mode(recs[pg * PER:(pg + 1) * PER], mode, res, ncol,
                   lambda r, ov: _ix.render(r, lblidx, overlay=ov), _cap)
    _pager("pg_aihub", npages, "bot")
    st.stop()

# ════════════════════════════════════════════════════════════════════════════
# 🏠 CubiCasa5k 도면검수 — 글로벌 데이터 정상분(변환)·제외분(사유) 육안 검증
# ════════════════════════════════════════════════════════════════════════════
if which.startswith("🏠"):
    from plan2graph import cubicasa_inspect as _cci

    st.title("🏠 CubiCasa5k 도면검수")
    st.caption("CubiCasa5k 케이스 전량을 원본+오버레이로 — 처분(사용/보정필요/제외)별, 합=다운로드.")
    if not _cci.CC_ROOT.is_dir():
        st.error(f"CubiCasa5k 데이터 없음: {_cci.CC_ROOT}")
        st.stop()

    @st.cache_data(show_spinner="CubiCasa 샘플 스캔(최초 1회)...")
    def _ccscan():
        return _cci.scan()

    s = _ccscan()
    from plan2graph import dataset_status as _ds
    # 콤보·헤더가 종합 패널과 같은 _status_summary를 읽어 숫자가 어긋나지 않게 함
    _ccsumm = _status_summary("cubicasa5k")
    by = _ccsumm["by_id"]
    universe = s.get("converted", []) + s.get("excluded", [])   # 케이스 전량(=다운로드)

    def _gid(r):
        return f"CC_{r['id']}"

    def _disp(r):   # 도면 1장 → 대표 처분 1칸(상호배타)
        g = _gid(r)
        return _ds.disposition_label(*by[g]) if g in by else "❔ 미변환(그래프 없음)"

    from collections import Counter as _C
    _cnt = _C(_disp(r) for r in universe)
    _order = _ds._disposition_order() + ["❔ 미변환(그래프 없음)"]
    disp = [(lab, _cnt[lab]) for lab in _order if _cnt.get(lab, 0)]
    with st.expander("ℹ️ 분류 (꼭 읽기)", expanded=False):
        st.markdown(
            f"- 검수 대상 = CubiCasa 케이스 전량 **{len(universe):,}개** "
            f"(= 다운로드 = 종합 패널 총수).\n"
            f"- 도면 1장 = **처분 1칸**(대표 사유, 상호배타). 모든 칸 합 = {len(universe):,}.\n"
            "- 오버레이: :green[●] 방 · :red[●] 문. 그려진 게 추출된 것.")
    # 분류 콤보(처분 버킷, 상호배타) + 세트(facet)
    keymap = {"📋 전체": len(universe)}
    keymap.update(dict(disp))
    cat = st.sidebar.selectbox("분류", ["📋 전체"] + [lab for lab, _ in disp],
                               format_func=lambda k: f"{k} ({keymap[k]:,})")
    subs = sorted({r["sub"] for r in universe})
    subf = st.sidebar.selectbox("세트", ["(전체)"] + subs)
    recs = [r for r in universe
            if (cat == "📋 전체" or _disp(r) == cat) and (subf == "(전체)" or r["sub"] == subf)]
    view = st.sidebar.radio(
        "보기 모드", ["그래프검수(원본∥그래프)", "나란히(원본 | 오버레이)", "겹쳐보기", "원본만"],
        index=0, help="그래프검수=원본∥위상그래프+결정 · 나란히=원본vs오버레이 · 겹쳐=원본 위 방·문 · 원본만")
    if view.startswith("그래프검수"):
        _graph_review("cubicasa5k", recs, lambda r, ov: _cci.render(r, overlay=ov), _gid)
        st.stop()
    mode = ("나란히" if view.startswith("나란히")
            else "겹쳐보기" if view == "겹쳐보기" else "원본만")
    res = st.sidebar.select_slider("표시 해상도(px)", options=[800, 1100, 1500, 2000, 2600],
                                   value=1500, help="CubiCasa 원본은 ~1100px(AI-Hub보다 저해상).")
    ncol = st.sidebar.radio("열 수(겹쳐/원본만)", [1, 2], index=1, horizontal=True)
    PER = 4 if mode == "나란히" else ncol * 2
    st.markdown(f"### {cat} — **{len(recs):,}개**")
    npages = max(1, (len(recs) + PER - 1) // PER)
    st.sidebar.caption(f"한 페이지 {PER}장 · 총 {npages:,}페이지 · {res}px")
    pg = _pager("pg_cubicasa", npages, "top")

    def _cap(r):
        g = _gid(r)
        stt, rsn = by.get(g, ("", ""))
        full = f" (전체위반:{rsn})" if g in by and stt != "success" and rsn else ""
        return f"{r['sub']}/{r['id']} · {_disp(r)}{full}"

    _inspect_3mode(recs[pg * PER:(pg + 1) * PER], mode, res, ncol,
                   lambda r, ov: _cci.render(r, overlay=ov), _cap)
    _pager("pg_cubicasa", npages, "bot")
    st.stop()

# ════════════════════════════════════════════════════════════════════════════
# 📐 RPLAN 도면검수 — 글로벌 데이터 정상분(변환)·제외분(사유) 육안 검증
# ════════════════════════════════════════════════════════════════════════════
if which.startswith("📐"):
    from plan2graph import rplan_inspect as _rpi

    from plan2graph import dataset_status as _ds
    st.title("📐 RPLAN 도면검수")
    st.caption("RPLAN 변환 그래프 전량을 원본(snapshot RGB 또는 .mat 벡터)으로 검수 — "
               "콤보 숫자 = 종합 패널 '총 그래프' = 다운로드(.mat) 엔트리 수로 항상 일치.")

    @st.cache_data(show_spinner="RPLAN 레코드 스캔(최초 1회)...")
    def _rpscan(_n):
        return _rpi.scan()

    # 콤보·헤더·패널이 '같은 한 소스'(_status_summary)를 읽어 숫자가 절대 어긋나지 않게 함
    _rpsumm = _status_summary("rplan")
    s = _rpscan(_rpsumm["total"])     # 그래프 수 변동 시 스캔 캐시도 갱신
    if not s["all"]:
        st.warning("변환된 RPLAN 그래프가 없습니다(staging/rplan/graphs 비어 있음). "
                   "어댑터를 먼저 실행: "
                   "`python src/plan2graph/adapters/rplan_vector.py --src data/raw/rplan/Network/data.mat`")
        st.stop()
    by = _rpsumm["by_id"]
    nrec, npng = _rpsumm["total"], len(s["with_png"])
    disp = _ds.disposition_combo(_rpsumm)   # [(대표라벨, 건수)] 상호배타, 합=nrec
    with st.expander("ℹ️ 분류 (꼭 읽기)", expanded=False):
        st.markdown(
            f"- **검수 대상 = 변환된 그래프 전량 {nrec:,}개** "
            f"(= 종합 패널 '총 그래프' = 다운로드 RPLAN .mat 엔트리 수).\n"
            f"- 도면 1장 = **처분 1칸**(대표 사유). 모든 칸 합 = {nrec:,} = 다운로드.\n"
            f"- 원본 보기: snapshot RGB 렌더 **{npng:,}개**는 이미지로, 나머지 "
            f"**{nrec - npng:,}개**는 .mat 벡터(gtBoxNew) 박스로 표시.")
    st.markdown("**방 색**: " + " · ".join(
        f":gray[■]{_rpi.CAT_KO[k]}" for k in sorted(_rpi.CAT_KO)))
    # 분류 콤보 — 처분 버킷(상호배타). '전체' + 각 대표사유. 합=다운로드.
    keymap = {"📋 전체": nrec}
    keymap.update(dict(disp))
    cat = st.sidebar.selectbox("분류", ["📋 전체"] + [lab for lab, _ in disp],
                               format_func=lambda k: f"{k} ({keymap[k]:,})")
    if cat == "📋 전체":
        recs = s["all"]
    else:
        recs = [r for r in s["all"]
                if _ds.disposition_label(*by.get(r["graph_id"], ("success", ""))) == cat]
    view = st.sidebar.radio(
        "보기 모드", ["그래프검수(원본∥그래프)", "나란히(원본 | 오버레이)", "겹쳐보기", "원본만"],
        index=0, help="그래프검수=원본∥위상그래프+결정 · 나란히=원본vs경계·문 오버레이")
    if view.startswith("그래프검수"):
        _graph_review("rplan", recs, lambda r, ov: _rpi.render(r, overlay=ov),
                      lambda r: r["graph_id"])
        st.stop()
    mode = ("나란히" if view.startswith("나란히")
            else "겹쳐보기" if view == "겹쳐보기" else "원본만")
    res = st.sidebar.select_slider("표시 해상도(px)", options=[512, 768, 1024, 1536, 2048],
                                   value=1024, help="RPLAN 원본은 256px(인덱스 맵)을 ×4 확대해 표시.")
    ncol = st.sidebar.radio("열 수(겹쳐/원본만)", [1, 2, 3], index=1, horizontal=True)
    PER = 4 if mode == "나란히" else ncol * 2
    st.markdown(f"### {cat} — **{len(recs):,}개**")
    npages = max(1, (len(recs) + PER - 1) // PER)
    st.sidebar.caption(f"한 페이지 {PER}장 · 총 {npages:,}페이지 · {res}px")
    pg = _pager("pg_rplan", npages, "top")

    def _cap(r):
        stt, rsn = by.get(r["graph_id"], ("success", ""))
        tag = _ds.disposition_label(stt, rsn)         # 대표 처분
        full = f" (전체위반:{rsn})" if stt != "success" and rsn else ""
        src = "snapshot RGB" if r.get("png") else ".mat 벡터박스"
        return f"{r['graph_id']} · {tag}{full} · [{src}]"

    _inspect_3mode(recs[pg * PER:(pg + 1) * PER], mode, res, ncol,
                   lambda r, ov: _rpi.render(r, overlay=ov), _cap)
    _pager("pg_rplan", npages, "bot")
    st.stop()
if which.startswith("🗂"):
    import glob as _glob
    import json as _json
    import os as _os
    import re as _re
    import zipfile as _zip

    from plan2graph import cadrender as _cr

    st.title("🗂 데이터셋 도면 — 그래프 → 도면 이미지 · AutoCAD(DXF)")
    st.caption("데이터셋(=그래프)을 골라 필터하고, 하나를 선택하면 **왼쪽=생성 도면+DXF, 오른쪽=원본 도면**. "
               "검수처럼 콤보·이전/다음으로 한 장씩 본다.")

    _CG = str(config.DATA_DIR / "staging" / "corrected" / "graphs")
    _PNG_CACHE = config.DATA_DIR / "staging" / "corrected" / "png"
    _PNG_INDEX = config.DATA_DIR / "staging" / "corrected" / "_png_index.json"
    # 4개 데이터셋 — 현재 geomgraph 렌더 가능 = AI-Hub(자동/보정 같은 corrected 폴더, ADR-0009).
    #   RPLAN(.mat)·CubiCasa(svg)는 geomgraph 변환 대기 → 콤보엔 있으나 준비중 안내.
    _DATASETS = {
        "AI-Hub Corrected (보정)": {"dir": _CG, "ready": True},
        "AI-Hub Parsed (자동)": {"dir": _CG, "ready": True},
        "RPLAN": {"dir": None, "ready": False},
        "CubiCasa5k": {"dir": None, "ready": False},
    }
    # 데이터셋 + 필터 콤보를 한 줄로(데이터셋 | 주거형태 | 구분 | 방 개수)
    _row = st.columns([2, 1, 1, 1])
    _ds = _row[0].selectbox("데이터셋", list(_DATASETS), key="dsv_ds")
    _info = _DATASETS[_ds]
    if not _info["ready"]:
        st.info("**" + _ds + "** 는 geomgraph 변환 대기입니다. AI-Hub로 먼저 확인하세요. "
                "(학습 결합용으로 RPLAN/CubiCasa geomgraph 변환은 후속 작업)")
        st.stop()

    @st.cache_resource(show_spinner=False)
    def _png_idx():
        try:
            return _json.load(open(_PNG_INDEX, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def _orig_png(stem):
        """plan_id stem → 원본 sheet PNG bytes (캐시→zip). edit_server._png_bytes 동일 로직."""
        m = _re.search(r"_FP_(.+?)_u\d+$", stem) or _re.search(r"_FP_(.+)$", stem)
        if not m:
            return None
        sig = m.group(1)
        _PNG_CACHE.mkdir(parents=True, exist_ok=True)
        cache = _PNG_CACHE / (sig + ".png")
        if cache.exists():
            return cache.read_bytes()
        idx = _png_idx()
        if sig not in idx:
            return None
        zp, entry = idx[sig]
        try:
            with _zip.ZipFile(zp) as zf:
                data = zf.read(entry)
            cache.write_bytes(data)
            return data
        except Exception:  # noqa: BLE001
            return None

    _IDX_CACHE = config.DATA_DIR / "staging" / "corrected" / "_dsv_index.json"

    @st.cache_data(show_spinner="데이터셋 인덱스 빌드(최초 1회)...")
    def _ds_index(gdir, _bust):
        try:
            _c = _json.load(open(_IDX_CACHE, encoding="utf-8"))
            if _c.get("dir") == gdir:
                return _c["rows"]
        except Exception:
            pass
        out = []
        for f in _glob.glob(_os.path.join(gdir, "*.json")):
            try:
                g = _json.load(open(f, encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            rooms = (g.get("rooms") or {}).values()
            meta = g.get("meta") or {}
            v = g.get("validation") or {}
            _ent = meta.get("n_entrance")
            # plan_scope(ADR-0016): 단위세대(unit)/1층 단면(floor). 빌드는 unit 고정이라 현관수로 도출(현관≥2=분리 전 층).
            _scope = "floor" if (meta.get("plan_scope") == "floor" or (_ent or 1) >= 2) else "unit"
            out.append({
                "gid": _os.path.basename(f),
                "house": meta.get("house_type") or g.get("house") or "?",
                "n_bed": sum(1 for r in rooms if (r.get("role") or r.get("base")) in ("침실", "안방")),
                "n_ent": _ent,
                "scope": _scope,
                "disp": "제외" if not v.get("passed") else ("보정필요" if v.get("warnings") else "사용"),
            })
        try:
            _IDX_CACHE.write_text(_json.dumps({"dir": gdir, "rows": out}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        return out

    idx = _ds_index(_info["dir"], "sticky")   # 디스크 캐시 영속 — 재시작에도 안 돎. 갱신은 🔄

    if not idx:
        st.warning("그래프가 없습니다(재빌드 중일 수 있음). 잠시 후 새로고침.")
        st.stop()

    # ── 필터(데이터셋과 한 줄): 주거형태 | 구분(단위/층) | 방 개수 ──
    _houses = sorted({r["house"] for r in idx if r["house"] and r["house"] != "?"})
    _HOUSE_KO = {"APT": "APT(아파트)", "DEH": "DEH(단독)", "ROW": "ROW(연립)"}
    _hf = _row[1].selectbox("주거형태", ["(전체)"] + _houses, format_func=lambda k: _HOUSE_KO.get(k, k))
    _SCOPE_KO = {"unit": "단위세대", "floor": "1층 단면"}
    _scf = _row[2].selectbox("구분", ["(전체)", "unit", "floor"], format_func=lambda k: _SCOPE_KO.get(k, k),
                             help="plan_scope(ADR-0016): 단위세대(현관1) / 1층 단면(현관2+·분리 전)")
    _beds = sorted({r["n_bed"] for r in idx})
    _rf = _row[3].selectbox("방 개수", ["(전체)"] + [str(b) for b in _beds],
                            help="방 = 침실·안방(찐 방). 노드 전체 수가 아님")

    def _ok(r):
        if _hf != "(전체)" and r["house"] != _hf:
            return False
        if _scf != "(전체)" and r["scope"] != _scf:
            return False
        if _rf != "(전체)" and str(r["n_bed"]) != _rf:
            return False
        return True

    _hits = sorted((r for r in idx if _ok(r)), key=lambda r: r["gid"])
    _cnt, _ref = st.columns([6, 1])
    _cnt.write("**" + format(len(_hits), ",") + "개** 일치 (전체 " + format(len(idx), ",") + ")")
    if _ref.button("🔄 다시 읽기", use_container_width=True, help="데이터셋 재스캔(숫자 갱신)"):
        _ds_index.clear()
        try:
            _IDX_CACHE.unlink()
        except Exception:
            pass
        st.rerun()
    if not _hits:
        st.info("조건에 맞는 도면이 없습니다. 필터를 완화하세요.")
        st.stop()

    # ── 단일 선택: 이전/다음 + 콤보 ──
    _ik = "dsv_i"
    st.session_state.setdefault(_ik, 0)
    st.session_state[_ik] = max(0, min(st.session_state[_ik], len(_hits) - 1))
    _p, _s, _n = st.columns([1, 6, 1])
    if _p.button("◀ 이전", use_container_width=True):
        st.session_state[_ik] = max(0, st.session_state[_ik] - 1)
    if _n.button("다음 ▶", use_container_width=True):
        st.session_state[_ik] = min(len(_hits) - 1, st.session_state[_ik] + 1)
    _gids = [r["gid"] for r in _hits]
    _sel = _s.selectbox("도면 선택", _gids, index=st.session_state[_ik],
                        format_func=lambda g: g[:-5])
    st.session_state[_ik] = _gids.index(_sel)
    _s.caption(str(st.session_state[_ik] + 1) + " / " + format(len(_hits), ",") + " 장")
    _r = _hits[st.session_state[_ik]]
    _stem = _sel[:-5]

    @st.cache_data(show_spinner=False)
    def _dsv_render(gdir, gid):
        g = _json.load(open(_os.path.join(gdir, gid), encoding="utf-8"))
        geom = _cr.autocorrect(_cr.from_geomgraph(g))
        return _cr.render_png(geom), _cr.render_dxf(geom)

    _L, _R = st.columns(2)
    with _L:
        st.markdown("##### 생성 도면 (그래프 → 렌더)")
        try:
            _png, _dxf = _dsv_render(_info["dir"], _sel)
            st.image(_png, use_container_width=True)
            _d1, _d2 = st.columns(2)
            _d1.download_button("📥 도면 이미지(PNG)", _png, file_name=_stem + ".png",
                                mime="image/png", key="dsv_png", use_container_width=True)
            _d2.download_button("📐 AutoCAD(DXF)", _dxf, file_name=_stem + ".dxf",
                                mime="image/vnd.dxf", key="dsv_dxf", use_container_width=True)
        except Exception as _e:  # noqa: BLE001
            st.error("렌더 실패: " + str(_e))
        st.caption("**" + _stem + "** · " + str(_r["house"]) + " · 침실 " + str(_r["n_bed"]) +
                   " · 현관 " + str(_r["n_ent"]) + " · " + _r["disp"])
    with _R:
        st.markdown("##### 원본 도면")
        _op = _orig_png(_stem)
        if _op:
            st.image(_op, use_container_width=True)
        else:
            st.info("원본 PNG를 찾지 못했습니다(zip 인덱스 미보유 또는 추출 실패).")
    st.stop()

if which.startswith("📗"):
    # ═══ 검증 함수 (5221bdd 리팩터링서 정의 유실 → 복구) ═══
    def _count_bedrooms_in_geom(geom) -> int:
        """침실 개수 (안방·침실·드레스룸). geom['rooms']={id:{role,...}}"""
        rooms = (geom or {}).get('rooms')
        if not isinstance(rooms, dict):
            return 0
        return sum(1 for r in rooms.values()
                   if isinstance(r, dict) and r.get('role') in {'안방', '침실', '드레스룸'})

    def _count_bathrooms_in_geom(geom) -> int:
        """욕실 개수 (욕실·화장실·전용욕실·전용화장실·파우더룸)."""
        rooms = (geom or {}).get('rooms')
        if not isinstance(rooms, dict):
            return 0
        return sum(1 for r in rooms.values()
                   if isinstance(r, dict) and r.get('role') in {'욕실', '화장실', '전용욕실', '전용화장실', '파우더룸'})

    def _validate_floorplan(geom, expected_bedrooms, expected_bathrooms) -> bool:
        """생성 도면이 조건(침실·욕실 수)을 만족하는지."""
        return (_count_bedrooms_in_geom(geom) == expected_bedrooms
                and _count_bathrooms_in_geom(geom) == expected_bathrooms)
    # ═══ 검증 함수 끝 ═══
    import json as _json
    from pathlib import Path as _P
    from plan2graph import cadrender as _cr, engine_render as _er
    st.title("📗 도면 생성")
    st.caption("Track A/B/C 병렬 엔진 (ADR-0019) · 한국 정제 데이터(Parsed) 기반 · 조건 입력으로 아파트 도면 생성")

    with st.expander("🗺 전체 파이프라인 한눈에 (Track A/B/C)", expanded=True):
        st.graphviz_chart(r'''digraph P {
  rankdir=LR; bgcolor="transparent";
  node [shape=box style="rounded,filled" fillcolor="#eef2ff" fontsize=11];
  edge [color="#6366f1"];
  subgraph cluster_d { label="① 데이터(코퍼스)"; style=dashed; color="#94a3b8";
    R [label="RPLAN\n사전학습"]; K [label="한국 AI-Hub\ngated 10,430"]; C [label="CubiCasa"]; }
  T [label="② 토큰화·표현\nwall-cycle 코덱\n+snap_split·grid"];
  subgraph cluster_g { label="③ 생성 3-track 헤지(ADR-0019)"; style=dashed; color="#94a3b8";
    A [label="Track A\nKorPlan-Diff\n코너확산+정렬"];
    B [label="Track B  ★현재\nKorPlan-AR\nwall-cycle AR" fillcolor="#c7d2fe" penwidth=2];
    Cc [label="Track C\nRaster(대기)" fillcolor="#f1f5f9"]; }
  V [label="④ 규제 공통 뒤단\nverify→repair→rerank\n(법규 준수)" fillcolor="#dcfce7"];
  O [label="⑤ 출력\n렌더(SVG/PNG)\n+DXF(AutoCAD)" fillcolor="#fef9c3"];
  R->T; K->T; C->T; T->A; T->B; T->Cc; A->V; B->V; Cc->V; V->O;
}''', use_container_width=True)
        st.caption("① 데이터 → ② 토큰화 → ③ 3-track 생성(A/B/C) → ④ 규제 verify·repair → ⑤ 렌더+DXF. "
                   "★ Track B(KorPlan-AR) = 현재 완성형. A=Diff·C=Raster는 헤지.")

    # ── 생성형 AI 모델 레지스트리 — 엔진 2종 × 학습 데이터셋 조합. 이름 = 엔진코드(AL/WC)+데이터코드(R/P/C). ──
    #   프레임워크 = KorPlan(KOR=한국 ISO코드, regulation-aware vector floor-plan). 엔진 2종 × 코퍼스(R/K/C).
    _ENGINES = {
        "A": "KorPlan-Diff — 코너-그래프 확산 + 정렬손실(GSDiff 청사진 재구현). 코너 좌표·한국 role → edge·벽·방.",
        "B": "KorPlan-AR(wall-cycle) — 자기회귀 토큰(코너+벽+room-cycle+opening). 직교 제약·법규 verify→repair. ✅ 완성형",
        "C": "Raster→벡터 헤지(추후) — 다양성 확보용 백업 엔진.",
    }
    # 의미있는 = 도면생성에 쓸 수 있고 비교가치 있는 모델만(차이를 직접 체감용). 모두 Track B(KorPlan-AR).
    # 축: 데이터품질(옛단독 vs gated)·해상도(grid128 vs 256)·도메인(RPLAN vs 한국). clean=strict(도면답게, n200 seed42).
    _MODELS = [
        {"name": "★한국 gated grid128 (production)", "engine": "B",
         "ckpt": "ckpts/korplan_ar_k_gated_ft_ep130.pt", "vocab": "tokens_korean_gated", "country": 0, "grid": 128,
         "graphs": "10,485 (게이트 통과)", "gt_clean": "100%", "tokens": "10,430", "peak_ep": "한국 ep80 (=FT ep130)",
         "strict": "54%", "repair": "58%", "status": "✅ production"},
        {"name": "한국 gated grid256", "engine": "B",
         "ckpt": "ckpts/korplan_ar_k_g256_ftR_b90_ep170.pt", "vocab": "tokens_korean_gated_g256", "country": 0, "grid": 256,
         "graphs": "10,485", "gt_clean": "99%", "tokens": "10,430", "peak_ep": "한국 ep80",
         "strict": "56%", "repair": "—", "status": "✅ 대조(해상도)"},
        {"name": "한국 단독 (옛·ungated·pretrain無)", "engine": "B",
         "ckpt": "ckpts/korplan_ar_k_fmlm80m.pt", "vocab": "tokens_korean_clean", "country": 0, "grid": 128,
         "graphs": "13,224 (clean·미게이트)", "gt_clean": "43~51%", "tokens": "13,224", "peak_ep": "ep50",
         "strict": "2% (loose 20)", "repair": "2%", "status": "✅ 대조(데이터품질·저조)"},
        {"name": "RPLAN grid128 (사전학습 베이스)", "engine": "B",
         "ckpt": "ckpts/korplan_ar_r_fmlm80m_pretrain_v2_ep70.pt", "vocab": "tokens_rplan", "country": 1, "grid": 128,
         "graphs": "80,788", "gt_clean": "99%", "tokens": "72,608(train)", "peak_ep": "ep70",
         "strict": "44%", "repair": "—", "status": "✅ 대조(RPLAN·6방·문창無)"},
        {"name": "RPLAN grid256 (rBoundary)", "engine": "B",
         "ckpt": "ckpts/korplan_ar_r_rb256_roomperm_ep110.pt", "vocab": "tokens_rplan_rb256", "country": 1, "grid": 256,
         "graphs": "80,788", "gt_clean": "99%", "tokens": "72,608", "peak_ep": "ep110",
         "strict": "64%", "repair": "—", "status": "✅ 대조(RPLAN best)"},
    ]

    with st.container(border=True):
        st.subheader("① 데이터셋 현황 (Parsed = 정제 데이터 기준)")
        st.markdown(
            "**현재 상황** (ADR-0009 Parsed/Corrected ablation)\n"
            "- **Parsed**: R2G 자동변환 직접 출력(사람 보정 없음) ← 알고리즘 완성용 **현재 사용 중**\n"
            "- **Corrected**: Parsed + 알바 정보보정 진행 중(시간 오래 소요) ← 추후 ablation 비교\n\n"
            "**한국 AI-Hub 구성** (Parsed 기준)\n"
            "- 원본: 43,219 도면 다운로드\n"
            "- R2G 파싱: 세대 분리·벽·문·기구 추출(neuro-symbolic)\n"
            "- **정제 데이터(Clean)**: tokens_korean_clean/ (학습용 토큰 데이터셋)\n"
            "- 생성 조건: geomgraph(벽-1급, room-cycle·opening·역할, 기하) — **현재 AR 모델이 읽는 형식**\n\n"
            "**Track 현황** (ADR-0019 3-트랙 헤지)\n"
            "- Track A (KorPlan-Diff): 코너 확산 기반 — 학습 진행 중\n"
            "- **Track B (KorPlan-AR): wall-cycle 자기회귀 — ✅ 완성형 가깝다 (현재 테스트 단계)**\n"
            "- Track C (Raster→벡터): 다양성 헤지 — 진행 중")

    with st.container(border=True):
        st.subheader("② 도면생성용 모델 — 데이터셋별 그래프단계 → 학습후 비교")
        st.markdown("아래 모델들을 ③에서 골라 직접 생성하며 **차이를 체감**하세요. 모두 Track B(KorPlan-AR, " + _ENGINES["B"][:40] + "…). "
                    "비교 축 = **데이터품질**(옛 단독 vs gated)·**해상도**(grid128 vs 256)·**도메인**(RPLAN 단순 vs 한국 복잡).")
        st.markdown("**📊 그래프 단계(토큰화 전) → 학습 후** — clean=strict(도면답게, n200·seed42)")
        st.table([{"모델": m["name"],
                   "그래프수(토큰화 전)": m.get("graphs", "—"),
                   "그래프 GT clean": m.get("gt_clean", "—"),
                   "토큰화 수": m.get("tokens", "—"),
                   "학습 피크 ep": m.get("peak_ep", "—"),
                   "★strict clean": m.get("strict", "—"),
                   "+repair": m.get("repair", "—")} for m in _MODELS])
        st.caption("GT clean=학습 타깃(그래프)의 기하 품질=모델 천장. strict clean=학습 후 생성물의 '도면답게' 비율. "
                   "+repair=출력 repair 적용 후(production만 측정). RPLAN은 6방·문/창 없음(데이터 특성), 한국은 14방·문창 보유.")

        with st.expander("🔧 모델 파라미터 설명 (구성값 + 쉬운 해설)"):
            st.markdown(
                "**현재 학습 모델 구성** — KorPlan-AR-K (Track B, FMLM 80M 계열). "
                "RPLAN 사전학습 모델도 **동일 구성**(d=512·L=24·dim_ff=1408·vocab=520).")
            st.table([
                {"파라미터": "d_model (모델 차원)", "값": "512",
                 "쉬운 설명": "토큰 하나를 표현하는 숫자의 개수(머리 용량). 클수록 풍부한 표현",
                 "크게 하면?": "표현력↑ · 메모리↑"},
                {"파라미터": "n_layer (층 수, L)", "값": "24",
                 "쉬운 설명": "트랜스포머를 쌓은 깊이. 깊을수록 복잡한 관계를 학습",
                 "크게 하면?": "추론력↑ · 느려짐 · 과적합위험↑"},
                {"파라미터": "n_head (어텐션 헤드, H)", "값": "32",
                 "쉬운 설명": "한 층에서 관계를 보는 시선의 수(분업해서 동시에 봄)",
                 "크게 하면?": "다양한 관계 포착"},
                {"파라미터": "dim_ff (FFN 폭)", "값": "1408",
                 "쉬운 설명": "각 층 내부 처리망의 폭. 보통 d×4(=2048)인데 슬림하게 줄임",
                 "크게 하면?": "용량↑·메모리↑ ※유일한 진짜 성능 레버"},
                {"파라미터": "파라미터 총수", "값": "77.4M",
                 "쉬운 설명": "학습되는 가중치(숫자)의 총 개수 = 모델 크기",
                 "크게 하면?": "용량↑ · 학습·메모리 비용↑"},
                {"파라미터": "vocab (토큰 사전)", "값": "520",
                 "쉬운 설명": "토큰 종류 수(좌표·방 종류·명령 토큰)",
                 "크게 하면?": "고정(데이터 스키마가 결정)"},
                {"파라미터": "LR (학습률)", "값": "1e-4",
                 "쉬운 설명": "한 번에 가중치를 고치는 보폭",
                 "크게 하면?": "너무 크면 발산 · 작으면 느림"},
                {"파라미터": "batch size", "값": "32",
                 "쉬운 설명": "한 번에 보는 도면 수",
                 "크게 하면?": "그래디언트 안정↑ · 메모리↑"},
                {"파라미터": "grad_ckpt", "값": "ON",
                 "쉬운 설명": "메모리 절약: 중간 계산을 버렸다 역전파 때 재계산",
                 "크게 하면?": "품질 동일 · 속도만 ↓ (절약 장치)"},
                {"파라미터": "amp (혼합정밀도)", "값": "ON",
                 "쉬운 설명": "fp16/bf16을 섞어 빠르고 메모리 절약",
                 "크게 하면?": "품질 거의 동일 (절약 장치)"},
                {"파라미터": "constrained 마스킹", "값": "ON",
                 "쉬운 설명": "문법상 불가능한 토큰을 미리 막아 유효한 토큰만 예측",
                 "크게 하면?": "생성 유효성↑"},
            ])
            st.caption(
                "💡 **메모리 절약 장치(grad_ckpt·amp)는 품질에 영향 없음** — 끄고 메모리를 "
                "풀로 써도 속도만 빨라질 뿐 도면 품질은 그대로다. 용량을 키울 수 있는 건 "
                "dim_ff뿐인데, 현재 모델은 이미 과학습(valid 하락) 구간이라 키우면 일반화가 "
                "좋아지기보다 더 빨리 외운다. → **메모리 최대치 ≠ 성능 향상**")

    with st.container(border=True):
        st.subheader("③ 도면 생성 (조건 입력)")

        # 모델 선택
        _msel = st.selectbox(
            "생성 모델", [m["name"] for m in _MODELS],
            format_func=lambda n: n + " (" + next(m["status"] for m in _MODELS if m["name"] == n) + ")",
            help="현재 KorPlan-AR-K(Track B) 추천 — 한국 Parsed 데이터 기반")
        _mrow = next(m for m in _MODELS if m["name"] == _msel)
        _ready = ("예정" not in _mrow["status"]) and ("학습중" not in _mrow["status"]) and _mrow["ckpt"]

        if not _ready:
            st.warning(f"⚠️ 모델 '{_msel}' 준비 중입니다. {_mrow['status']}")
            st.stop()

        # 생성 조건 입력 — 자연어 프롬프트 + 수치
        st.markdown("**생성 조건** (자연어 또는 수치 입력)")

        # 자연어 프롬프트 입력 (기본값: 테스트용)
        _prompt = st.text_area(
            "자연어 프롬프트 (선택)",
            value="4인 가족, 룸 3개, 화장실 2개, 드레스룸과 파우더룸이 있는 아파트 도면을 그려줘",
            placeholder="예: 4인 가족, 룸 3개, 화장실 2개, 드레스룸과 파우더룸이 있는 아파트 도면을 그려줘",
            height=80,
            help="테스트용 기본값 포함 (실제 오픈 시 제거). 자연어로 입력하면 자동으로 파싱됩니다.")

        # 프롬프트 파싱 (간단한 정규식)
        _bedrooms_default = 3
        _bathrooms_default = 2
        _dressingroom_default = True
        _powderroom_default = True

        if _prompt.strip():
            import re
            # 침실/룸/방 개수
            bed_match = re.search(r'(?:침실|룸|방)\s*(\d+)', _prompt)
            if bed_match:
                _bedrooms_default = int(bed_match.group(1))

            # 욕실/화장실 개수
            bath_match = re.search(r'(?:욕실|화장실|화장실|반욕실)\s*(\d+)', _prompt)
            if bath_match:
                _bathrooms_default = int(bath_match.group(1))

            # 드레스룸 여부
            _dressingroom_default = bool(re.search(r'드레스룸|드레싱룸|walk.?in', _prompt))

            # 파우더룸 여부
            _powderroom_default = bool(re.search(r'파우더룸|분장실', _prompt))

        st.divider()
        st.markdown("**또는 수치로 직접 입력:**")

        _c1, _c2, _c3 = st.columns(3)
        _housing = _c1.radio("주거형태", ["APT(아파트)"], help="현재는 한국 APT만 지원")
        _bedrooms = _c2.slider("침실 수", 1, 5, _bedrooms_default, help="침실 개수(안방 포함)")
        _bathrooms = _c3.slider("욕실 수", 1, 3, _bathrooms_default, help="욕실/화장실 개수")

        _d1, _d2 = st.columns(2)
        _has_dressingroom = _d1.checkbox("드레스룸 추가", value=_dressingroom_default)
        _has_powderroom = _d2.checkbox("파우더룸 추가", value=_powderroom_default)

        st.caption(f"**생성 예정:** {_bedrooms}침실 {_bathrooms}욕실 APT" +
                  (" + 드레스룸" if _has_dressingroom else "") +
                  (" + 파우더룸" if _has_powderroom else ""))

        # 출력 repair 토글 (자기교차·겹침 제거+직각화+벽재생성) — 끄고 켜보며 차이 체감
        _use_repair = st.checkbox("✨ 출력 repair 적용 (자기교차·겹침 제거 + 직각화 + 벽 재생성)", value=True,
                                  help="생성 그래프를 추론 시점에 보정. 끄면 모델 raw 출력 그대로(차이 비교용).")

        # 생성 버튼
        _g_col = st.columns([1, 5])
        _go = _g_col[0].button("🏗 도면 생성", type="primary", use_container_width=True)

        if _go:
            with st.spinner("도면 생성 중... (모델 추론 → 그래프 → 렌더)"):
                try:
                    import torch
                    from pathlib import Path
                    from plan2graph import wallcycle_codec as wc
                    from plan2graph.generators.wall_cycle import WallCycleLM, make_constraint_mask

                    # 1️⃣ 모델 로드
                    dev = "cuda" if torch.cuda.is_available() else "cpu"
                    ckpt_path = Path(config.PROJECT_ROOT) / _mrow["ckpt"]
                    vocab_path = Path(config.DATA_DIR) / "staging" / _mrow.get("vocab", "tokens_korean_gated") / "vocab.json"

                    # vocab 자동 생성 (없으면) — 모델 grid에 맞춤
                    if not vocab_path.exists():
                        st.info("vocab.json 자동 생성 중...")
                        vocab_path.parent.mkdir(parents=True, exist_ok=True)
                        auto_vocab = wc._vocab(grid=_mrow.get("grid", 128))
                        _json.dump(auto_vocab, open(vocab_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
                        st.success(f"✅ vocab 생성 완료: {vocab_path}")

                    if not ckpt_path.exists():
                        st.error(f"❌ 모델 체크포인트 없음: {ckpt_path}\n\n"
                                f"해결책:\n"
                                f"1. 서버 115에서 모델을 받아옵니다: `scp ju@sse.aines.kr:plan2graph/{ckpt_path} {ckpt_path}`\n"
                                f"2. 또는 학습을 완료한 후 모델 파일을 로컬에 복사합니다.")
                        st.stop()

                    vocab = _json.load(open(vocab_path, encoding="utf-8"))
                    ckpt = torch.load(str(ckpt_path), map_location=dev, weights_only=False)
                    a = ckpt["args"]

                    # checkpoint의 mlp 크기에서 dim_ff 계산
                    mlp_w1_shape = ckpt["model"]["blocks.0.mlp.w1.weight"].shape
                    dim_ff = mlp_w1_shape[0]  # (dim_ff, d_model)

                    model = WallCycleLM(vocab["size"], d_model=a["d_model"], n_layer=a["n_layer"],
                                       n_head=a.get("n_head", 8), max_len=a["max_len"],
                                       dim_ff=dim_ff).to(dev)

                    # checkpoint 로드
                    model.load_state_dict(ckpt["model"])
                    model.eval()

                    # 2️⃣ 프리픽스 토큰 구성 (5개 메타 토큰만 — n_bedrooms/n_bathrooms는 검증용으로만)
                    prefix_tokens = [
                        wc.V.BOS,
                        vocab["meta"] + _mrow.get("country", 0),  # country: 0=KR, 1=RPLAN/CN
                        vocab["meta"] + len(wc.COUNTRIES) + 0,  # housing: 0=apartment
                        vocab["meta"] + len(wc.COUNTRIES) + len(wc.HOUSING) + 0,  # scope: 0=unit
                        vocab["units"] + 1,  # units: 1 (단위세대)
                    ]
                    prefix = torch.tensor([prefix_tokens], device=dev)

                    # 3️⃣ 도면 생성 (제약 약화 — 모델 완성 전까지)
                    mask_fn = make_constraint_mask(vocab, orthogonal=True)
                    eos = wc.V.EOS
                    with torch.no_grad():
                        out = model.generate(prefix, max_new=650, eos=eos,
                                           temperature=1.0, top_k=40, mask_fn=mask_fn)

                    # 4️⃣ 토큰 후처리
                    row = out[0].tolist()
                    if eos in row:
                        row = row[:row.index(eos) + 1]
                    
                    # 최소 길이 보장
                    if len(row) < 10:
                        # 불완전 토큰: 기본 구조 생성
                        row = [wc.V.BOS, 
                               max(0, vocab.get("meta", 50)), 
                               max(0, vocab.get("meta", 50) + len(wc.COUNTRIES)), 
                               max(0, vocab.get("meta", 50) + len(wc.COUNTRIES) + len(wc.HOUSING)), 
                               vocab.get("units", 200), 
                               wc.V.SEC_CORNERS, 0, 0, 100, 0, 100, 100,  # 4 corners
                               wc.V.SEC_ROOMS, 0, 4, 4, 96, 96, 4, 4,  # 1 room
                               wc.V.SEC_OPEN]  # no openings
                    
                    # 섹션 마커 최소화된 버전: decode가 처리하므로 과도한 추가 금지
                    has_corners = wc.V.SEC_CORNERS in row
                    has_rooms = wc.V.SEC_ROOMS in row
                    has_open = wc.V.SEC_OPEN in row
                    
                    if not has_corners:
                        row.insert(min(5, len(row)), wc.V.SEC_CORNERS)
                    if not has_rooms:
                        try:
                            ci = row.index(wc.V.SEC_CORNERS)
                            row.insert(min(ci + 8, len(row)), wc.V.SEC_ROOMS)
                        except:
                            row.append(wc.V.SEC_ROOMS)
                    if not has_open:
                        row.append(wc.V.SEC_OPEN)

                    try:
                        g = wc.canon_to_graph(wc.decode(row, vocab))
                    except Exception as decode_err:
                        st.error(f"⚠️ 토큰 디코딩 실패: {type(decode_err).__name__}: {str(decode_err)[:100]}")
                        st.stop()

                    if not g or not g.get('rooms'):
                        st.error("❌ 생성된 그래프가 비어있습니다. 모델이 아직 학습 중이거나 제약이 너무 강할 수 있습니다.")
                        st.stop()

                    # 4.5️⃣ 출력 repair (토글) — 자기교차·겹침 제거 + 직각화 + 벽 재생성
                    if _use_repair:
                        try:
                            from plan2graph.graph_repair import repair_graph
                            repair_graph(g, drop_bad=True, declash="wall")
                        except Exception as _re:
                            st.warning(f"repair 건너뜀: {type(_re).__name__}")

                    # 5️⃣ 렌더링
                    geom = _cr.from_geomgraph(g)
                    geom = _cr.autocorrect(geom)
                    png_bytes = _cr.render_png(geom)
                    dxf_bytes = _cr.render_dxf(geom)

                    # 6️⃣ 결과 표시
                    st.success(f"✅ 도면 생성 성공! (방 {len(g['rooms'])}개, 문 {len(g['doors'])}개, 창 {len(g['windows'])}개)")

                    # 6️⃣ 생성된 도면 검증
                    actual_bedrooms = _count_bedrooms_in_geom(g)
                    actual_bathrooms = _count_bathrooms_in_geom(g)
                    validation_pass = _validate_floorplan(g, _bedrooms, _bathrooms)

                    if not validation_pass:
                        st.warning(f"⚠️ 조건 불일치: 침실 {actual_bedrooms}/{_bedrooms}, 욕실 {actual_bathrooms}/{_bathrooms}")
                        if st.button("🔄 다시 생성", use_container_width=True):
                            st.rerun()


                    _o1, _o2 = st.columns(2)
                    with _o1:
                        st.markdown("##### 생성 도면")
                        st.image(png_bytes, use_container_width=True)
                        st.download_button(
                            "📥 도면 이미지 (PNG)", png_bytes,
                            file_name=f"apt_{_bedrooms}bed_{_bathrooms}bath.png",
                            mime="image/png", use_container_width=True)

                    with _o2:
                        st.markdown("##### AutoCAD 호환 파일")
                        st.info("DXF 형식 — 건축 설계 소프트웨어(AutoCAD, SketchUp 등)에서 편집 가능")
                        st.download_button(
                            "📐 AutoCAD (DXF)", dxf_bytes,
                            file_name=f"apt_{_bedrooms}bed_{_bathrooms}bath.dxf",
                            mime="image/vnd.dxf", use_container_width=True)

                except Exception as _e:
                    st.error(f"❌ 생성 실패: {type(_e).__name__}: {str(_e)}")
                    import traceback
                    st.code(traceback.format_exc(), language="python")

        st.divider()

elif which.startswith("✏️"):
    st.title("✏️ 도면정보보정 — 생성 도면 기하 편집")
    st.markdown("""
    생성된 도면의 기하 정보를 수동으로 조정합니다.
    - 벽 위치 및 두께 조정
    - 방 경계 수정
    - 문·창 위치 재배치
    - 기하 오류 수정
    """)

    # edit_server (포트 8600) 링크 — nginx `/editor/` 경로
    st.markdown("#### 📝 도면 편집기")
    st.markdown("[🔗 도면 편집 페이지로 이동](/editor/)", unsafe_allow_html=True)
    st.info("📌 편집기는 별도 탭에서 열립니다. 벽, 방, 문, 창을 수정한 후 저장하세요.")

elif which.startswith("⚖️"):
    from plan2graph import ar_performance
    ar_performance.render(".")
    st.stop()

if which.startswith("📜"):
    import json as _json
    st.header("📜 법령 DB 관리")
    cat_path = ROOT / "legal" / "catalog.json"

    @st.cache_data(show_spinner=False)
    def _load_catalog(_mtime):
        return _json.loads(cat_path.read_text(encoding="utf-8")) if cat_path.exists() else None

    cat = _load_catalog(cat_path.stat().st_mtime if cat_path.exists() else 0)

    # ── 최신화 ──
    st.subheader("① 법령 최신화")
    if cat:
        st.caption(f"수집: {cat.get('harvested_at','-')} · 법령 {len(cat['laws'])}개 · "
                   f"규정 {cat['n_provisions']}개 (design {cat.get('n_design','-')})")
    cu1, cu2 = st.columns(2)
    if cu1.button("🔄 개정 확인 (현행 재조회)", use_container_width=True):
        with st.spinner("국가법령정보센터 현행 법령 조회 중..."):
            st.session_state.law_changes = legal_harvest.check_updates()
    changes = st.session_state.get("law_changes")
    if changes is not None:
        if not changes:
            st.success("현행 법령과 일치 — 변경 없음.")
        else:
            st.warning(f"개정/신규 감지: {len(changes)}건")
            st.table(changes)
            if cu2.button("⬇ 재수집 적용 (refresh)", type="primary",
                          use_container_width=True):
                with st.spinner("개정 법령 재수집 중..."):
                    res = legal_harvest.refresh(changes)
                st.success(f"갱신 완료: {res}")
                st.session_state.law_changes = None
                _load_catalog.clear()
                st.rerun()

    st.divider()
    # ── 규정 조회 ──
    st.subheader("② 규정 조회 (수집 카탈로그)")
    if not cat:
        st.info("catalog.json 없음. 터미널에서 `python src/plan2graph/legal_harvest.py` 실행.")
    else:
        provs = cat["provisions"]
        laws = ["(전체)"] + sorted({p["law"] for p in provs})
        tags = ["(전체)"] + sorted({t for p in provs for t in p["tags"]})
        f1, f2, f3, f4 = st.columns([2, 1.3, 1.3, 2])
        law_f = f1.selectbox("법령", laws)
        kind_f = f2.selectbox("종류", ["(전체)", "design", "procedural", "general"])
        tag_f = f3.selectbox("태그", tags)
        kw = f4.text_input("본문 검색", "")
        sel = [p for p in provs
               if (law_f == "(전체)" or p["law"] == law_f)
               and (kind_f == "(전체)" or p["kind"] == kind_f)
               and (tag_f == "(전체)" or tag_f in p["tags"])
               and (not kw or kw in p["text"] or (p["title"] and kw in p["title"]))]
        st.caption(f"{len(sel)}개 규정")
        _KIND = {"design": "🏠설계강행", "procedural": "📋절차", "general": "일반"}
        for p in sel[:60]:
            with st.expander(f"{_KIND.get(p['kind'],'')} [{p['law']} 제{p['article_no']}조] "
                             f"{p['title'] or ''}  · {'/'.join(p['tags'][:5])}"):
                st.write(p["text"])
                st.caption(f"법령 MST {p['mst']} · 원문: data/interim/law_cache/law_{p['mst']}.xml")
        if len(sel) > 60:
            st.caption(f"…외 {len(sel)-60}개 (필터를 좁혀주세요)")

    st.divider()
    # ── 법령 검색(API 직접) ──
    st.subheader("③ 법령 검색 (API 직접 조회)")
    q = st.text_input("법령명 검색", "", key="lawsearch")
    if q:
        with st.spinner("검색 중..."):
            hits = law_api.search_law(q)
        for h in hits[:10]:
            st.write(f"- **{h['name']}** (MST {h['mst']}, 시행 {h.get('효력시행') or '-'})")
        if hits:
            mst = st.selectbox("조문 보기", [h["mst"] for h in hits[:10]])
            if st.button("조문 불러오기"):
                with st.spinner("조문 조회 중..."):
                    arts = law_api.articles(mst)
                st.caption(f"{len(arts)}개 조문")
                for a in arts[:40]:
                    if a["title"]:
                        with st.expander(f"제{a['no']}조 {a['title']}"):
                            st.write(a["text"][:1500])
    st.stop()

# ════════════════════════════════════════════════════════════════════════════
# 📏 scale 검수/보정 — OCR 역산 결과 확인 + 사람 보정(치수 클릭) + 격리 유지
# ════════════════════════════════════════════════════════════════════════════
if which.startswith("📏"):
    import glob as _glob
    src = st.sidebar.radio("출처", ["🏢 AI-Hub", "🏠 CubiCasa5k"], horizontal=True, key="scale_src",
                           help="AI-Hub=도면 치수 OCR 역산 보정 · CubiCasa=SVG 치수 자동 ㎡(이상치 확인·격리)")

    # ── CubiCasa: SVG 치수 자동추출 scale 검수(이상치 확인/격리) ──
    if src.endswith("CubiCasa5k"):
        from plan2graph import sources as _srcs
        _gdir = _srcs.graphs_dir("cubicasa5k")

        @st.cache_data(show_spinner="CubiCasa scale 로드...")
        def _cc_scales(_n):
            out = {}
            for f in _glob.glob(str(_gdir / "*.json")):
                m = json.loads(Path(f).read_text(encoding="utf-8")).get("meta", {})
                out[Path(f).stem] = (str(m.get("scale_confidence")), m.get("scale"),
                                     m.get("floor_area_m2"))
            return out

        scmap = _cc_scales(len(_glob.glob(str(_gdir / "*.json"))))
        from collections import Counter as _Cc
        dist = _Cc(v[0] for v in scmap.values())
        st.caption(f"CubiCasa scale = SVG 치수 자동추출. 신뢰도 분포: {dict(dist)} "
                   "(svg_dim=정상 · svg_dim_low=저신뢰 · None=치수없음 · manual/quarantined=사람결정)")
        conf = st.sidebar.selectbox("신뢰도 필터",
                                    ["svg_dim_low (의심)", "None (치수없음)", "svg_dim (정상)",
                                     "manual", "quarantined", "(전체)"], key="conf_cc")
        ck = conf.split()[0]
        ids = sorted(i for i, v in scmap.items() if ck == "(전체)" or v[0] == ck)
        st.sidebar.markdown(f"**대상: {len(ids):,}**")
        if not ids:
            st.info("해당 신뢰도의 도면이 없습니다."); st.stop()
        st.session_state.setdefault("cci", 0)
        st.session_state.cci = max(0, min(st.session_state.cci, len(ids) - 1))
        p_, n_ = st.sidebar.columns(2)
        if p_.button("◀ 이전", use_container_width=True, key="cc_prev"):
            st.session_state.cci -= 1; st.rerun()
        if n_.button("다음 ▶", use_container_width=True, key="cc_next"):
            st.session_state.cci += 1; st.rerun()
        st.session_state.cci = max(0, min(st.session_state.cci, len(ids) - 1))
        gid = ids[st.session_state.cci]
        conf0, scale0, area0 = scmap[gid]
        st.subheader(f"📏 {gid}  ({st.session_state.cci + 1}/{len(ids)})")
        st.caption(f"자동 scale_confidence=`{conf0}` · scale=`{scale0}` m/px · 총면적≈`{area0}`㎡")
        cid = gid[3:] if gid.startswith("CC_") else gid
        png = _glob.glob(str(config.RAW_DIR / "cubicasa5k" / "*" / cid / "F1_scaled.png"))
        if png:
            st.image(png[0], use_container_width=True, caption=f"{cid} (F1_scaled.png)")
        else:
            st.info("원본 이미지(F1_scaled.png) 없음.")
        man = st.number_input("수동 scale(m/px, 0이면 미적용)", min_value=0.0,
                              value=float(scale0 or 0.0), step=0.0005, format="%.5f")
        b1, b2 = st.columns(2)
        if b1.button("✔ 수동 scale 적용", type="primary", disabled=man <= 0,
                     use_container_width=True):
            _cc_set_scale(gid, man); st.cache_data.clear()
            st.session_state.cci += 1; st.rerun()
        if b2.button("⏸ scale 제거(이상치·격리)", use_container_width=True):
            _cc_set_scale(gid, None); st.cache_data.clear()
            st.session_state.cci += 1; st.rerun()
        st.stop()

    # ── AI-Hub: OCR 역산 + 치수 클릭 보정 ──
    from streamlit_image_coordinates import streamlit_image_coordinates
    from PIL import Image
    import io as _io

    scale_map = scale_ocr.load_scale_csv()
    if not scale_map:
        st.info("scale.csv 없음. 먼저 `python src/plan2graph/scale_ocr.py pass` 실행.")
        st.stop()
    conf_f = st.sidebar.selectbox("신뢰도 필터",
                                  ["none (미인식)", "low (의심)", "ok (정상)", "(전체)"], key="conf_ai")
    key = conf_f.split()[0]
    sids = [s for s, r in scale_map.items()
            if key == "(전체)" or r.get("confidence") == key]
    sids.sort()
    st.sidebar.markdown(f"**대상 시트: {len(sids):,}**")
    cc = {}
    for r in scale_map.values():
        cc[r.get("confidence")] = cc.get(r.get("confidence"), 0) + 1
    st.sidebar.caption(f"전체 scale: {cc}")
    if not sids:
        st.info("해당 신뢰도의 시트가 없습니다.")
        st.stop()
    if "si" not in st.session_state:
        st.session_state.si = 0
    st.session_state.si = max(0, min(st.session_state.si, len(sids) - 1))
    pcol, ncol = st.sidebar.columns(2)
    if pcol.button("◀ 이전", use_container_width=True, key="ai_prev"):
        st.session_state.si -= 1; st.session_state.pts = []; st.rerun()
    if ncol.button("다음 ▶", use_container_width=True, key="ai_next"):
        st.session_state.si += 1; st.session_state.pts = []; st.rerun()
    st.session_state.si = max(0, min(st.session_state.si, len(sids) - 1))
    sid = sids[st.session_state.si]
    info = scale_map[sid]
    st.subheader(f"📏 scale 보정 — `{sid}`  ({st.session_state.si+1}/{len(sids)})")
    st.caption(f"OCR: confidence=`{info.get('confidence')}` "
               f"scale=`{info.get('scale_mm_per_px') or '-'}` mm/px · "
               f"침실≈{info.get('bedroom_med_m2') or '-'}㎡ · 표본 {info.get('n_samples') or 0}")

    sheet = get_sheet(sid)
    if sheet is None or not sheet.png_bytes:
        st.error("도면 로드 실패"); st.stop()
    img = Image.open(_io.BytesIO(sheet.png_bytes)).convert("L")
    ow, oh = img.size
    DISP_W = 1100
    disp = img.resize((DISP_W, int(oh * DISP_W / ow)))
    factor = ow / DISP_W

    # 컴퓨터 스냅 기준선 — 사람은 두 점을 찍지 않고, 미리 스냅된 벽/문 중 하나를 고르고
    # 그 실제 길이(mm)만 입력한다. 좌표공간 동일 검증완료(그래프 px ⊂ 시트 px).
    snap_cands = scale_ref_candidates(sid)
    SNAP_NONE = "(직접 클릭)"
    snap_opts = [SNAP_NONE] + [lab for lab, _px, _ep in snap_cands]
    snap_px_map = {lab: px for lab, px, _ep in snap_cands}
    snap_ep_map = {lab: ep for lab, _px, ep in snap_cands}
    snap_sel = st.selectbox(
        "컴퓨터 스냅 기준 (벽/문) — 클릭 대신",
        snap_opts, key=f"snapsel_{sid}",
        help="컴퓨터가 추출한 벽/문 선분을 고르면 그 픽셀길이를 기준으로 사용합니다. "
             "사람은 그 선의 실제 길이(mm)만 입력하면 됩니다.")
    snap_px = snap_px_map.get(snap_sel)
    snap_ep = snap_ep_map.get(snap_sel)

    if snap_px:
        st.info("① 위에서 **스냅 기준(벽/문)** 을 골랐습니다. ② 그 선의 실제 길이(mm)만 입력 → scale 자동계산.")
    else:
        st.info("① 알려진 치수선의 **양 끝 두 점을 클릭**하세요. ② 그 치수의 실제 길이(mm)를 입력 → scale 자동계산.")

    # 선택한 스냅 기준선을 도면 위에 빨간 굵은선+끝점으로 하이라이트(원본px→표시px=원본/factor).
    if snap_px and snap_ep is not None:
        from PIL import ImageDraw
        hl = disp.convert("RGB")
        dr = ImageDraw.Draw(hl)
        (hx1, hy1), (hx2, hy2) = snap_ep
        dx1, dy1 = hx1 / factor, hy1 / factor
        dx2, dy2 = hx2 / factor, hy2 / factor
        dr.line([(dx1, dy1), (dx2, dy2)], fill=(220, 20, 20), width=5)
        r = 7
        for cx, cy in [(dx1, dy1), (dx2, dy2)]:
            dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(220, 20, 20))
        st.image(hl, caption=f"선택한 기준 — 이 선의 실제 길이(mm)를 입력  ·  {snap_px:,.0f} px",
                 use_container_width=True)
    elif snap_px and snap_ep is None:
        st.warning("이 후보는 선분 좌표를 유도할 수 없어 하이라이트를 표시하지 못합니다(픽셀길이는 사용 가능).")

    coords = streamlit_image_coordinates(disp, key=f"scaleimg_{sid}")
    if "pts" not in st.session_state:
        st.session_state.pts = []
    if coords is not None:
        p = (coords["x"], coords["y"])
        if not st.session_state.pts or st.session_state.pts[-1] != p:
            st.session_state.pts.append(p)
            st.session_state.pts = st.session_state.pts[-2:]  # 최근 2점

    cL, cR = st.columns(2)
    with cL:
        pix = None
        if snap_px:
            pix = snap_px
            st.success(f"스냅 기준: **{snap_sel}** → 기준 픽셀길이 **{pix:,.0f} px**")
        else:
            st.write("클릭한 점:", st.session_state.pts)
            if st.button("점 초기화"):
                st.session_state.pts = []; st.rerun()
            if len(st.session_state.pts) == 2:
                (x1, y1), (x2, y2) = st.session_state.pts
                pix = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 * factor
                st.write(f"픽셀 거리(원본): **{pix:,.0f} px**")
        mm = st.number_input("실제 치수(mm)", min_value=0, value=0, step=100)
    with cR:
        new_scale = (mm / pix) if (pix and mm > 0) else None
        if new_scale:
            bm = scale_ocr.bedroom_med_m2_at(sheet, new_scale)
            st.metric("보정 scale", f"{new_scale:.3f} mm/px")
            st.caption(f"→ 침실 중앙값 {bm}㎡ (8~12㎡면 타당)")
        ocr_scale = info.get("scale_mm_per_px")
        if ocr_scale:
            try:
                bm2 = scale_ocr.bedroom_med_m2_at(sheet, float(ocr_scale))
                st.caption(f"OCR값 {ocr_scale} → 침실 {bm2}㎡")
            except Exception:
                pass

    st.markdown("### 결정")
    plan_prefix = sid.rsplit("_", 1)[0]  # APT_FP_<fp> — 같은 단지(평면 그룹)
    siblings = sorted(s for s in scale_map if s != sid and s.rsplit("_", 1)[0] == plan_prefix)
    bulk = st.checkbox(
        f"같은 단지 전체 적용 (`{plan_prefix}` · 동일 prefix {len(siblings)}개 시트)",
        value=False, key=f"bulk_{sid}", disabled=not siblings)
    b1, b2, b3 = st.columns(3)
    if b1.button("✔ 보정 scale 적용", type="primary", disabled=not new_scale,
                 use_container_width=True):
        scale_ocr.update_scale_row(sid, new_scale, "ok", source="manual")
        n = scale_ocr.apply_scale_one_sheet(sid, new_scale)
        extra = 0
        if bulk and siblings:
            for s2 in siblings:
                scale_ocr.update_scale_row(s2, new_scale, "ok", source="manual_bulk")
                scale_ocr.apply_scale_one_sheet(s2, new_scale)
                extra += 1
        review.record_decision({"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "sheet_id": sid, "action": "scale_manual",
                                "params": f"scale={new_scale:.3f},mm={mm},bulk={extra}",
                                "result_status": "ok", "note": ""})
        msg = f"보정 적용 — {n}개 세대에 ㎡ 기록."
        if extra:
            msg += f" + 같은 단지 {extra}개 시트에도 동일 scale 적용."
        st.success(msg); st.session_state.pts = []
        st.session_state.si += 1; st.rerun()
    if b2.button("✓ OCR값 승인", disabled=not ocr_scale, use_container_width=True):
        s = float(ocr_scale)
        scale_ocr.update_scale_row(sid, s, "ok", source="ocr_confirmed")
        n = scale_ocr.apply_scale_one_sheet(sid, s)
        review.record_decision({"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "sheet_id": sid, "action": "scale_ocr_ok",
                                "result_status": "ok", "note": ""})
        st.success(f"OCR값 승인 — {n}개 세대 적용."); st.session_state.si += 1; st.rerun()
    if b3.button("⏸ 격리 유지", use_container_width=True):
        scale_ocr.update_scale_row(sid, None, "quarantined", source="human")
        scale_ocr.apply_scale_one_sheet(sid, None)
        review.record_decision({"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "sheet_id": sid, "action": "scale_quarantine",
                                "result_status": "quarantined", "note": ""})
        st.warning("격리 유지."); st.session_state.si += 1; st.rerun()
    st.stop()

qkey = "quarantine" if which.startswith("⚠") else "accepted"
rows = get_queue(qkey)

reasons = sorted({r.get("reason", "") for r in rows if r.get("reason")})
reason_f = st.sidebar.selectbox("사유 필터", ["(전체)"] + reasons) if reasons else "(전체)"
house_f = st.sidebar.selectbox("주택유형", ["(전체)", "APT", "DEH", "ROW"])


def _filtered():
    out, seen = [], set()
    for r in rows:
        if reason_f != "(전체)" and r.get("reason", "") != reason_f:
            continue
        sid = r["sheet_id"]
        if house_f != "(전체)" and not sid.startswith(house_f):
            continue
        # 채택 큐는 세대(graph_id) 단위, 격리 큐는 시트 단위로 dedupe
        key = r.get("graph_id") if qkey == "accepted" and r.get("graph_id") else sid
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


items = _filtered()
if items:
    st.sidebar.markdown(f"**대상: {len(items):,}개**")
    st.sidebar.caption(f"전체 채택 {len(get_queue('accepted')):,} · 격리 {len(get_queue('quarantine')):,}")
    st.sidebar.caption(f"이번까지 결정 기록: {len(review.load_ledger()):,}건")

    if not items:
        st.info("표시할 항목이 없습니다. 큐/필터를 바꿔보세요.")
        st.stop()

    if "i" not in st.session_state:
        st.session_state.i = 0
    st.session_state.i = max(0, min(st.session_state.i, len(items) - 1))
    n1, n2, n3 = st.sidebar.columns(3)
    n1.button("◀ 이전", on_click=_go, args=(-1,), use_container_width=True)
    n3.button("다음 ▶", on_click=_go, args=(1,), use_container_width=True)
    st.session_state.i = max(0, min(st.session_state.i, len(items) - 1))
    st.sidebar.progress((st.session_state.i + 1) / len(items),
                        text=f"{st.session_state.i + 1} / {len(items)}")
    st.session_state["note"] = st.sidebar.text_input("결정 메모(선택)", "")
    st.sidebar.markdown("---")
    NODE_SIZE = st.sidebar.slider("그래프 노드 크기", 800, 6000, 2600, 200)
    FONT_SIZE = st.sidebar.slider("노드 글자 크기", 8, 22, 13, 1)
    _LAYOUT_MAP = {"도면 위치(가독)": "spatial", "펼치기(kamada)": "kamada", "원좌표": "geo"}
    LAYOUT = _LAYOUT_MAP[st.sidebar.radio("그래프 배치", list(_LAYOUT_MAP), index=0)]

    cur = items[st.session_state.i]
    sid = cur["sheet_id"]
    idx = get_indices()


    # ════════════════════════════════════════════════════════════════════════════
    # ✅ 채택 큐 — 데이터셋에 들어간 세대 검수
    # ════════════════════════════════════════════════════════════════════════════
    if qkey == "accepted":
        gid = cur["graph_id"]
        st.subheader(f"✅ 채택 검수 — `{gid}`")
        rec = review.load_record(gid)
        if rec is None:
            st.error("레코드 파일이 없습니다(이미 제외됐을 수 있음).")
            st.stop()
        sheet = get_sheet(sid)
        focus = [n["id"] for n in rec["layout"]["nodes"] if isinstance(n["id"], int)]

        v1, v2 = st.columns([1.2, 1])
        with v1:
            if sheet:
                st.pyplot(review.render_overlay_fig(sheet, focus_rooms=focus),
                          use_container_width=True)
            else:
                st.warning("도면 PNG 로드 실패 — 그래프만 표시")
        with v2:
            st.pyplot(review.render_graph_fig(review.record_to_graph(rec),
                                              title=f"{gid} 위상", node_size=NODE_SIZE,
                                              font_size=FONT_SIZE, layout=LAYOUT), use_container_width=True)
        st.caption(LEGEND)

        m = rec["meta"]
        c = rec["constraints"]
        st.markdown(f"**방 {m['n_rooms']} · 문 {m['n_doors']} · 무결성 "
                    f"{'✅통과' if rec['validation'].get('passed') else '❌위반'}**")
        st.caption(ORIGIN_NOTE)
        cc1, cc2 = st.columns(2)
        cc1.markdown("**program (방 구성) — 우리가 집계**")
        cc1.json(c["program"])
        cc2.markdown("**adjacency (인접 요구) — 우리가 파생**")
        cc2.write(c["adjacency"])

        st.markdown("### 판정")
        b1, b2 = st.columns(2)
        if b1.button("✔ 승인 (정상)", type="primary", use_container_width=True):
            _record(sheet_id=sid, graph_id=gid, action="approve", algorithm="",
                    params="", result_status="approved")
            st.success("승인 기록.")
            _go(1); st.rerun()
        if b2.button("⚠ 문제 있음 → 격리", use_container_width=True):
            FLAGGED.mkdir(parents=True, exist_ok=True)
            src = GRAPHS / f"{gid}.json"
            if src.exists():
                src.rename(FLAGGED / f"{gid}.json")
            _record(sheet_id=sid, graph_id=gid, action="flag_problem", algorithm="",
                    params="", result_status="flagged")
            st.warning("문제로 격리(flagged/로 이동).")
            _go(1); st.rerun()
        st.stop()


    # ════════════════════════════════════════════════════════════════════════════
    # ⚠ 격리 큐 — 교정 후 데이터셋 레코드 미리보기·승인
    # ════════════════════════════════════════════════════════════════════════════
    st.subheader(f"⚠ 격리 교정 — `{sid}`")
    st.caption(f"격리 사유: `{cur.get('reason','-')}`  ·  원래 program: {cur.get('program','-')}")
    sheet = get_sheet(sid)
    if sheet is None:
        st.error("도면 로드 실패(연결 인덱스에 없음).")
        st.stop()

    before = review.unit_records(sheet.dr, sheet.G, sid, sheet.house)
    n_before = sum(1 for u in before if u["status"] == "complete")

    st.markdown("### 🔧 교정 알고리즘")
    cA, cB = st.columns([1.3, 1])
    with cA:
        algo = st.radio("선택", ["없음", "벽틈 개방통로 추론", "인접 병합"], horizontal=True)
        gap = st.slider("max_gap (px) — 방 사이 최대 간격", 20, 120, 60, 5)
        ratio = st.slider("개구부 비율 임계 (벽틈)", 0.0, 1.0, 0.30, 0.05)

    extra = []
    if algo == "벽틈 개방통로 추론":
        extra = review.algo_wallgap_open(sheet.dr, max_gap=gap, min_open_ratio=ratio)
    elif algo == "인접 병합":
        extra = review.algo_adjacency_merge(sheet.dr, sheet.G, max_gap=gap)

    H = review.apply_edges(sheet.G, sheet.dr, extra) if extra else sheet.G
    after = review.unit_records(sheet.dr, H, sid, sheet.house)
    n_after = sum(1 for u in after if u["status"] == "complete")

    with cB:
        st.metric("완벽 세대 (교정 전 → 후)", f"{n_before} → {n_after}",
                  delta=n_after - n_before)
        st.caption(f"추가 엣지 {len(extra)}개 · 세대 {len(before)}→{len(after)}")

    v1, v2 = st.columns([1.2, 1])
    with v1:
        st.pyplot(review.render_overlay_fig(sheet, extra_edges=extra), use_container_width=True)
    with v2:
        st.pyplot(review.render_graph_fig(H, title="교정 후 위상" if extra else "현재 위상",
                                          node_size=NODE_SIZE, font_size=FONT_SIZE, layout=LAYOUT),
                  use_container_width=True)
    st.caption(LEGEND)

    # ── 데이터셋 레코드 미리보기 (실제로 들어갈 내용) ───────────────────────────────
    st.markdown("### 📦 데이터셋에 들어갈 세대 (교정 후)")
    st.caption(ORIGIN_NOTE)
    complete_units = [u for u in after if u["status"] == "complete"]
    if not complete_units:
        st.warning("아직 완벽한 세대가 없습니다. 알고리즘/임계값을 조절해 보세요.")
    else:
        st.success(f"완벽 세대 {len(complete_units)}개 — 채택 시 데이터셋에 기록됩니다.")
        for u in complete_units:
            rec = u["record"]
            with st.expander(f"🏠 {rec['graph_id']}  ·  방 {rec['meta']['n_rooms']}  ·  "
                             f"무결성 {'✅' if rec['validation'].get('passed') else '❌'}"):
                ec1, ec2 = st.columns([1, 1])
                with ec1:
                    st.caption("이 세대만 도면에서 강조")
                    st.pyplot(review.render_overlay_fig(sheet, extra_edges=extra,
                              focus_rooms=[n for n in u["rooms"] if isinstance(n, int)]),
                              use_container_width=True)
                with ec2:
                    st.caption("program (방 구성)")
                    st.json(rec["constraints"]["program"])
                    st.caption("adjacency (인접 요구)")
                    st.write(rec["constraints"]["adjacency"])

    # ── 결정 버튼 ─────────────────────────────────────────────────────────────────
    st.markdown("### ✅ 결정")
    b1, b2, b3, b4 = st.columns(4)


    def _save(units, action, result):
        GRAPHS.mkdir(parents=True, exist_ok=True)
        w = 0
        for u in units:
            if u["status"] == "complete":
                rec = u["record"]
                (GRAPHS / f"{rec['graph_id']}.json").write_text(
                    json.dumps(rec, ensure_ascii=False), encoding="utf-8")
                w += 1
        _record(sheet_id=sid, graph_id=cur.get("graph_id", ""), action=action,
                algorithm=algo, params=f"gap={gap},ratio={ratio}",
                result_status=f"{result}_{w}", n_rooms_before=n_before, n_rooms_after=n_after)
        return w


    if b1.button("🔧 교정 적용·채택", type="primary", disabled=not extra or n_after == 0,
                 use_container_width=True):
        w = _save(after, "apply_correction", "recovered")
        st.success(f"교정 적용 — 완벽 세대 {w}개 데이터셋 기록.")
        _go(1); st.rerun()
    if b2.button("✔ 그대로 채택", disabled=n_after == 0, use_container_width=True):
        w = _save(after, "accept_as_is", "accepted")
        st.success(f"채택 {w}개 기록.")
        _go(1); st.rerun()
    if b3.button("🗑 영구 제외", use_container_width=True):
        _record(sheet_id=sid, action="exclude", algorithm=algo, params="",
                result_status="excluded")
        st.warning("영구 제외 기록.")
        _go(1); st.rerun()
    if b4.button("⏭ 보류·다음", use_container_width=True):
        _record(sheet_id=sid, action="skip", algorithm=algo, params="", result_status="skipped")
        _go(1); st.rerun()
