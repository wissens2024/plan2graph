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
def get_queue(which: str, _v: int = 0):
    return review.load_queue(which)


def _go(delta):
    st.session_state.i += delta


def _record(**kw):
    base = {"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "note": st.session_state.get("note", "")}
    base.update(kw)
    review.record_decision(base)


# ════════════════════════════════════════════════════════════════════════════
# 🧩 위상 큐레이션 — extract2 자동추출을 원본 위에서 사람이 교정·검증 → gold 저장
#   APT 전량 워크플로우(미검수/자동/검증완료/모호/제외). Phase0=표시·상태,
#   Phase1=클릭 노드/엣지 편집(streamlit-image-coordinates 필요).
# ════════════════════════════════════════════════════════════════════════════
@st.cache_resource(max_entries=24, show_spinner="유닛 자동추출(extract2)...")
def _gold_unit(sheet_id: str, unit_i: int):
    """extract2.load_unit 캐시 — (dr, png, u_idx, G, n_units). G에 shapely poly 포함."""
    from plan2graph import extract2
    return extract2.load_unit(sheet_id, unit_i)


def _curate_aihub():
    import time as _time
    from plan2graph import extract2, goldset

    st.title("🧩 위상 큐레이션 (AI-Hub · APT)")
    st.caption("자동추출(extract2)을 **원본 위에서** 사람이 교정·검증해 위상+기하를 최대 상세로 "
               "gold 데이터셋에 담는다. 추출이 맞으면 [검증완료], 틀리면 교정 후 저장.")

    units = goldset.load_index()
    if not units:
        st.warning("APT 유닛 인덱스가 없습니다. 서버에서 먼저:\n\n"
                   "```\npython scripts/build_apt_index.py\n```")
        return

    led = goldset.load_ledger()
    cnt = goldset.status_counts([u["unit_id"] for u in units])
    done = cnt.get("검증완료", 0)
    st.progress(done / max(1, len(units)),
                text=f"검증완료 {done:,} / 전체 {len(units):,}  ·  "
                     f"미검수 {cnt.get('미검수', 0):,} · 자동 {cnt.get('자동', 0):,} · "
                     f"모호 {cnt.get('모호', 0):,} · 제외 {cnt.get('제외', 0):,}")

    # ── 필터 + 네비게이션 ──
    flt = st.sidebar.selectbox("상태 필터", ["전체"] + list(goldset.STATUSES), index=0)
    pool = [u for u in units
            if flt == "전체" or goldset.status_of(u["unit_id"], led) == flt]
    if not pool:
        st.info(f"'{flt}' 상태인 유닛이 없습니다.")
        return
    st.sidebar.caption(f"필터 결과: {len(pool):,}개 (방수 오름차순)")

    if "cur_i" not in st.session_state:
        st.session_state.cur_i = 0
    st.session_state.cur_i = max(0, min(st.session_state.cur_i, len(pool) - 1))

    c1, c2, c3 = st.sidebar.columns(3)
    if c1.button("◀ 이전"):
        st.session_state.cur_i = max(0, st.session_state.cur_i - 1)
    if c2.button("다음 ▶"):
        st.session_state.cur_i = min(len(pool) - 1, st.session_state.cur_i + 1)
    if c3.button("⏭ 미검수"):
        nxt = next((k for k in range(st.session_state.cur_i + 1, len(pool))
                    if goldset.status_of(pool[k]["unit_id"], led) == "미검수"), None)
        if nxt is not None:
            st.session_state.cur_i = nxt
    sel_uid = st.sidebar.selectbox(
        "유닛 직접 선택", [u["unit_id"] for u in pool], index=st.session_state.cur_i,
        format_func=lambda x: f"{x}  [{goldset.status_of(x, led)}]")
    st.session_state.cur_i = [u["unit_id"] for u in pool].index(sel_uid)

    u = pool[st.session_state.cur_i]
    uid, sid, ui = u["unit_id"], u["sheet_id"], u["unit_i"]
    st.markdown(f"### {uid}  ·  방 {u['n_rooms']}개  ·  "
                f"상태 **{goldset.status_of(uid, led)}**  "
                f"({st.session_state.cur_i + 1}/{len(pool)})")

    # ── 자동추출 + 원본∥오버레이 ──
    try:
        dr, png, u_idx, G, n_units = _gold_unit(sid, ui)
    except Exception as e:
        st.error(f"추출 실패: {e}")
        st.exception(e)
        return
    st.caption(f"유닛 {ui}/{n_units - 1} · 방노드 {len(u_idx)} · "
               f"dr: rooms={len(dr.rooms)} doors={len(dr.doors)} "
               f"windows={len(dr.windows)} objects={len(dr.objects)} texts={len(dr.texts)}")
    if len(dr.objects) == 0:
        st.warning("⚠ OBJ(기구) 0개 — 욕실/화장실·주방 역할 유도 신호 없음. "
                   "이 시트는 OBJ 미병합(V2V 복구 대상)일 수 있음.")

    st.pyplot(extract2.render_review(dr, png, G), clear_figure=True)

    # ── 추출 결과 표(노드·엣지) ──
    cc1, cc2 = st.columns(2)
    with cc1:
        st.markdown("**노드(공간)**")
        st.dataframe([{"id": n, "base": G.nodes[n]["base"], "role": G.nodes[n]["role"],
                       "면적㎡": G.nodes[n].get("area"),
                       "기구": ",".join(G.nodes[n].get("fx", []))} for n in G],
                     use_container_width=True, height=240)
    with cc2:
        st.markdown("**엣지(연결)**")
        st.dataframe([{"a": G.nodes[a]["role"], "b": G.nodes[b]["role"],
                       "via": d["via"]} for a, b, d in G.edges(data=True)],
                     use_container_width=True, height=240)
        bb = sum(1 for a, b in G.edges
                 if G.nodes[a]["base"] == "침실" and G.nodes[b]["base"] == "침실")
        st.caption(f"침실-침실 직접연결: **{bb}** (0이 정상 — 복도/전실 경유여야)")

    st.info("✏️ 클릭 기반 노드/엣지 교정은 **Phase 1**(서버에 `streamlit-image-coordinates` "
            "설치 후). 지금은 추출이 정확한 유닛을 [검증완료]로 gold 저장할 수 있습니다.")

    # ── 결정(상태 + gold 저장) ──
    note = st.text_input("메모(모호·교정필요 사유 등)", key=f"note_{uid}")
    b1, b2, b3, b4 = st.columns(4)

    def _save(status):
        rec = goldset.build_record(uid, sid, ui, dr, G, house=u.get("house", "APT"),
                                   status=status, curator="admin", notes=note,
                                   verified_at=_time.strftime("%Y-%m-%d %H:%M:%S"))
        goldset.save_record(rec)
        goldset.set_status(uid, status, curator="admin", notes=note,
                           at=_time.strftime("%Y-%m-%d %H:%M:%S"))

    if b1.button("✅ 검증완료(저장)", type="primary"):
        _save("검증완료")
        st.session_state.cur_i = min(len(pool) - 1, st.session_state.cur_i + 1)
        st.rerun()
    if b2.button("🤔 모호(보류)"):
        goldset.set_status(uid, "모호", curator="admin", notes=note,
                           at=_time.strftime("%Y-%m-%d %H:%M:%S"))
        st.rerun()
    if b3.button("🚫 제외"):
        goldset.set_status(uid, "제외", curator="admin", notes=note,
                           at=_time.strftime("%Y-%m-%d %H:%M:%S"))
        st.rerun()
    if b4.button("💾 자동저장(미확정)"):
        _save("자동")
        st.toast("자동추출 레코드 저장(검증 전)")

    existing = goldset.load_record(uid)
    if existing:
        with st.expander(f"▸ 저장된 gold 레코드 보기 ({existing['meta']['status']})"):
            st.json({"meta": existing["meta"],
                     "n_nodes": len(existing["nodes"]),
                     "n_edges": len(existing["edges"]),
                     "node0": existing["nodes"][0] if existing["nodes"] else None})


# ── 사이드바 ──────────────────────────────────────────────────────────────────
st.sidebar.markdown("#### 🏗 Plan2Graph 관리자")
_MENU = ["🧮 종합 현황",
         "🏢 AI-Hub 검수 (T)", "🏠 CubiCasa 검수", "📐 RPLAN 검수",
         "📈 T-라인 결과", "📘 T-라인 도면생성",
         "🧩 AI-Hub 검수 (G)", "📗 G-라인 도면생성",
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
except ModuleNotFoundError:
    which = st.sidebar.radio("메뉴", _MENU, index=0, label_visibility="collapsed")

# ════════════════════════════════════════════════════════════════════════════
# ✏️ 위상 편집(신규) — 원본 위에서 사람이 위상 직접 구축(자동추론 0) → gold
#   기존 자동추출(extract2)·골드(goldset) 미사용. 자체 데이터 소스(독립).
# ════════════════════════════════════════════════════════════════════════════
if which.startswith("🧩"):
    from plan2graph import topoedit
    st.title("🧩 AI-Hub 검수 (G) — 위상+기하 보정")
    _g0m_g = config.release_dir("g0") / "manifest.json"
    _used_g = (json.loads(_g0m_g.read_text(encoding="utf-8")).get("n_units", 0)
               if _g0m_g.exists() else 0)
    _recdir_g = config.DATA_DIR / "staging" / "topo_human" / "records"
    _corr_g = len(list(_recdir_g.glob("*.svg"))) if _recdir_g.exists() else 0
    _cg = st.columns(3)
    _cg[0].metric("사용 (자동 g0)", f"{_used_g:,}")
    _cg[1].metric("보정 (위상편집 SVG)", f"{_corr_g:,}")
    _cg[2].metric("제외", "—")
    st.caption("롤링 증분: **사용** 데이터로 학습→계속 사용→위상편집으로 보정해 늘면 재학습/이어쓰기. "
               "보정 = 아래 편집기에서 도면 골라 위상+기하 직접 교정.")
    st.divider()
    topoedit.render_editor()
    st.stop()

# ════════════════════════════════════════════════════════════════════════════
# 🧮 검수 현황(종합) — AI-Hub·CubiCasa·RPLAN 변환 결과를 한 화면에서 비교
# ════════════════════════════════════════════════════════════════════════════
if which.startswith("🧮"):
    from plan2graph import dataset_status

    st.title("🧮 검수 현황(종합)")
    st.caption("AI-Hub · CubiCasa5k · RPLAN — 처분(✅사용 / 🛠보정·복구필요 / 🚫제외)별, "
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

    # ── 파이프라인 진행 상황(생성 도면) — 단계별 산출물을 실제 파일에서 읽어 상태 표시 ──
    st.markdown("### 🧭 파이프라인 진행 상황 — 자연어 → 위상 → 기하 → 자기교정 → 도면")
    st.caption("각 단계 **실제 산출물 파일**(manifest·run.json·checkpoint)에서 상태를 읽음 — "
               "GUI만 봐도 어디까지 했는지 추적(코드·GUI 일치). ✅완료 · ⬜예정.")
    _ROOTd = Path(__file__).resolve().parent
    _g0m = config.release_dir("g0") / "manifest.json"
    _g0 = json.loads(_g0m.read_text(encoding="utf-8")) if _g0m.exists() else None
    _grp = config.run_dir("geom_g0") / "run.json"
    _gr = json.loads(_grp.read_text(encoding="utf-8")) if _grp.exists() else None
    _ck = (config.run_dir("geom_g0") / "checkpoint.pt").exists()
    _pre = bool(config.iter_runs("geom_g0_pre-*"))
    _stages = [
        ("1. 기하 데이터(g0, 자동)",
         (f"{_g0['n_plans']:,}도면 / {_g0['n_units']:,}세대" if _g0 else "—"),
         "✅" if _g0 else "⬜", "scripts/build_geom.py → releases/g0"),
        ("2. 기하 추출기(2층 스키마)", "geomgraph", "✅", "src/plan2graph/geomgraph.py"),
        ("3. 기하 모델 학습",
         (f"loss {_gr.get('loss', 0):.3f} · {_gr.get('epochs')}ep" if _gr else "미학습"),
         "✅" if _ck else "⬜", "train_geom.py → runs/geom_g0"),
        ("4. 자기교정(겹침0·외곽채움)", "geom_correct", "✅", "src/plan2graph/geom_correct.py"),
        ("5. 도면 생성(GUI)", "도면생성 §기하", "✅", "🏗 도면 생성 메뉴"),
        ("6. 2단계(글로벌 사전학습→g0)", ("학습됨" if _pre else "미적용"),
         "✅" if _pre else "⬜", "train_geom --pretrain g_rplan"),
        ("7. 문·창·복도 생성", "스키마 보유, 생성 예정", "⬜", "GEOMETRY_SCHEMA TODO"),
        ("8. 인접실현↑ / 법규 검증", "treemap 50%대 → 개선 예정", "⬜", "자기교정 루프 고도화"),
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
    _aim_t = config.DATA_DIR / "staging" / "aihub" / "manifest.jsonl"
    if _aim_t.exists():
        @st.cache_data(show_spinner=False)
        def _aih_disp_t(_sz):
            from collections import Counter as _Ctr
            return dict(_Ctr(json.loads(_l)["disposition"]
                             for _l in _aim_t.read_text(encoding="utf-8").splitlines() if _l.strip()))
        _dt = _aih_disp_t(_aim_t.stat().st_size)
        _ct = st.columns(3)
        _ct[0].metric("✅ 사용", f"{_dt.get('use', 0):,}")
        _ct[1].metric("🛠 보정 필요", f"{_dt.get('fix', 0):,}")
        _ct[2].metric("🚫 제외", f"{_dt.get('excl', 0):,}")
        st.caption("변환 완성도 레버(자동): ② 기하룰(개방통로·발코니·문매칭) · ③ 임계(간격·비율) · "
                   "④ 무결성 기준(R1~R5) · ① 검출 재학습(V2V/STR) · 라벨 합집합 재변환 → 보정필요를 사용으로.")
        st.divider()
    st.caption("AI-Hub 도면을 원본 PNG로 확인 — 채택분(dual)·제외분(부분/완전배제) 사유 육안 검증.")
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
        ("use", "dual_dedup_merge"): "✅ 사용 · dual(중복라벨복구)",
        ("use", "v2v_str_recovered"): "✅ 사용 · 방만→V2V STR복구",
        ("use", "v2v_spa_recovered"): "✅ 사용 · 구조만→V2V SPA복구",
        ("fix", "convert_failed"): "🛠 보정필요 · 변환실패(dual)",
        ("fix", "spa_only_pending"): "🛠 복구대상 · 방만(V2V 대기)",
        ("fix", "str_only_pending"): "🛠 복구대상 · 구조만(V2V 대기)",
        ("excl", "nonfp"): "🚫 제외 · 비-FP(평면도 아님)",
        ("excl", "objocr"): "🚫 제외 · OBJ/OCR만",
        ("excl", "duplicate"): "🔁 제외 · 중복(사본)",
    }
    _AIHUB_ORDER = list(AIHUB_LABEL.values())

    def _albl(row):
        return AIHUB_LABEL.get((row["disposition"], row["reason"]),
                               f"{row['disposition']}·{row['reason']}")

    from collections import Counter as _C
    _cnt = _C(_albl(r) for r in rows)
    disp = [(lab, _cnt[lab]) for lab in _AIHUB_ORDER if _cnt.get(lab, 0)]
    with st.expander("ℹ️ 분류 안내 (꼭 읽기)", expanded=False):
        st.markdown(
            f"검수 대상 = 받은 원천 도면 전량 **{len(rows):,}장**(고유+중복사본). "
            "도면 1장 = **처분 1칸**(대표 사유, 상호배타), 모든 칸 합 = 다운로드.\n\n"
            "- **✅ 사용**: dual(SPA+STR) 직접변환 · 방만/구조만은 **V2V로 복구**해 사용\n"
            "- **🛠 보정·복구**: 방만/구조만 V2V 대기 · dual인데 변환 실패(품질게이트)\n"
            "- **🚫 제외(영구)**: 비-FP(단면/입면/구조=평면도 아님) · OBJ/OCR만(방·구조 없음) · "
            "중복(같은 PNG byte-identical, 1장만 채택)")
    keymap = {"📋 전체": len(rows)}
    keymap.update(dict(disp))
    cat = st.sidebar.selectbox("분류", ["📋 전체"] + [lab for lab, _ in disp],
                               format_func=lambda k: f"{k} ({keymap[k]:,})")
    _HOUSE_KO = {"APT": "APT(아파트)", "DEH": "DEH(단독주택)", "ROW": "ROW(연립주택)"}
    house = st.sidebar.selectbox("거주형태", ["(전체)", "APT", "DEH", "ROW"],
                                 format_func=lambda k: _HOUSE_KO.get(k, k))
    sel = [r for r in rows
           if (cat == "📋 전체" or _albl(r) == cat) and (house == "(전체)" or r.get("house") == house)]
    # 렌더 대상: manifest 행의 지문 → 렌더 인덱스(없으면 스킵). 카운트는 manifest(sel)가 권위.
    recs = [ridx[r["fingerprint"]] for r in sel if r["fingerprint"] in ridx]

    st.markdown(f"### {cat} — **{len(sel):,}개**" +
                (f"  ·  _렌더가능 {len(recs):,}_" if len(recs) != len(sel) else ""))
    @st.cache_data(show_spinner="라벨 인덱스 구성(최초 1회)...")
    def _lblidx(sp):
        return _ix.label_index(sp)

    view = st.sidebar.radio(
        "보기 모드", ["그래프검수(원본∥그래프)",
                   "나란히(원본 | 오버레이)", "겹쳐보기", "원본만"],
        index=0, help="그래프검수=구버전 위상 검수 · 나란히/겹쳐/원본만=라벨 육안확인 · "
                      "사람 위상 구축은 좌측 메뉴 🧩 AI-Hub 검수 (G)")
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
# ════════════════════════════════════════════════════════════════════════════
# 📈 결과 대시보드 — 데이터셋 조합별 성능 비교 (이전 구조 복원: 608eeaf)
#   개념: 데이터셋을 합쳐 하나를 학습 → 어떤 조합이 최고인지 비교.
#   데이터버전 매핑: 없음→v0 · +CubiCasa→v2 · +RPLAN→v3 · +결합→v4 (experiments._PRETRAIN_VER).
# ════════════════════════════════════════════════════════════════════════════
if which.startswith("📈"):
    import json as _json
    import random as _random
    from collections import Counter as _Counter
    from plan2graph import model_baseline as _mb
    from plan2graph import review as _rv

    vers = sorted(_v for _v, _line, _rp in config.list_releases()
                  if _line == "tline" and (_rp / "splits" / "test.txt").exists())
    REL = config.RELEASES_DIR              # 최상위 releases 폴더(라인 무관 파일용)
    if not vers:
        st.info("동결된 버전이 없습니다. `python src/plan2graph/release.py v0` 먼저 실행.")
        st.stop()
    st.title("📈 T-라인 결과 — 데이터셋 · 위상모델 성능")
    st.caption("**데이터셋 조합별 성능 비교** — 데이터를 합쳐 하나를 학습했을 때 어떤 조합이 최고인지 한눈에. "
               "test는 AI-Hub 동결분으로 전 버전 공유(비교 기준 고정).")
    ver = vers[-1]

    def _mt(p):
        return p.stat().st_mtime if p.exists() else 0

    @st.cache_data(show_spinner=False)
    def _manifest(v, _k):
        p = config.release_dir(v) / "manifest.json"
        return _json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    @st.cache_data(show_spinner=False)
    def _evalr(v, _k):
        p = config.release_dir(v) / "eval.json"
        return _json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    @st.cache_data(show_spinner="데이터셋 통계 집계...")
    def _roomdist(v, _k):
        c = _Counter()
        for f in (config.release_dir(v) / "graphs").glob("*.json"):
            r = _json.loads(f.read_text(encoding="utf-8"))
            for n in r["layout"]["nodes"]:
                if n.get("type") and n["type"] != "exterior":
                    c[n["type"]] += 1
        return dict(c.most_common())

    @st.cache_resource(show_spinner="모델 학습(통계 생성기)...")
    def _model(v, _k):
        return _mb.fit(_mb._load_split(v, "train"))

    man = _manifest(ver, _mt(config.release_dir(ver) / "manifest.json"))
    ev = _evalr(ver, _mt(config.release_dir(ver) / "eval.json"))

    @st.cache_data(show_spinner="실험 원장 집계...")
    def _agg(_k):
        from plan2graph import experiments
        return experiments.agg_summary()

    _idx = ROOT / "runs" / "index.jsonl"
    summ = _agg(_mt(_idx)) if _idx.exists() else {"eval": [], "generalization": []}
    _v0man = _manifest("v0", _mt(config.release_dir("v0") / "manifest.json"))
    _AIHUB = _v0man.get("n_graphs")
    _ai = f"AI-Hub {_AIHUB:,}" if _AIHUB else "AI-Hub"

    # combine 런만(version=vN & pretrain 없음) — 전이학습 런(pretrain 有)과 분리. ds_version 안 씀.
    def _best_eval(dsv):
        cs = [r for r in summ["eval"] if r.get("version") == dsv
              and r.get("pretrain") in (None, "없음")
              and r["generator"] == "신경망" and r["loop"] == "on"]
        return max(cs, key=lambda r: r["seeds"]) if cs else None

    def _best_unseen(dsv):
        cs = [r for r in summ["generalization"] if r.get("version") == dsv
              and r.get("pretrain") in (None, "없음")
              and r["generator"] == "신경망" and r["subset"] == "unseen"]
        return max(cs, key=lambda r: r["seeds"]) if cs else None

    def _pm(m, s):
        return "—" if m is None else (f"{m:.3f}±{s:.3f}" if s else f"{m:.3f}")

    # ── 1) 데이터셋 — 버전별 학습 데이터 구성(합친 구성) ──
    st.header("1. 데이터셋 — 버전별 학습 데이터 구성")
    st.caption("버전 = 학습에 넣은 **데이터 조합**. 여러 데이터셋을 **합쳐 하나를 한 번에 학습**"
               "(전이학습 아님) → 어떤 조합이 최고 성능인지 비교. 평가는 전 버전 **동일 동결 test**(아래 ⓘ).")
    _AH0 = "3,324도면→7,101세대"
    _AH = "10,063도면→20,828세대"
    st.table([
        {"버전": "v0", "한국 (AI-Hub)": f"{_AH0} (클린·기준)", "글로벌 추가": "—", "상태": "완료"},
        {"버전": "v1", "한국 (AI-Hub)": _AH, "글로벌 추가": "—", "상태": "완료"},
        {"버전": "v2", "한국 (AI-Hub)": _AH, "글로벌 추가": "CubiCasa 3,028", "상태": "완료"},
        {"버전": "v3", "한국 (AI-Hub)": _AH, "글로벌 추가": "RPLAN 80,371", "상태": "완료"},
        {"버전": "v4", "한국 (AI-Hub)": _AH, "글로벌 추가": "CubiCasa 3,028 + RPLAN 80,371", "상태": "완료"},
        {"버전": "v5", "한국 (AI-Hub)": "—", "글로벌 추가": "RPLAN 80,371", "상태": "완료"},
        {"버전": "v6", "한국 (AI-Hub)": "—", "글로벌 추가": "CubiCasa 3,028", "상태": "완료"},
        {"버전": "v7", "한국 (AI-Hub)": "—", "글로벌 추가": "RPLAN 80,371 + CubiCasa 3,028", "상태": "완료"},
    ])
    st.caption("각 버전 = 한국(AI-Hub) + 글로벌 추가를 **합쳐서 한 모델로 학습**. "
               "**AI-Hub는 도면 1장=다세대**라 도면수≠세대수(병기), 글로벌(RPLAN·CubiCasa)은 도면=세대 1:1. "
               "수치는 **세대 그래프** 기준.")
    st.subheader("방 종류 분포 — 출처별 (AI-Hub · RPLAN · CubiCasa)")
    import pandas as _pd
    import altair as _alt
    _rdp = REL / "roomdist_by_source.json"
    if _rdp.exists():
        _rd = _json.loads(_rdp.read_text(encoding="utf-8"))
        _types = sorted({t for d in _rd.values() for t in d},
                        key=lambda t: -sum(d.get(t, 0) for d in _rd.values()))
        # 출처별 **실제** 원 분류명(AI-Hub=한글, RPLAN·CubiCasa=영문). 대표영문(번역) 제거.
        _ORIG = {  # (AI-Hub, RPLAN, CubiCasa)
            "침실": ("침실", "masterroom·childroom·secondroom·guestroom", "Bedroom"),
            "거실": ("거실", "livingroom", "LivingRoom"),
            "주방": ("주방", "kitchen·diningroom", "Kitchen"),
            "화장실": ("화장실", "bathroom", "Bath"),
            "현관": ("현관", "entrance", "Entry·DraughtLobby"),
            "발코니": ("발코니", "balcony", "—"),
            "드레스룸": ("드레스룸", "walkin·storage", "WalkIn·Closet"),
            "다목적공간": ("다목적공간", "studyroom", "Study·Office"),
            "실외기실": ("실외기실", "—", "Laundry·Utility"),
            "기타": ("기타", "—", "Hall·Corridor"),
        }
        _si = {"AI-Hub": 0, "RPLAN": 1, "CubiCasa": 2}

        def _orig(t, s):
            return _ORIG.get(t, (t, t, t))[_si[s]]
        # x축은 한글(통합)만 — 가독성. 출처별 원 라벨은 막대 툴팁(원 라벨)에서.
        _long = _pd.DataFrame([{"방 종류": t, "출처": s, "노드 수": _rd[s].get(t, 0),
                                "원 라벨": _orig(t, s)}
                               for s in _rd for t in _types])
        _ch = _alt.Chart(_long).mark_bar().encode(
            x=_alt.X("방 종류:N", sort=_types, title=None),
            xOffset=_alt.XOffset("출처:N"),
            y=_alt.Y("노드 수:Q", title="노드 수 (양)"),
            color=_alt.Color("출처:N",
                             scale=_alt.Scale(domain=["AI-Hub", "RPLAN", "CubiCasa"],
                                              range=["#4C78A8", "#54A24B", "#E6A817"]),
                             legend=_alt.Legend(title="출처")),
            tooltip=["방 종류", "출처", "원 라벨", _alt.Tooltip("노드 수:Q", format=",")],
        )
        st.altair_chart(_ch, use_container_width=True)
        _tot = " · ".join(f"{s} {sum(d.values()):,}" for s, d in _rd.items())
        st.caption(f"출처별 방 종류 **노드 수(양)** — 막대 길이=실제 개수(방종류별 3색 그룹). "
                   f"x축=통합 한글. **막대에 마우스 올리면 출처별 실제 원 라벨**(AI-Hub 한글 / RPLAN·CubiCasa 영문). "
                   f"총 노드: {_tot}. (`scripts/roomdist_by_source.py` 스냅샷)")
    else:
        st.bar_chart(_roomdist(ver, _mt(config.release_dir(ver) / "manifest.json")))
        st.caption("출처별 분포는 `python scripts/roomdist_by_source.py` 실행 후 표시.")

    with st.expander("▸ 방 종류 — 소스별 원 분류명 매핑 (AI-Hub / RPLAN / CubiCasa → 통합)"):
        st.caption("이종 데이터셋의 서로 다른 방 라벨을 **하나의 통합 온톨로지(한글)**로 변환(`adapters/common.py`). "
                   "차례대로 **AI-Hub / RPLAN / CubiCasa**의 원 분류명.")
        st.table([
            {"통합(한글)": "침실", "AI-Hub": "침실", "RPLAN": "masterroom·childroom·secondroom·guestroom", "CubiCasa": "Bedroom"},
            {"통합(한글)": "거실", "AI-Hub": "거실", "RPLAN": "livingroom", "CubiCasa": "LivingRoom"},
            {"통합(한글)": "주방", "AI-Hub": "주방", "RPLAN": "kitchen·diningroom", "CubiCasa": "Kitchen"},
            {"통합(한글)": "화장실", "AI-Hub": "화장실", "RPLAN": "bathroom", "CubiCasa": "Bath(room)"},
            {"통합(한글)": "현관", "AI-Hub": "현관", "RPLAN": "entrance", "CubiCasa": "Entry·DraughtLobby"},
            {"통합(한글)": "발코니", "AI-Hub": "발코니", "RPLAN": "balcony", "CubiCasa": "— (없음)"},
            {"통합(한글)": "드레스룸", "AI-Hub": "드레스룸", "RPLAN": "walkin·storage", "CubiCasa": "WalkIn·Closet·Storage"},
            {"통합(한글)": "다목적공간", "AI-Hub": "다목적공간", "RPLAN": "studyroom", "CubiCasa": "Study·Office·Den"},
            {"통합(한글)": "실외기실", "AI-Hub": "실외기실", "RPLAN": "— (없음)", "CubiCasa": "Laundry·Utility"},
            {"통합(한글)": "기타", "AI-Hub": "기타", "RPLAN": "— (외부·벽 제외)", "CubiCasa": "Hall·Corridor"},
        ])
        st.caption("⚠️ **RPLAN은 안방(masterroom)·자녀방·손님방을 따로 분류**하지만 통합 시 모두 '침실'로 합쳐짐 → "
                   "역할(role) 정보 소실(§5-1 한계와 직결). **발코니·실외기실은 글로벌에 거의 없음** → 한국 고유(도메인 격차).")

    # ── 2) 학습·생성 설정 + 목표치 ──
    st.header("2. 학습·생성 설정 + 목표치")
    st.caption("아래 설정으로 각 버전을 학습하고, **목표치(실제 도면의 인접분포)에 맞춰 생성** → §3에서 성능 평가. "
               "정확한 버전·시드별 값은 `runs/<run_id>/meta.json`.")
    st.markdown("**① 학습·생성 설정**")
    st.table([
        {"항목": "모델", "값": "set-transformer link-predictor"},
        {"항목": "모델 크기", "값": "embedding 48 · layers 2 · heads 4 · FFN 96"},
        {"항목": "학습 방식", "값": "combine (데이터 합쳐 한 번 학습)"},
        {"항목": "optimizer", "값": "Adam · lr 1e-3"},
        {"항목": "epochs", "값": "100"},
        {"항목": "batch size", "값": "64"},
        {"항목": "seeds", "값": "42 · 1 · 2 · 3 · 4 (5회 → 평균±표준편차)"},
        {"항목": "평가 test", "값": "균형 동결 (APT/DEH/ROW 300도면)"},
        {"항목": "규제루프", "값": "off / on"},
    ])
    st.caption("**파라미터 설정 근거**: 위 기본값(embedding 48·layers 2·heads 4·FFN 96)으로 **전 버전(v0~v7) 통일** "
               "(데이터 조합 효과만 분리). epochs·파라미터 모두 성능에 영향을 주므로, 별도로 **모델 용량 2배 "
               "ablation**(embedding 96·layers 4·heads 8·FFN 192, 같은 v0 데이터)을 수행 → **§3 `v0cap2x` 행.** "
               "**결과: 2배 모델이 매크로 0.188→0.166 개선**(특히 소수형태 ROW 0.233→0.191) → 기본 모델이 "
               "소수형태엔 용량 부족이었음(데이터뿐 아니라 모델 용량도 레버).")

    @st.cache_data(show_spinner="목표치(인접분포) 집계...")
    def _adj_target(v, _k):   # 실제 도면의 방-쌍 연결 빈도 → 확률(eval_gen._metrics의 P_real과 동일 정의)
        pairs = _Counter()
        for f in (config.release_dir(v) / "graphs").glob("*.json"):
            r = _json.loads(f.read_text(encoding="utf-8"))
            nt = {n["id"]: n["type"] for n in r["layout"]["nodes"] if isinstance(n["id"], int)}
            for e in r["layout"]["edges"]:
                if (e.get("via") in _mb.CONNECT_VIAS and isinstance(e.get("source"), int)
                        and isinstance(e.get("target"), int)):
                    a, b = nt.get(e["source"]), nt.get(e["target"])
                    if a and b:
                        pairs[tuple(sorted((a, b)))] += 1
        tot = sum(pairs.values()) or 1
        return [(f"{a}–{b}", c / tot) for (a, b), c in pairs.most_common(15)]

    st.markdown("**② 목표치 — 실제 도면의 방-쌍 연결 확률 P_real** (AI-Hub v0, 통행연결 상위 15쌍)")
    _adj = _adj_target("v0", _mt(config.release_dir("v0") / "manifest.json")) if (config.release_dir("v0") / "graphs").exists() else []
    if _adj:
        st.table([{"방-쌍": k, "목표 연결확률": f"{v*100:.1f}%"} for k, v in _adj])
        st.caption("생성모델이 맞춰야 할 목표(= §3 adj_L1의 P_real). 거실이 상위에 반복 → 한국 집은 거실 허브 구조.")
    else:
        st.caption("(v0 그래프 없음)")

    # ── 3) 생성 성능 — 버전별 핵심 ──
    st.header("3. 생성 성능 — 버전별 핵심 (동결 test, 규제루프 on)")
    st.info("ⓘ **평가 test = AI-Hub 균형 동결분(소버린 기준).** 주거형태 **APT·DEH·ROW를 골고루**"
            "(각 100도면 = 300도면 → 465세대그래프) 고정 → **전 버전이 이 동일 test로 평가**(조합 비교 타당성). "
            "글로벌만(v5~v7)이 낮게 나오는 건 정상 — 글로벌↔국내 도메인 격차를 보는 게 목적.")
    with st.expander("ⓘ 평가지표 설명 (범례)"):
        st.markdown(
            "- **인접L1 (adj_L1)** ↓ — 생성 도면의 *방-쌍 인접(연결) 분포*가 실제 도면과 얼마나 "
            "다른지(L1 거리). **낮을수록** 실제 배치에 가까움 = 좋음. 핵심 변별 지표.\n"
            "- **인접L1(unseen)** ↓ — 학습에서 **못 본 방 조합**(program)에 대한 인접L1 = 진짜 일반화력.\n"
            "- **무결성** ↑ — 생성 위상이 구조 규칙(R1~R5: 연결성·현관 등)을 통과한 비율.\n"
            "- **법규** ↑ — 채광 등 건축법규 통과율(규제루프 on이 위반을 자동보정 → 100%).\n"
            "- **다양성** ↑ — 같은 입력에 서로 다른 그래프를 내는 비율(고유 결과 / 전체).\n"
            "- **신규성** ↑ — 학습셋에 없던 새 구조를 생성한 비율.\n\n"
            "헤드라인은 **인접L1**(충실도) — 무결성·법규는 대개 100%라 변별이 약하고, 인접L1이 조합 간 차이를 드러냄.")
    st.caption("같은 동결 test에서 데이터버전별 신경망 성능 + v0 규칙기반(알고리즘) 기준선. "
               "여러 시드 평균. 전체 매트릭스는 §5.")
    rows3 = []
    base = next((r for r in summ["eval"] if r.get("ds_version") == "v0"
                 and r["generator"] == "규칙기반" and r["loop"] == "on"), None)
    base_u = next((r for r in summ["generalization"] if r.get("ds_version") == "v0"
                   and r["generator"] == "규칙기반" and r["subset"] == "unseen"), None)
    if base:
        rows3.append({"버전": "v0", "생성기": "규칙기반(알고리즘)",
                      "인접L1↓(전체)": _pm(base["adj_L1_mean"], base["adj_L1_std"]),
                      "인접L1↓(unseen)": _pm(base_u["adj_L1_mean"], base_u["adj_L1_std"]) if base_u else "—",
                      "무결성": f"{(base['integrity'] or 0)*100:.0f}%",
                      "법규": f"{(base['legal'] or 0)*100:.0f}%",
                      "다양성": f"{(base['diversity'] or 0)*100:.0f}%",
                      "신규성": f"{(base['novelty'] or 0)*100:.0f}%"})
    for dsv in ["v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7",
                "v0cap2x", "v1cap2x", "v4cap2x", "v7cap2x"]:
        e, u = _best_eval(dsv), _best_unseen(dsv)
        if e:
            rows3.append({"버전": dsv, "생성기": "신경망",
                          "인접L1↓(전체)": _pm(e["adj_L1_mean"], e["adj_L1_std"]),
                          "인접L1↓(unseen)": _pm(u["adj_L1_mean"], u["adj_L1_std"]) if u else "—",
                          "무결성": f"{(e['integrity'] or 0)*100:.0f}%",
                          "법규": f"{(e['legal'] or 0)*100:.0f}%",
                          "다양성": f"{(e['diversity'] or 0)*100:.0f}%",
                          "신규성": f"{(e['novelty'] or 0)*100:.0f}%"})
    if rows3:
        st.table(rows3)
    else:
        st.info("성능 데이터 없음: `bash scripts/run_matrix.sh` 후 표시됩니다.")

    # ── 4) 실제 vs 생성 예시 (규칙기반 생성기) ──
    st.header("4. 실제 도면 vs AI 생성 (같은 program · 규칙기반 예시)")
    if "ex_seed_dash" not in st.session_state:
        st.session_state.ex_seed_dash = 1
    if st.button("🎲 다른 예시", key="dash_ex"):
        st.session_state.ex_seed_dash += 1
    _test = _mb._load_split(ver, "test")
    if _test:
        rec = _test[st.session_state.ex_seed_dash % len(_test)]
        program = dict(_Counter(n["type"] for n in rec["layout"]["nodes"]
                                if isinstance(n["id"], int)))
        st.caption(f"program: {program}")
        gen = _mb.generate(_model(ver, _mt(config.release_dir(ver) / "manifest.json")), program,
                           _random.Random(st.session_state.ex_seed_dash))
        e1, e2 = st.columns(2)
        with e1:
            st.markdown("**실제 (데이터셋)**")
            st.pyplot(_rv.render_graph_fig(_rv.record_to_graph(rec),
                      title="real", node_size=1500, font_size=11, layout="kamada"),
                      use_container_width=True)
        with e2:
            st.markdown("**AI 생성 (같은 program)**")
            st.pyplot(_rv.render_graph_fig(gen, title="generated",
                      node_size=1500, font_size=11, layout="kamada"),
                      use_container_width=True)

    # ── 5) A/B 비교 — 데이터 조합 × 생성기 × 규제루프 (combine 런만) ──
    st.header("5. A/B 비교 — 데이터 조합 × 생성기 × 규제루프")
    st.caption("데이터 조합(v0~v7)을 생성기(규칙기반 vs 신경망)·규제루프(on/off)로 같은 동결 균형 test에서 비교. "
               "시드 평균±표준편차. **combine 런만**(전이학습 런 제외). 단일 소스 = runs/index.jsonl.")

    def _gen_label(r):
        return f"{r['generator']}({r['arch']})" if r.get("arch") else r["generator"]

    def _cmb(r):   # combine 런 + 기준선만(pretrain 없음) — 전이학습(pretrain 有) 제외
        return r.get("pretrain") in (None, "없음")

    def _sort_key(r):
        return (r.get("version", "z"), 0 if r["generator"] == "규칙기반" else 1,
                str(r.get("loop", "")))

    _ev5 = [r for r in summ["eval"] if _cmb(r)]
    _gn5 = [r for r in summ["generalization"] if _cmb(r)]
    if _ev5:
        st.subheader("전체 test")
        st.table([{"버전": r["version"], "생성기": _gen_label(r),
                   "규제루프": r["loop"], "시드": r["seeds"],
                   "인접L1↓": _pm(r["adj_L1_mean"], r["adj_L1_std"]),
                   "무결성": f"{(r['integrity'] or 0)*100:.0f}%",
                   "법규": f"{(r['legal'] or 0)*100:.0f}%",
                   "다양성": f"{(r['diversity'] or 0)*100:.0f}%",
                   "신규성": f"{(r['novelty'] or 0)*100:.0f}%"}
                  for r in sorted(_ev5, key=_sort_key)])
        st.caption("인접L1↓: 낮을수록 실제 배치에 가까움 · 무결성=위상 R1~R5 · "
                   "법규=채광 등 통과(규제루프 on이 위반 자동보정).")
    if _gn5:
        st.subheader("일반화 — seen / unseen program")
        st.caption("처음 보는 방 구성(unseen)에서의 사실성 — 데이터 조합 효과가 가장 드러나는 축.")
        st.table([{"버전": r["version"], "생성기": _gen_label(r),
                   "subset": r["subset"], "시드": r["seeds"],
                   "인접L1↓": _pm(r["adj_L1_mean"], r["adj_L1_std"])}
                  for r in sorted(_gn5, key=_sort_key)])

    # ── 6) 요약 (결론 + 연구 한계) ──
    st.header("6. 요약")
    st.markdown(
        "**핵심 결론 — 한국형 소버린 도면 생성에서는 데이터의 *출처·양*보다 "
        "*균형 잡힌 클린 한국 데이터*가 결정적이다.**\n\n"
        "1. **소버린(한국) 데이터는 필수다.** 글로벌만 학습(v5~v7)은 국내 균형 test에서 "
        "매크로 adj_L1 0.50~0.76으로, 국내 포함(~0.19) 대비 처참했다. 글로벌 평면도는 "
        "한국 고유 공간(발코니·실외기실·현관)이 희박해(§1 방분포) 국내 도면의 인접 구조를 "
        "재현하지 못한다 — **도메인 격차**.\n"
        "2. **글로벌을 더해도 (한국 타깃 기준) 도움이 안 된다.** 클린 AI-Hub(v0, 마이크로 0.088)가 "
        "최선이며, CubiCasa·RPLAN을 합치거나(v2~v4) AI-Hub 양을 V2V로 늘려도(v1) 개선되지 않거나 "
        "오히려 악화됐다(노이즈). 전이학습으로 바꿔도 결론은 같다. → *계획서의 \"글로벌 표준 "
        "데이터 사전학습이 유리\"라는 가정은 (한국 동결 test에서) 반증.* "
        "**단 주의**: 평가가 한국 test라 글로벌이 불리한 건 부분적으로 설계상 당연(소버린 목표). "
        "여기서 비자명한 건 *글로벌-only 실패*(예상된 OOD)가 아니라 **글로벌+한국 ≤ 한국 단독**"
        "(추가가 무해하지 않고 오히려 약간 해로움)이다. '글로벌이 보편적으로 무용'은 아님(아래 한계 6).\n"
        "3. **모델 용량은 소수형태 개선의 레버다.** 같은 v0 데이터에 모델을 2배로 키우자"
        "(v0cap2x) 매크로가 0.188→0.166으로 개선됐고, 특히 과소대표 주거형태(ROW)에서 가장 "
        "컸다. 데이터 조합이 아니라 **모델 용량**이 소수형태 충실도를 끌어올린다.\n"
        "4. **규제 루프(Symbolic)가 준법을 보장한다.** 글로벌을 섞으면 생성 도면의 사전(off) "
        "법규 통과율이 0%까지 떨어지나(글로벌엔 한국 채광법 패턴이 없음), 규제 루프(on)가 전부 "
        "100%로 복구한다 — Neuro-Symbolic의 Symbolic 기여.\n\n"
        "**종합:** 최적 구성은 *클린 한국(AI-Hub) 데이터 + 충분한 모델 용량 + 규제 루프*이며, "
        "무분별한 글로벌 데이터 증강은 오히려 해가 된다.")
    st.markdown(
        "#### 연구의 한계\n"
        "1. **역할(role) 위상 미반영.** 방 타입이 역할을 구분하지 않는다(침실=안방/손님방, "
        "화장실=부부/공용 미구분). 원인은 소스별로 다르다 — **AI-Hub는 애초에 역할을 주석하지 "
        "않아 복원 불가**, RPLAN은 구분(masterroom 등)이 있었으나 통합 온톨로지에서 소실. "
        "따라서 adj_L1은 역할 오배치(안방에 공용화장실 등)를 잡지 못해 현 수치가 역할 수준에서 "
        "관대할 수 있다.\n"
        "2. **문화 의존성.** 역할·동선 위상은 한국/유럽/중국/일본이 다르나, 본 연구는 "
        "한국(AI-Hub) 한정이며 문화별 위상을 모델링하지 않았다.\n"
        "3. **위상까지만 생성.** 본 단계는 방-관계 그래프(위상)를 생성하며 좌표·치수의 기하 "
        "도면은 다음 단계다.\n"
        "4. **소수형태 데이터 빈곤.** DEH·ROW의 학습 도면이 적어 유형별 성능 상한을 확정하지 "
        "못했다.\n"
        "5. **단일 변별 지표.** 무결성·다양성·신규성은 거의 포화(가드레일)라, 버전 간 변별은 "
        "사실상 adj_L1 하나에 의존한다.\n"
        "6. **평가가 한국 test 한정 → 글로벌 평가의 편향.** test가 AI-Hub(한국)뿐이라 글로벌이 "
        "불리한 건 부분적으로 설계상 당연하다(소버린 목표엔 타당하나, 글로벌 데이터의 가치를 "
        "공정히 재려면 글로벌·교차도메인 test가 필요). 또한 한국 데이터가 충분(7천~2만)한 "
        "고자원 상황이라 전이의 이득이 작을 수 있다 — *저자원이라면 글로벌 사전학습이 유효할 여지*. "
        "즉 '글로벌 무익'은 **한국 타깃·고자원 한정 결론**이다.")
    st.stop()


# ════════════════════════════════════════════════════════════════════════════
# 🏗 도면 생성 (시연) — 자연어 → 위상 도면 + 자기교정 근거
# ════════════════════════════════════════════════════════════════════════════
if which.startswith("📘"):
    from pathlib import Path as _Path
    from plan2graph import review as _rv
    _ROOT = _Path(__file__).resolve().parent
    st.title("📘 T-라인 도면생성 — 자연어→위상→treemap")
    st.caption("위상 모델(부품)을 골라 → 좌표 도면을 만들고 → 검사·재생성으로 무결화. "
               "**최종 목표는 *잘 나온 도면***. 어떤 방법 조합이 최고 도면을 만드는지 탐색한다.")

    # ── 1) 파이프라인 — 지금 어디까지 ──
    st.header("1. 파이프라인 — 지금 어디까지")
    st.markdown(
        "자연어 → 제약(program) → **위상 생성**(✅ 구현·평가) → **Symbolic 자기교정**(✅ 위상) "
        "→ **좌표 도면(geometry)**(✅ 규칙기반 1세대 · 고급방법 로드맵) → 무결 도면.\n\n"
        "위상까지는 완성·평가됐다(→ 📈 결과 대시보드). **여기서는 그 위상으로 실제 좌표 도면까지 그려 보고**, "
        "더 나은 *좌표 도면 생성 방법*을 탐색한다. (위상 모델은 끝이 아니라 **부품**)")

    # ── 2) 최종 모델 구성 — 사전학습 × 파인튜닝 (데이터셋 자유 조합) ──
    st.header("2. 최종 모델 구성 — 사전학습 × 파인튜닝")
    st.caption("**① 사전학습 → ② 파인튜닝 → ③ 기법**을 고른다. 그 조합의 모델이 있으면 **선정**, "
               "없으면 **백그라운드 2단계 학습**(끝나면 ③에서 생성). ①·② 라벨은 §1 데이터셋 테이블과 동일.")
    _VL = {  # 버전 → 데이터 구성(§1 테이블과 동일)
        "v0": "3,324도면→7,101세대 (클린·기준)",
        "v1": "10,063도면→20,828세대",
        "v2": "10,063도면→20,828세대+CubiCasa 3,028",
        "v3": "10,063도면→20,828세대+RPLAN 80,371",
        "v4": "10,063도면→20,828세대+CubiCasa 3,028+RPLAN 80,371",
        "v5": "RPLAN 80,371",
        "v6": "CubiCasa 3,028",
        "v7": "RPLAN 80,371+CubiCasa 3,028",
    }
    _OPTS = ["없음", *_VL]

    def _fmt(o):
        return o if o == "없음" else f"{o}:{_VL[o]}"
    _cf1, _cf2, _cf3 = st.columns(3)
    _pre = _cf1.selectbox("① 사전학습", _OPTS, index=0, format_func=_fmt, key="cfg_pre")
    _ft = _cf2.selectbox("② 파인튜닝", _OPTS, index=1, format_func=_fmt, key="cfg_ft")
    _adv = _cf3.selectbox("③ 기법", ["표준", "용량 2배"], index=0, key="cfg_adv")

    from plan2graph import experiments as _exp
    _ARCH = "set-transformer-v2"
    _cap = _adv == "용량 2배"
    _prv = None if _pre == "없음" else _pre
    _ftv = None if _ft == "없음" else _ft
    # 정규화: 한쪽만 고르면 그 데이터로 단일학습, 둘 다면 2단계(사전학습→파인튜닝)
    if _prv and _ftv:
        _version, _ptr = _ftv, _prv
    elif _ftv:
        _version, _ptr = _ftv, None
    elif _prv:
        _version, _ptr = _prv, None
    else:
        _version, _ptr = None, None
    _ridver = (f"{_version}cap2x" if _cap else _version) if _version else None
    _rid = _exp.make_run_id("neural", _ridver, _ptr, 42, _ARCH) if _ridver else None
    _desc = f"사전학습[{_prv or '없음'}] → 파인튜닝[{_ftv or '없음'}]" + (" · 용량2배" if _cap else "")
    _ckpt = (config.run_dir(_rid) / "checkpoint.pt") if _rid else None
    _exists = bool(_ckpt and _ckpt.exists())
    _olog = (_ROOT / "logs" / f"ondemand-{_rid}.log") if _rid else None
    if _version is None:
        st.warning("①·② 중 최소 하나는 데이터셋을 고르세요. (둘 다 '없음'이면 학습할 데이터가 없음)")
    else:
        st.markdown(f"**→ 최종 모델: {_desc}**  ·  {'✅ 선정(학습됨)' if _exists else '⚠️ 미학습'}  "
                    f"<span style='color:gray;font-size:0.85em'>{_rid}</span>",
                    unsafe_allow_html=True)
    if _version is not None and not _exists:
        if _cap:
            st.info("용량2배는 즉석 학습 미지원 — 기존 용량2배 모델이 있을 때만 선정됩니다. "
                    "(표준으로 바꾸면 즉석 학습 가능)")
        elif _olog.exists():
            _ll = _olog.read_text(errors="ignore").strip().splitlines()
            st.warning("🔄 학습 진행 중 (GPU1·백그라운드) — 완료되면 ✅로 바뀌고 ③에서 생성. "
                       f"최근: `{(_ll[-1] if _ll else '학습 시작/사전학습 단계…')[:130]}`")
            _r1, _r2 = st.columns([1, 1])
            if _r1.button("🔄 상태 새로고침"):
                st.rerun()
            if _r2.button("↻ 다시 시작(로그 초기화)", help="크래시/중단 시 — 로그 지우고 다시 학습"):
                _olog.unlink(missing_ok=True)
                st.rerun()
        else:
            st.info("이 조합의 모델이 없습니다 → 즉석 학습으로 만들 수 있습니다.")
            if st.button("🛠 이 조합 학습 시작 (GPU1 · 백그라운드)"):
                import subprocess as _sp
                import sys as _sys
                _pa = (f"--pretrain {_ptr} --pretrain-epochs 50 " if _ptr else "")
                _cmd = (f"cd '{_ROOT}' && CUDA_VISIBLE_DEVICES=1 PYTHONPATH=src setsid nohup "
                        f"{_sys.executable} -u -m plan2graph.train_gen train "
                        f"{_pa}--finetune {_version} "
                        f"--epochs 100 --seed 42 > 'logs/ondemand-{_rid}.log' 2>&1 &")
                _sp.Popen(["bash", "-lc", _cmd])
                st.success("학습 시작됨 — 데이터량에 따라 수 분~수십 분. "
                           "'상태 새로고침'으로 진행 확인, 완료되면 ③에서 생성.")
                st.rerun()

    # ── 3) Neuro-Symbolic 도면 생성 ──
    st.header("3. Neuro-Symbolic 도면 생성")
    st.caption("위 최종 모델로: 자연어 → 제약 → **위상 생성(Neuro)** → **규제 자기교정(Symbolic)** "
               "→ **좌표 도면(geometry)**. 패널2 위상 OFF∥ON · 패널3 실제 도면 · 패널4 근거.")
    if not _exists:
        st.info("이 조합은 아직 학습되지 않았습니다 → **§2에서 [이 조합 학습 시작]**으로 만든 뒤 생성하세요.")
    else:
        gtext = st.text_input("자연어 요구", "신혼부부 아파트 침실2 욕실1 거실 주방", key="ns_text")
        _g2, _g3 = st.columns(2)
        gseed = _g2.number_input("시드", 0, 9999, 1, key="ns_seed")
        ght = _g3.selectbox("주거형태", ["자동", "APT", "DEH", "ROW"], key="ns_house",
                            help="type조건 모델일 때만 적용")
        if st.button("🏗 생성 (Neuro-Symbolic)"):
            from plan2graph import text2graph as _t2g, gen_loop as _gl

            @st.cache_resource(show_spinner="생성기 로드(CPU)...")
            def _load_ng(_path):
                from plan2graph import generators as _G
                return _G.load(_path)   # arch 디스패치

            try:
                prog = _t2g.parse(gtext)["program"]
                st.caption(f"제약 program: {prog}")
                ng = _load_ng(str(_ckpt))
                _typed = getattr(ng, "arch", "") == "set-transformer-typed"
                st.caption("주거형태조건: "
                           f"{ght if (_typed and ght != '자동') else '미적용(비-typed 또는 자동)'}")

                def gfn(p, r):
                    return (ng.generate(p, r, house_type=ght)
                            if (_typed and ght != "자동") else ng.generate(p, r))
                _sd = int(gseed)
                G_off, _ = _gl.generate_compliant(gfn, prog, max_tries=1, repair=False, seed=_sd)
                v_off = _gl.verify(G_off)
                G_on, hist = _gl.generate_compliant(gfn, prog, max_tries=5, repair=True, seed=_sd)
                v_on = _gl.verify(G_on)
                st.markdown("**패널2 — 자기교정 OFF ∥ ON (같은 입력)**")
                p1, p2 = st.columns(2)
                with p1:
                    st.markdown(f"자기교정 **OFF**(Neuro만) — 위반 {v_off['n']} "
                                f"{'✅' if v_off['passed'] else '❌'}")
                    st.pyplot(_rv.render_graph_fig(G_off, title="loop off", node_size=1500,
                              font_size=11, layout="kamada"), use_container_width=True)
                with p2:
                    st.markdown(f"자기교정 **ON**(+Symbolic) — 위반 {v_on['n']} "
                                f"{'✅통과' if v_on['passed'] else '❌'}")
                    st.pyplot(_rv.render_graph_fig(G_on, title="loop on", node_size=1500,
                              font_size=11, layout="kamada"), use_container_width=True)
                st.markdown("**패널3 — 실제 도면 (geometry · 규칙기반 1세대) — 같은 위상 → 좌표 배치**")
                from plan2graph import floorgeom as _fg
                d1, d2 = st.columns(2)
                with d1:
                    st.pyplot(_fg.render_floorplan_fig(G_off, title="loop off"),
                              use_container_width=True)
                with d2:
                    st.pyplot(_fg.render_floorplan_fig(G_on, title="loop on"),
                              use_container_width=True)
                st.caption("방=유형 색사각형 · 검은테=벽 · 흰 틈=문(위상 엣지+경계공유) · 외벽 현관/창. "
                           "면적은 유형별 표준비(실측 scale 확보 전 근사). "
                           "좌표회귀·diffusion·RL은 §5 로드맵.")
                _st_on = _fg.layout_stats(G_on)
                _m1, _m2, _m3 = st.columns(3)
                _m1.metric("방 수", _st_on["rooms"])
                _m2.metric("위상 인접 실현율", f"{_st_on['adj_rate'] * 100:.0f}%",
                           help="위상에서 연결된 방쌍 중 실제 도면에서 벽을 공유(문 연결)한 비율 "
                                "— 1세대 배치가 위상을 얼마나 지켰는지(도면 품질 지표)")
                _m3.metric("실현된 인접(문)", f"{_st_on['adj_realized']} / {_st_on['adj_total']}")
                st.caption("**도면 품질(§5 기준 일부)** — 인접 실현율: 위상의 방 연결이 좌표 도면에서 "
                           "실제 인접(문)으로 구현된 비율. 1세대(treemap) 한계로 일부 인접은 비실현 "
                           "→ 좌표회귀·RL이 올릴 여지(§5 로드맵).")
                st.markdown("**패널4 — Symbolic 근거 (자기교정 로그)**")
                st.table([{"시도": h["attempt"], "위반(전)": h["violations_before"],
                           "수정": ", ".join(h["fixes"]) or "—",
                           "위반(후)": h["violations_after"],
                           "통과": "✅" if h["passed"] else ""} for h in hist])
                if v_on["violations"]:
                    st.write("잔여 위반(규칙 근거):",
                             [{"종류": x["kind"], "근거": x.get("rule") or x.get("reason") or str(x)}
                              for x in v_on["violations"]])
                else:
                    st.success("최종 위상 무결 — 무결성·법규 통과.")
            except Exception as e:  # noqa: BLE001
                st.error(f"생성 실패: {e}")

    # ── 4) 도면(geometry) 방법 — 위상→좌표 (로드맵) ──
    st.header("4. 도면(geometry) 방법 — 위상→좌표 (로드맵)")
    st.caption("위상 → 좌표 도면을 만드는 방법 후보. {위상 모델} × {도면 방법} × {정제 루프} 중 *최고 도면* 탐색.")
    st.table([
        {"방법": "규칙기반 기하 배치", "설명": "위상에 좌표·벽 채움(squarified treemap·면적비)",
         "상태": "✅ 구현 — §3에서 시연(1세대)"},
        {"방법": "좌표 회귀 GNN", "설명": "위상+제약 → 좌표 직접 예측", "상태": "예정"},
        {"방법": "Layout diffusion", "설명": "위상 조건 생성형 도면", "상태": "예정"},
        {"방법": "Constrained RL", "설명": "면적·법규·동선 보상 최적화", "상태": "예정(원설계 Phase-3)"},
        {"방법": "Self-Correction 루프", "설명": "그린다→검사→다시 그린다(위상 규제루프의 geometry 확장)",
         "상태": "위상 적용 / geometry 예정"},
    ])

    # ── 5) 최종 도면 평가 — '잘 나온 도면'의 기준 ──
    st.header("5. 최종 도면 평가 — '잘 나온 도면'의 기준")
    st.markdown(
        "**최종 도면 품질**로 {위상 모델 × 도면 방법 × 정제 루프} 조합을 판정한다:\n"
        "- **위상 인접 실현율** — 위상 연결이 도면에서 실제 인접(문)으로 구현된 비율 "
        "(✅ §3에서 측정 중)\n"
        "- **면적 정확도** — 생성 면적 vs 요구·실제 (실측 scale 확보 후)\n"
        "- **법규 준수** — 채광·면적비 등 geometry 규제 검증 (위상 규제루프의 좌표 확장)\n"
        "- **동선·기능성** — 현관→거실→방 경로, 방 비율의 합리성\n"
        "- **시각 품질** — 실제 도면과의 유사도 / 전문가 평가\n\n"
        "현재 1세대(treemap)는 *인접 실현율*만 측정. 나머지 지표는 좌표 정밀화(§4 고급 방법)와 "
        "실측 scale 확보 후 활성화 → 이 기준에서 최고인 조합을 찾는 것이 목표.")

    # ── 6) 요약 ──
    st.header("6. 요약")
    st.markdown(
        "위상은 **부품**, 목표는 **잘 나온 도면**이다. 현재 *자연어 → 위상 생성 → 자기교정 → "
        "좌표 도면(1세대)*까지 한 화면에서 동작한다. 모델은 §2에서 *사전학습 × 파인튜닝*으로 "
        "자유 조합(없으면 즉석 학습)하고, §3에서 그 모델로 위상·도면을 만든다. "
        "다음은 *좌표 도면 방법 고도화(§4) → 최종 도면 품질 평가(§5)*다. "
        "위상에서 검증된 원리(**클린 한국 데이터 · 충분한 모델 용량 · 규제 루프**)를 도면으로 잇는다.")
    st.stop()
# ════════════════════════════════════════════════════════════════════════════
# 📜 법령 DB — 최신화(관리자 클릭) + 법령/규정 조회
# ════════════════════════════════════════════════════════════════════════════
if which.startswith("📗"):
    from pathlib import Path as _Path
    _ROOT = _Path(__file__).resolve().parent
    st.title("📗 G-라인 도면생성 — 자연어→위상→학습 기하모델")
    st.caption("NL → 방 구성 → **g0 실측 면적** → **자기교정(겹침0·외곽채움)** → 도면. "
               "위상 모델 없이도 동작(관례 인접=거실 허브). 본 파이프라인: 위상→기하→검증→자기교정.")

    # 기하 모델 학습 — G-라인 데이터셋을 순서대로(사전학습→파인튜닝) 또는 기존 모델 사용
    st.markdown("**기하 모델 학습** — 데이터셋을 순서대로(① 사전학습 → ② 파인튜닝) 또는 기존 모델 사용")
    _gvers = []  # 기하(G-라인) 데이터셋
    for _v, _line, _rp in config.list_releases():
        if _line != "gline":
            continue
        try:
            _m = json.loads((_rp / "manifest.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        _gvers.append((_m.get("version", _v),
                       _m.get("n_units"), ",".join(_m.get("houses", []))))
    if not _gvers:
        st.info("기하 데이터셋(G-라인)이 아직 없습니다. (scripts/build_geom.py / build_geom_global.py 로 빌드)")
    else:
        _gmap = {v: f"{v} ({u:,}세대·{h})" if u else v for v, u, h in _gvers}
        _gnames = [v for v, _, _ in _gvers]
        _gp1, _gp2, _gp3 = st.columns(3)
        _pre_opts = ["없음", *_gnames]
        _pidx = _pre_opts.index("g_global") if "g_global" in _pre_opts else 0
        _fidx = _gnames.index("g0") if "g0" in _gnames else 0
        _gpre = _gp1.selectbox("① 사전학습", _pre_opts, index=_pidx,
                               format_func=lambda o: o if o == "없음" else _gmap[o], key="geom_pre")
        _gft = _gp2.selectbox("② 파인튜닝", _gnames, index=_fidx,
                              format_func=lambda o: _gmap[o], key="geom_ft")
        _gep = _gp3.number_input("에폭", 1, 200, 20, key="geom_ep")
        _gprv = None if _gpre == "없음" else _gpre
        _grid = f"geom_{_gft}" + (f"_pre-{_gprv}" if _gprv else "")
        _grun = config.run_dir(_grid) / "run.json"
        _glog = _ROOT / "logs" / f"train_{_grid}.log"
        st.caption(f"→ 모델: `{_grid}`")
        if _grun.exists():
            _gj = json.loads(_grun.read_text(encoding="utf-8"))
            st.success(f"✅ 학습됨 — {_grid} (loss {_gj.get('loss', 0):.3f})")
            if st.button("↻ 다시 학습", key="geom_tr_re"):
                _glog.unlink(missing_ok=True)
                _grun.unlink(missing_ok=True)
                st.rerun()
        elif _glog.exists():
            _ll = _glog.read_text(errors="ignore").strip().splitlines()
            st.warning(f"🔄 학습 중(GPU1·백그라운드) — 최근: `{(_ll[-1] if _ll else '시작…')[:120]}`")
            if st.button("🔄 상태 새로고침", key="geom_tr_ref"):
                st.rerun()
        else:
            if st.button("🛠 기하 학습 시작 (GPU1·백그라운드)", key="geom_tr_go"):
                import subprocess as _sp
                _pa = f"--pretrain {_gprv} " if _gprv else ""
                _cmd = (f"cd '{_ROOT}' && CUDA_VISIBLE_DEVICES=1 PYTHONPATH=src setsid nohup "
                        f"{sys.executable} -u -m plan2graph.train_geom "
                        f"{_pa}--finetune {_gft} --epochs {int(_gep)} "
                        f"> 'logs/train_{_grid}.log' 2>&1 &")
                _sp.Popen(["bash", "-lc", _cmd])
                st.success(f"학습 시작됨 — {_grid} · '상태 새로고침'으로 확인.")
                st.rerun()
    st.divider()

    gtext2 = st.text_input("요구(자연어)", "신혼부부 아파트 침실2 욕실1 거실 주방", key="geo_text")
    if st.button("🏠 기하 도면 생성"):
        try:
            from plan2graph import text2graph as _t2g, geom_correct as _gc, geom_gen as _gg

            @st.cache_data(show_spinner="g0 실측 면적 prior 집계(최초 1회)...")
            def _priors():
                return _gc.role_area_priors("g0")
            prog = _t2g.parse(gtext2)["program"]
            st.caption(f"방 구성(program): {prog}")
            rooms = _gc.program_to_rooms(prog, _priors())
            edges = _gc.convention_edges(rooms)
            boxes = _gc.correct(rooms, edges)
            v = _gc.verify(rooms, edges, boxes)
            st.image(_gg.render(rooms, boxes),
                     caption=f"자기교정 도면 — 방 {len(rooms)} · 인접실현 "
                             f"{v['adj_rate']*100:.0f}% · 겹침 {v['n_overlap']}")
            c1, c2, c3 = st.columns(3)
            c1.metric("방 수", len(rooms))
            c2.metric("인접 실현율", f"{v['adj_rate']*100:.0f}%")
            c3.metric("겹침", v["n_overlap"])
            st.caption("g0(실측 21,613세대) 면적 prior + 관례 인접 → squarified 자기교정. "
                       "인접 미실현 쌍은 위상 라우팅 대상(2층 자기교정).")
        except Exception as e:  # noqa: BLE001
            st.error(f"기하 생성 실패: {e}")
    st.stop()

if which.startswith("⚖️"):
    st.title("⚖️ 성능 비교 — T-라인 vs G-라인 도면 품질")
    st.caption("최종 잣대: 누가 더 품질 높은 도면을 만드나. T(규칙기반 treemap) vs G(학습 기하모델).")
    st.info("준비 중 — 같은 자연어 요구로 T·G 도면을 생성해 나란히 비교하는 화면을 붙입니다(ADR-0002 ③).")
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

    st.info("① 알려진 치수선의 **양 끝 두 점을 클릭**하세요. ② 그 치수의 실제 길이(mm)를 입력 → scale 자동계산.")
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
        st.write("클릭한 점:", st.session_state.pts)
        if st.button("점 초기화"):
            st.session_state.pts = []; st.rerun()
        pix = None
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
    b1, b2, b3 = st.columns(3)
    if b1.button("✔ 보정 scale 적용", type="primary", disabled=not new_scale,
                 use_container_width=True):
        scale_ocr.update_scale_row(sid, new_scale, "ok", source="manual")
        n = scale_ocr.apply_scale_one_sheet(sid, new_scale)
        review.record_decision({"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "sheet_id": sid, "action": "scale_manual",
                                "params": f"scale={new_scale:.3f},mm={mm}",
                                "result_status": "ok", "note": ""})
        st.success(f"보정 적용 — {n}개 세대에 ㎡ 기록."); st.session_state.pts = []
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
