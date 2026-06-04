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
SPLIT = "Training"
GRAPHS = config.PROCESSED_DIR / "graphs"
FLAGGED = config.PROCESSED_DIR / "flagged"

LEGEND = (
    "**범례** — 선(연결): 🔴문 · 🟢발코니(미닫이창) · 🟠개방통로(문·창 없는 트임) · 🟣인접병합 · ⚫현관↔외부 "
    "(점선=교정 미리보기)　|　방 채움색=세대(연결요소) 구분　|　그래프 노드색=위계 🔴public·🔵private·🟢service, 外=외부"
)
ORIGIN_NOTE = ("ℹ️ program·adjacency·위상은 **원본 라벨이 아니라 우리가 기하추론으로 생성**한 것"
               "(원본엔 방 폴리곤·이름만 있음).")


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


def _status_summary(source_id):
    """출처 검수 현황 데이터(총/정상/격리/사유/by_id)만 집계 — 렌더링 없음.
    개별 페이지의 격리사유 필터와 '검수 현황(종합)' 페이지가 함께 쓴다. 화면 점유를
    없애기 위해 메트릭·차트는 그리지 않고, 종합 페이지에서만 시각화한다."""
    from plan2graph import dataset_status, sources
    gdir = sources.graphs_dir(source_id)
    nkey = len(list(gdir.glob("*.json"))) if gdir.is_dir() else 0  # 파일수 변동=자동 무효화

    @st.cache_data(show_spinner="검수 현황 집계(최초 1회)...")
    def _agg(sid, _n):
        return dataset_status.scan_status(sources.graphs_dir(sid))

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


# ── 사이드바 ──────────────────────────────────────────────────────────────────
st.sidebar.title("🏗 Plan2Graph 관리자")
st.sidebar.caption("데이터셋을 눈으로 검증하는 콘솔")
which = st.sidebar.radio("큐", ["⚠ 격리 (교정)", "✅ 채택 (검수)", "📏 scale 검수/보정",
                                "📜 법령 DB", "📊 결과 대시보드", "🧮 검수 현황(종합)",
                                "🔍 AI-Hub 도면 검수",
                                "🌍 CubiCasa5k 도면검수", "🏙 RPLAN 도면검수"], index=0)

# ════════════════════════════════════════════════════════════════════════════
# 🧮 검수 현황(종합) — AI-Hub·CubiCasa·RPLAN 변환 결과를 한 화면에서 비교
# ════════════════════════════════════════════════════════════════════════════
if which.startswith("🧮"):
    from plan2graph import dataset_status

    st.title("🧮 검수 현황(종합)")
    st.caption("AI-Hub · CubiCasa5k · RPLAN 세 출처의 그래프 변환 결과를 한눈에 — "
               "총/정상/격리(보정 대상)와 격리 사유 분포. 개별 검수는 각 도면검수 메뉴에서.")
    if st.button("🔄 재집계(캐시 비움)", help="재변환·dedup 후 현황을 다시 집계"):
        st.cache_data.clear(); st.rerun()

    SRC = [("aihub", "🔍 AI-Hub"), ("cubicasa5k", "🌍 CubiCasa5k"), ("rplan", "🏙 RPLAN")]
    rows = [(name, _status_summary(sid)) for sid, name in SRC]
    tot = {k: sum(s[k] for _, s in rows) for k in ("total", "success", "quarantine")}

    g = st.columns(3)
    g[0].metric("총 그래프 (3출처 합)", f"{tot['total']:,}")
    _r = (tot['success'] / tot['total'] * 100) if tot['total'] else 0
    g[1].metric("✅ 정상", f"{tot['success']:,}", f"{_r:.1f}%")
    g[2].metric("⚠ 격리(보정 대상)", f"{tot['quarantine']:,}")
    st.divider()

    for name, s in rows:
        st.markdown(f"#### {name}")
        c = st.columns(3)
        c[0].metric("총 그래프", f"{s['total']:,}")
        r = (s['success'] / s['total'] * 100) if s['total'] else 0
        c[1].metric("✅ 정상", f"{s['success']:,}", f"{r:.1f}%")
        c[2].metric("⚠ 격리", f"{s['quarantine']:,}")
        if s["reasons"]:
            n = sum(s["reasons"].values())
            with st.expander(f"격리 사유 분포 ({n:,}건) — 왜 그래프를 못 만들었나", expanded=False):
                st.bar_chart({dataset_status.reason_label(k): v
                              for k, v in s["reasons"].items()})
        elif s["total"] == 0:
            st.caption("아직 변환된 그래프가 없습니다(어댑터 미실행).")
        st.divider()
    st.stop()

# ════════════════════════════════════════════════════════════════════════════
# 🔍 배제 도면 검수 — 그래프 대상에서 빠진 평면도를 실제 PNG로 육안 검증
# ════════════════════════════════════════════════════════════════════════════
if which.startswith("🔍"):
    import io as _io
    from PIL import Image as _PImage
    from plan2graph import inspect_excluded as _ix

    st.title("🔍 AI-Hub 도면 검수")
    st.caption("AI-Hub 도면을 원본 PNG로 확인 — 채택분(dual)·제외분(부분/완전배제) 사유 육안 검증.")
    if not config.RAW_SOURCE_ROOT.is_dir():
        st.error(f"원본 RAW 없음: {config.RAW_SOURCE_ROOT}\n"
                 "PLAN2GRAPH_RAW 환경변수를 SPA/STR zip이 있는 경로로 설정 후 재실행.")
        st.stop()

    with st.expander("ℹ️ 배제 분류 전체 (꼭 읽기)", expanded=True):
        st.markdown(
            "**🟡 부분배제 (V2V로 복구 가능)** — 진짜 평면도인데 라벨이 한쪽만:\n"
            "- **방만(spa_only)**: 방 라벨만 → 엣지(문) 못 만듦 → V2V로 STR 예측 복구\n"
            "- **구조만(str_only)**: 문·벽만 → 노드(방) 못 만듦 → V2V로 SPA 예측 복구\n\n"
            "**🔴 완전배제 (데이터셋에서 영구 제외)**:\n"
            "- **비-FP(평면도 아님)**: 단면도·입면도·구조도 — *위상(방-문-방) 자체가 없음* → 그래프 불가\n"
            "- **중복**: 같은 도면이 라벨종류별로 byte-identical 복제 → 지문(CRC+크기)으로 1장만 남기고 제외 "
            "(전수 SHA256 검증·0충돌)\n"
            "- **OBJ/OCR만**: 객체·문자 라벨만(방·구조 없음) → 그래프 불가. "
            "*※ 이 zip은 미업로드(12.5GB)라 여기 표시 안 됨 — 보려면 OBJ/OCR 원천 업로드 필요.*\n\n"
            "**⚪ 참고**: dual(둘 다) = 이미 v0 그래프화. 품질게이트 격리는 좌측 '⚠ 격리' 메뉴에서 도면+그래프+사유로 검수.")

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
    counts = {"spa_only": len(cats["spa_only"]), "str_only": len(cats["str_only"]),
              "nonfp": len(nonfp), "dup": len(dup), "objocr": len(objocr),
              "dual": len(cats["dual"])}
    cat_label = {"spa_only": "🟡부분배제 방만(STR결손)", "str_only": "🟡부분배제 구조만(SPA결손)",
                 "nonfp": "🔴완전배제 비-FP(평면도아님)", "dup": "🔴완전배제 중복(복사본)",
                 "objocr": "🔴완전배제 OBJ/OCR만", "dual": "⚪참고 둘다(v0)"}
    cat = st.sidebar.selectbox("카테고리", list(cat_label),
                               format_func=lambda k: f"{cat_label[k]} ({counts[k]:,})")
    if cat == "objocr" and not objocr:
        st.warning("OBJ/OCR 원천 zip이 아직 업로드/배치되지 않았습니다. "
                   "scripts/objocr_upload.sh → objocr_setup.sh 후 표시됩니다.")
    house = st.sidebar.selectbox("거주형태", ["(전체)", "APT", "DEH", "ROW"])
    src = {"nonfp": nonfp, "dup": dup, "objocr": objocr}.get(cat, cats.get(cat, []))
    recs = [r for r in src if house == "(전체)" or r["house"] == house]

    st.markdown(f"### {cat_label[cat]} — **{len(recs):,}개**  ·  _{_ix.CATEGORIES[cat]}_")
    st.caption(f"분포: " + " · ".join(f"{k}:{len([r for r in src if r['house']==k]):,}"
                                     for k in ("APT", "DEH", "ROW")))
    if cat == "dup" and src:
        ng = len({r["group"] for r in src})
        st.info(f"🔁 총 **{len(src):,}개 사본** = **{ng:,}그룹** · 원본 **{ng:,}개 채택** + "
                f"중복 **{len(src)-ng:,}개 제외**. 같은 그룹은 연속(1/N…N/N), 모두 byte-identical.")
    view = st.sidebar.radio(
        "👁 보기 모드", ["나란히(원본 | 오버레이)", "겹쳐보기", "원본만"], index=0,
        help="나란히=원본을 먼저 보고 오른쪽 오버레이와 직접 대조 · 겹쳐=원본 위에 라벨 · 원본만=순수 원본")
    mode = ("나란히" if view.startswith("나란히")
            else "겹쳐보기" if view == "겹쳐보기" else "원본만")
    need_overlay = mode != "원본만"
    if need_overlay:
        st.markdown("**라벨 색**: :green[●] 방(SPA) · :red[●] 문 · :orange[●] 창 · :blue[●] 벽(STR) "
                    "— *그려진 게 라벨된 것. 안 그려진 종류 = 라벨 없음(배제 사유).*")

    @st.cache_data(show_spinner="라벨 인덱스 구성(최초 1회)...")
    def _lblidx(sp):
        return _ix.label_index(sp)

    lblidx = _lblidx(split) if need_overlay else {}
    res = st.sidebar.select_slider("표시 해상도(px)", options=[1200, 1600, 2000, 2600, 3200, 4200],
                                   value=2000, help="원본 PNG는 ~17MB 고해상. 클릭→전체화면 시 이 해상도로 보임.")
    ncol = st.sidebar.radio("열 수(겹쳐/원본만)", [1, 2], index=1, horizontal=True)
    PER = 4 if mode == "나란히" else ncol * 2
    st.sidebar.caption("이미지 클릭 → 우상단 ⛶ 전체화면이면 더 크게 보입니다.")
    npages = max(1, (len(recs) + PER - 1) // PER)
    pg = st.sidebar.number_input(f"페이지 (1~{npages})", 1, npages, 1) - 1
    st.sidebar.caption(f"한 페이지 {PER}장 · 총 {npages:,}페이지 · 해상도 {res}px")

    def _cap(r):
        if r.get("group"):   # 중복: 사본마다 i/N + 원본/제외
            return (f"🔁중복그룹#{r['group']} · {r['label']} {r['i']}/{r['n']} · "
                    f"key={r['key']} · {'✅원본(채택)' if r['kept'] else '❌중복(제외)'}")
        return f"{r['house']} · {r['key']} · 라벨={r['labels']}"

    _inspect_3mode(recs[pg * PER:(pg + 1) * PER], mode, res, ncol,
                   lambda r, ov: _ix.render(r, lblidx, overlay=ov), _cap)
    st.stop()

# ════════════════════════════════════════════════════════════════════════════
# 🌍 CubiCasa5k 도면검수 — 글로벌 데이터 정상분(변환)·제외분(사유) 육안 검증
# ════════════════════════════════════════════════════════════════════════════
if which.startswith("🌍"):
    from plan2graph import cubicasa_inspect as _cci

    st.title("🌍 CubiCasa5k 도면검수")
    st.caption("글로벌 데이터(CubiCasa5k) 도면을 원본+방 오버레이로 — 정상분(그래프 변환)·제외분(사유).")
    if not _cci.CC_ROOT.is_dir():
        st.error(f"CubiCasa5k 데이터 없음: {_cci.CC_ROOT}")
        st.stop()

    @st.cache_data(show_spinner="CubiCasa 샘플 스캔(최초 1회)...")
    def _ccscan():
        return _cci.scan()

    s = _ccscan()
    with st.expander("ℹ️ 분류 (꼭 읽기)", expanded=True):
        st.markdown(
            "- **정상분(converted)**: model.svg에서 방 폴리곤 추출 성공 → 그래프化(global_cubicasa)\n"
            "- **제외분(excluded)**: 방 0개 / SVG 파싱 실패 → 제외\n\n"
            "오버레이: :green[●] 방 · :red[●] 문 (SVG 주석을 F1_scaled.png에). "
            "그려진 게 추출된 것 — 안 그려지면 추출 실패(제외 사유).")
    # 검수 현황은 '🧮 검수 현황(종합)' 메뉴로 분리 — 여기선 필터용 데이터만 조용히 집계.
    _ccsumm = _status_summary("cubicasa5k")
    cat_label = {"converted": "🟢 정상분(그래프 변환)", "excluded": "🔴 제외분(방없음/실패)"}
    cat = st.sidebar.selectbox("분류", list(cat_label),
                               format_func=lambda k: f"{cat_label[k]} ({len(s[k]):,})")
    subs = sorted({r["sub"] for r in s[cat]})
    subf = st.sidebar.selectbox("세트", ["(전체)"] + subs)
    recs = [r for r in s[cat] if subf == "(전체)" or r["sub"] == subf]
    if cat == "converted":   # 정상/격리·사유 필터
        recs = _status_filter(recs, _ccsumm, lambda r: f"CC_{r['id']}")
    view = st.sidebar.radio(
        "👁 보기 모드", ["🔗 그래프검수(원본∥그래프)", "나란히(원본 | 오버레이)", "겹쳐보기", "원본만"],
        index=0, help="그래프검수=원본∥위상그래프+결정 · 나란히=원본vs오버레이 · 겹쳐=원본 위 방·문 · 원본만")
    if view.startswith("🔗"):
        if cat != "converted":
            st.info("그래프검수는 '정상분(converted)'에서만 가능합니다(사이드바 분류를 정상분으로).")
        else:
            _graph_review("cubicasa5k", recs, lambda r, ov: _cci.render(r, overlay=ov),
                          lambda r: f"CC_{r['id']}")
        st.stop()
    mode = ("나란히" if view.startswith("나란히")
            else "겹쳐보기" if view == "겹쳐보기" else "원본만")
    res = st.sidebar.select_slider("표시 해상도(px)", options=[800, 1100, 1500, 2000, 2600],
                                   value=1500, help="CubiCasa 원본은 ~1100px(AI-Hub보다 저해상).")
    ncol = st.sidebar.radio("열 수(겹쳐/원본만)", [1, 2], index=1, horizontal=True)
    PER = 4 if mode == "나란히" else ncol * 2
    st.markdown(f"### {cat_label[cat]} — **{len(recs):,}개**")
    npages = max(1, (len(recs) + PER - 1) // PER)
    pg = st.sidebar.number_input(f"페이지 (1~{npages})", 1, npages, 1) - 1
    st.sidebar.caption(f"한 페이지 {PER}장 · 총 {npages:,}페이지 · {res}px")

    def _cap(r):
        c = f"{r['sub']}/{r['id']}"
        if cat == "excluded":
            c += f" · 사유: {_cci.exclude_reason(r)}"
        return c

    _inspect_3mode(recs[pg * PER:(pg + 1) * PER], mode, res, ncol,
                   lambda r, ov: _cci.render(r, overlay=ov), _cap)
    st.stop()

# ════════════════════════════════════════════════════════════════════════════
# 🏙 RPLAN 도면검수 — 글로벌 데이터 정상분(변환)·제외분(사유) 육안 검증
# ════════════════════════════════════════════════════════════════════════════
if which.startswith("🏙"):
    from plan2graph import rplan_inspect as _rpi

    st.title("🏙 RPLAN 도면검수")
    st.caption("글로벌 데이터(RPLAN ~80k) 도면을 방 종류색으로 — 원본 전체/변환됨/미변환 육안 검증.")
    if not _rpi.RP_ROOT.is_dir():
        st.error(f"RPLAN 데이터 없음: {_rpi.RP_ROOT}\n\n"
                 "Zenodo 'RPLAN dataset.zip'을 풀어 이 경로에 PNG들을 두거나,\n"
                 "다른 곳에 풀었으면 환경변수 PLAN2GRAPH_RPLAN=<푼 경로> 로 지정 후 재실행하세요.")
        st.stop()

    @st.cache_data(show_spinner="RPLAN 원본 스캔(최초 1회)...")
    def _rpscan():
        return _rpi.scan()

    s = _rpscan()
    if not s["all"]:
        st.warning(f"{_rpi.RP_ROOT} 안에 PNG가 없습니다. zip을 이 경로에 풀었는지 확인하세요.")
        st.stop()
    with st.expander("ℹ️ 분류 (꼭 읽기)", expanded=True):
        st.markdown(
            "- **전체(원본)**: 받은 PNG 전부 — 변환 안 해도 바로 도면 검수.\n"
            "- **변환됨**: 어댑터로 그래프化된 것(global_rplan).\n"
            "- **미변환**: 아직 변환 안 됐거나 변환 실패한 것.\n\n"
            "RPLAN 원본이 **4채널 인덱스 맵**이면 category 채널을 방 종류색으로 칠해 보여주고"
            "(오버레이: instance 경계=검정 외곽선·문=:red[●]빨강), **이미 렌더된 도면 패키지**"
            "(snapshot_train 등)면 원본 이미지를 그대로 표시합니다.")
    if not s["converted"]:
        st.info("아직 그래프 변환 전입니다 — '전체(원본)'로 검수하세요. "
                "변환됨/미변환 분류를 보려면 먼저 어댑터 실행: "
                "`python src/plan2graph/adapters/rplan_vector.py --src <data_*_converted.pkl>`")
    # 검수 현황은 '🧮 검수 현황(종합)' 메뉴로 분리 — 여기선 필터용 데이터만 조용히 집계.
    _rpsumm = _status_summary("rplan")
    # 색 범례
    st.markdown("**방 색**: " + " · ".join(
        f":gray[■]{_rpi.CAT_KO[k]}" for k in sorted(_rpi.CAT_KO)))
    cat_label = {"all": "📋 전체(원본)", "converted": "🟢 변환됨(그래프)",
                 "excluded": "🔴 미변환/실패"}
    cat = st.sidebar.selectbox("분류", list(cat_label),
                               format_func=lambda k: f"{cat_label[k]} ({len(s[k]):,})")
    recs = s[cat]
    if cat == "converted":   # 정상/격리·사유 필터(원본 보고 '왜 못했나' 확인)
        recs = _status_filter(recs, _rpsumm, lambda r: f"RPLAN_{r['id']}")
    view = st.sidebar.radio(
        "👁 보기 모드", ["🔗 그래프검수(원본∥그래프)", "나란히(원본 | 오버레이)", "겹쳐보기", "원본만"],
        index=0, help="그래프검수=원본∥위상그래프+결정 · 나란히=원본vs경계·문 오버레이")
    if view.startswith("🔗"):
        if cat != "converted":
            st.info("그래프검수는 '변환됨(converted)'에서만 가능합니다(사이드바 분류를 변환됨으로).")
        else:
            _graph_review("rplan", recs, lambda r, ov: _rpi.render(r, overlay=ov),
                          lambda r: f"RPLAN_{r['id']}")
        st.stop()
    mode = ("나란히" if view.startswith("나란히")
            else "겹쳐보기" if view == "겹쳐보기" else "원본만")
    res = st.sidebar.select_slider("표시 해상도(px)", options=[512, 768, 1024, 1536, 2048],
                                   value=1024, help="RPLAN 원본은 256px(인덱스 맵)을 ×4 확대해 표시.")
    ncol = st.sidebar.radio("열 수(겹쳐/원본만)", [1, 2, 3], index=1, horizontal=True)
    PER = 4 if mode == "나란히" else ncol * 2
    st.markdown(f"### {cat_label[cat]} — **{len(recs):,}개**")
    npages = max(1, (len(recs) + PER - 1) // PER)
    pg = st.sidebar.number_input(f"페이지 (1~{npages})", 1, npages, 1) - 1
    st.sidebar.caption(f"한 페이지 {PER}장 · 총 {npages:,}페이지 · {res}px")

    def _cap(r):
        c = r["id"]
        if cat == "excluded":
            c += f" · 사유: {_rpi.exclude_reason(r)}"
        return c

    _inspect_3mode(recs[pg * PER:(pg + 1) * PER], mode, res, ncol,
                   lambda r, ov: _rpi.render(r, overlay=ov), _cap)
    st.stop()

# ════════════════════════════════════════════════════════════════════════════
# 📊 결과 대시보드 — 데이터셋·모델·지표 시각화 (설명/PPT용)
# ════════════════════════════════════════════════════════════════════════════
if which.startswith("📊"):
    import json as _json
    import random as _random
    from collections import Counter as _Counter
    from plan2graph import model_baseline as _mb
    from plan2graph import review as _rv

    REL = config.DATA_DIR / "releases"
    # 완전한 release만(manifest+splits/test 보유) — 미완 스모크 빌드(v2 등) 제외해 크래시 방지
    vers = sorted([p.name for p in REL.glob("v*") if p.is_dir()
                   and (p / "manifest.json").exists()
                   and (p / "splits" / "test.txt").exists()]) if REL.exists() else []
    if not vers:
        st.info("동결된 버전이 없습니다. `python src/plan2graph/release.py v0` 먼저 실행.")
        st.stop()

    st.title("📊 Plan2Graph 결과 대시보드")
    st.caption("자연어 요구 → [제약그래프] → 생성AI → [배치그래프] → 규제AI 검증 → 무결 도면. "
               "본 데이터셋·최소모델·규제검증 결과.")

    ver = st.sidebar.selectbox("버전", vers, index=len(vers) - 1)
    if st.sidebar.button("🔄 새로고침(캐시 비움)"):
        st.cache_data.clear(); st.cache_resource.clear(); st.rerun()

    def _mt(p):  # 파일 수정시각(캐시 무효화 키)
        return p.stat().st_mtime if p.exists() else 0

    @st.cache_data(show_spinner=False)
    def _manifest(v, _k):
        p = REL / v / "manifest.json"
        return _json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    @st.cache_data(show_spinner=False)
    def _evalr(v, _k):
        p = REL / v / "eval.json"
        return _json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    @st.cache_data(show_spinner="데이터셋 통계 집계...")
    def _roomdist(v, _k):
        c = _Counter()
        for f in (REL / v / "graphs").glob("*.json"):
            r = _json.loads(f.read_text(encoding="utf-8"))
            for n in r["layout"]["nodes"]:
                if n.get("type") and n["type"] != "exterior":
                    c[n["type"]] += 1
        return dict(c.most_common())

    @st.cache_resource(show_spinner="모델 학습(통계 생성기)...")
    def _model(v, _k):
        return _mb.fit(_mb._load_split(v, "train"))

    man = _manifest(ver, _mt(REL / ver / "manifest.json"))
    ev = _evalr(ver, _mt(REL / ver / "eval.json"))

    # ── 1) 데이터셋 ──
    st.header(f"1. 데이터셋 ({ver})")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("세대 그래프", f"{man.get('n_graphs','-'):,}" if man.get('n_graphs') else "-")
    c2.metric("시트(도면)", f"{man.get('n_sheets','-'):,}" if man.get('n_sheets') else "-")
    sp = man.get("splits", {})
    c3.metric("train/val/test", f"{sp.get('train','-')}/{sp.get('val','-')}/{sp.get('test','-')}")
    c4.metric("㎡ 확보", f"{man.get('n_scaled_m2','-'):,}" if man.get('n_scaled_m2') else "-")
    st.caption(f"정상 필터: {man.get('clean_filter','-')} · 제외 {man.get('excluded',{})} · "
               f"test 동결: {man.get('test_frozen_by','-')}")
    st.subheader("방 종류 분포 (노드 수)")
    st.bar_chart(_roomdist(ver, _mt(REL / ver / "manifest.json")))

    # ── 2) 학습된 인접확률 (거실 허브) ──
    st.header("2. 학습된 공간 인접확률")
    st.caption("최소 생성모델이 데이터에서 학습한 P(방A~방B). 거실이 허브임을 자동 학습.")
    if ev.get("top_adjacency"):
        st.bar_chart({d["pair"]: d["p"] for d in ev["top_adjacency"]})

    # ── 3) 모델 평가 지표 ──
    st.header("3. 최소 생성모델 평가 (test)")
    st.caption("제약그래프(program)로 배치그래프 생성 → 규제AI 검증. "
               "torch 없는 통계 baseline (신경망 격상 전).")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("위상 무결성", f"{ev.get('integrity_valid_rate',0)*100:.0f}%")
    m2.metric("법규(채광) 통과", f"{ev.get('legal_pass_rate',0)*100:.0f}%")
    m3.metric("인접 사실성(L1)", f"{ev.get('adjacency_L1_distance','-')}", help="0=실제와 일치")
    m4.metric("다양성", f"{ev.get('diversity',0)*100:.0f}%")
    m5.metric("신규성", f"{ev.get('novelty',0)*100:.0f}%", help="train에 없는 새 구조")

    # ── 4) 실제 vs 생성 예시 ──
    st.header("4. 실제 도면 vs AI 생성 (같은 program)")
    if "ex_seed" not in st.session_state:
        st.session_state.ex_seed = 1
    if st.button("🎲 다른 예시"):
        st.session_state.ex_seed += 1
    test = _mb._load_split(ver, "test")
    if test:
        rec = test[st.session_state.ex_seed % len(test)]
        program = dict(_Counter(n["type"] for n in rec["layout"]["nodes"]
                                if isinstance(n["id"], int)))
        st.caption(f"program: {program}")
        gen = _mb.generate(_model(ver, _mt(REL / ver / "manifest.json")), program,
                           _random.Random(st.session_state.ex_seed))
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

    # ── 5) A/B 비교 — 데이터버전 × 생성기 × 규제루프 (한 화면, PROJECT_PLAN §4-4·§5) ──
    st.header("5. A/B 비교 — 데이터버전 × 생성기 × 규제루프")
    st.caption("핵심 비교를 한 화면에: 데이터 버전(v0…) · 생성기(규칙기반 vs 신경망) · 사전학습(없음/CubiCasa/RPLAN) · "
               "규제루프(on/off)를 같은 동결 test에서 정량 비교. 여러 시드는 평균±표준편차로 접음. "
               "단일 소스 = 실험 원장 runs/index.jsonl (`python -m plan2graph.experiments agg`).")

    @st.cache_data(show_spinner="실험 원장 집계...")
    def _agg(_k):
        from plan2graph import experiments
        return experiments.agg_summary()

    _idx = ROOT / "runs" / "index.jsonl"
    summ = _agg(_mt(_idx)) if _idx.exists() else {"eval": [], "generalization": []}

    def _gen_label(r):
        return f"{r['generator']}({r['arch']})" if r.get("arch") else r["generator"]

    def _pm(m, s):
        return "—" if m is None else (f"{m:.3f}±{s:.3f}" if s else f"{m:.3f}")

    if summ["eval"]:
        st.subheader("전체 test")
        st.table([{"데이터버전": r["version"], "생성기": _gen_label(r),
                   "사전학습": r["pretrain"], "규제루프": r["loop"], "시드": r["seeds"],
                   "인접L1↓": _pm(r["adj_L1_mean"], r["adj_L1_std"]),
                   "무결성": f"{(r['integrity'] or 0)*100:.0f}%",
                   "법규": f"{(r['legal'] or 0)*100:.0f}%",
                   "다양성": f"{(r['diversity'] or 0)*100:.0f}%",
                   "신규성": f"{(r['novelty'] or 0)*100:.0f}%"} for r in summ["eval"]])
        st.caption("인접L1↓: 낮을수록 실제 배치에 가까움 · 무결성=위상 R1~R5 · "
                   "법규=채광 등 통과(규제루프 on이 위반 자동보정).")
    if summ["generalization"]:
        st.subheader("일반화 — seen / unseen program")
        st.caption("처음 보는 방 구성(unseen)에서의 사실성 — 사전학습 효과가 가장 드러나는 축.")
        st.table([{"데이터버전": r["version"], "생성기": _gen_label(r),
                   "사전학습": r["pretrain"], "subset": r["subset"], "시드": r["seeds"],
                   "인접L1↓": _pm(r["adj_L1_mean"], r["adj_L1_std"])}
                  for r in summ["generalization"]])
    if not summ["eval"]:
        ab_path = REL / "eval_ab.json"     # 원장 없을 때만 레거시 스냅샷 폴백
        ab = _json.loads(ab_path.read_text(encoding="utf-8")).get("rows", []) \
            if ab_path.exists() else []
        if ab:
            st.caption("실험 원장 없음 — eval_ab.json 스냅샷 표시")
            st.table([{"버전": r["version"], "생성기": r["generator"], "규제루프": r["reg_loop"],
                       "무결성": r["integrity"], "법규": r["legal"], "인접L1↓": r["adj_L1"],
                       "다양성": r["diversity"], "신규성": r["novelty"]} for r in ab])
        else:
            st.info("비교 데이터 없음: `bash scripts/run_matrix.sh`"
                    "(또는 `python -m plan2graph.eval_gen`) 실행 후 표시됩니다.")
    else:
        st.caption("요약: 전체 test에선 사전학습 중립(신경망≈규칙기반 수준), 일반화(unseen)에선 사전학습이 "
                   "소폭 우위·분산 감소. 상세 = EXPERIMENTS.md. "
                   "RPLAN(80,371) 사전학습 결과는 후속 업데이트로 같은 표에 행이 추가됨.")
    st.stop()

# ════════════════════════════════════════════════════════════════════════════
# 📜 법령 DB — 최신화(관리자 클릭) + 법령/규정 조회
# ════════════════════════════════════════════════════════════════════════════
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
    from streamlit_image_coordinates import streamlit_image_coordinates
    from PIL import Image
    import io as _io

    scale_map = scale_ocr.load_scale_csv()
    if not scale_map:
        st.info("scale.csv 없음. 먼저 `python src/plan2graph/scale_ocr.py pass` 실행.")
        st.stop()
    conf_f = st.sidebar.selectbox("신뢰도 필터",
                                  ["none (미인식)", "low (의심)", "ok (정상)", "(전체)"])
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
    if pcol.button("◀ 이전", use_container_width=True):
        st.session_state.si -= 1; st.session_state.pts = []; st.rerun()
    if ncol.button("다음 ▶", use_container_width=True):
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
