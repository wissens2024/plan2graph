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


# ── 사이드바 ──────────────────────────────────────────────────────────────────
st.sidebar.markdown("#### 🏗 Plan2Graph 관리자")
st.sidebar.caption("데이터셋 구축 ───── · 생성 AI ─────")
which = st.sidebar.radio("메뉴", ["🧮 종합 현황", "🏢 AI-Hub 검수",
                                 "🏠 CubiCasa 검수", "📐 RPLAN 검수",
                                 "📏 scale 보정", "📜 법령 DB",
                                 "📊 위상 모델 결과", "🏗 도면 생성(시연)"],
                          index=0, label_visibility="collapsed")

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
    st.stop()

# ════════════════════════════════════════════════════════════════════════════
# 🏢 배제 도면 검수 — 그래프 대상에서 빠진 평면도를 실제 PNG로 육안 검증
# ════════════════════════════════════════════════════════════════════════════
if which.startswith("🏢"):
    import io as _io
    from PIL import Image as _PImage
    from plan2graph import inspect_excluded as _ix

    st.title("🏢 AI-Hub 도면 검수")
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
        "보기 모드", ["그래프검수(원본∥그래프)", "나란히(원본 | 오버레이)", "겹쳐보기", "원본만"],
        index=0, help="그래프검수=원본∥그래프+결정(승인/격리/제외) · 나란히=원본vs오버레이 · 겹쳐 · 원본만")
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

    st.title("📊 생성 AI 결과")
    st.caption("파이프라인: 자연어 →[제약그래프]→ 생성AI →[배치그래프]→ 규제AI 검증 → 무결 도면")
    st.markdown("**구현 현황** &nbsp; 단일세대 위상 ✅ &nbsp;·&nbsp; 단일세대 기하 🔜 &nbsp;·&nbsp; "
                "다세대 floor-plate ⏳ (데이터갭)")

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

    # 실험 원장(시드 집계) — §1·§3·§5 공용 단일 소스(dataset-version-scheme).
    @st.cache_data(show_spinner="실험 원장 집계...")
    def _agg(_k):
        from plan2graph import experiments
        return experiments.agg_summary()

    _idx = ROOT / "runs" / "index.jsonl"
    summ = _agg(_mt(_idx)) if _idx.exists() else {"eval": [], "generalization": []}
    def _compose(v):   # 각 버전 manifest 라이브(하드코딩 금지 — doc/code/GUI 일치)
        p = REL / v / "manifest.json"
        return _manifest(v, _mt(p)) if p.exists() else None

    def _pm(m, s):
        return "—" if m is None else (f"{m:.3f}±{s:.3f}" if s else f"{m:.3f}")

    # ── 1) 데이터셋 — 버전별 학습 데이터 구성 ──
    st.header("1. 데이터셋 — 버전별 학습 데이터 구성")
    st.caption("버전 = 학습에 넣은 데이터 조합(누적 아닌 조합 실험). "
               "**test는 AI-Hub 동결분으로 전 버전 공유** → 비교 기준 고정. 재학습 시 최종값만 남김.")
    _PLAN = {"v1": {"ft": "AI-Hub + 보정(확장)", "pre": "—", "res": "⏳ 보정 진행중"},
             "v3": {"ft": "AI-Hub(dual+V2V)", "pre": "+ RPLAN 80,371", "res": "⏳ 학습대기"}}
    rows1 = []
    for v in ("v0", "v1", "v2", "v3"):
        m = _compose(v)
        if m:    # 동결된 버전 = 실제 manifest(라이브)
            ps, psh = m.get("per_source", {}), m.get("per_source_sheets", {})
            ai, ai_s = ps.get("aihub"), psh.get("aihub")
            cubi, rpl = ps.get("cubicasa5k"), ps.get("rplan")
            ft = (f"AI-Hub {ai_s:,} 도면 ({ai:,} 세대)" if ai and ai_s
                  else (f"AI-Hub {ai:,}" if ai else "—"))
            pre = " + ".join([s for s in (f"CubiCasa {cubi:,}" if cubi else None,
                                          f"RPLAN {rpl:,}" if rpl else None) if s]) or "—"
            rows1.append({"버전": v, "파인튜닝(한국형)": ft, "사전학습(글로벌)": pre, "결과": "✅ 완료"})
        else:    # 미동결 버전 = 계획 표기
            pl = _PLAN.get(v, {})
            rows1.append({"버전": v, "파인튜닝(한국형)": pl.get("ft", "—"),
                          "사전학습(글로벌)": pl.get("pre", "—"), "결과": pl.get("res", "⏳")})
    st.table(rows1)
    st.subheader(f"선택 버전 상세 — {ver} (파인튜닝/평가 데이터셋)")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("도면(시트)", f"{man.get('n_sheets','-'):,}" if man.get('n_sheets') else "-")
    c2.metric("세대 그래프", f"{man.get('n_graphs','-'):,}" if man.get('n_graphs') else "-")
    sp = man.get("splits", {})
    c3.metric("train/val/test", f"{sp.get('train','-')}/{sp.get('val','-')}/{sp.get('test','-')}")
    c4.metric("㎡ 확보", f"{man.get('n_scaled_m2','-'):,}" if man.get('n_scaled_m2') else "-")
    st.caption(f"정상 필터: {man.get('clean_filter','-')} · 제외 {man.get('excluded',{})} · "
               f"test 동결: {man.get('test_frozen_by','-')}")
    st.subheader("방 종류 분포 (노드 수)")
    st.bar_chart(_roomdist(ver, _mt(REL / ver / "manifest.json")))

    # ── 2) 핵심 결과 — 균형 소버린 벤치마크 ──
    st.header("2. 핵심 결과 — 균형 소버린 벤치마크 (동결 test 300시트, loop off)")
    _b1, _b2, _b3 = st.columns(3)
    _b1.success("🔄 **반전** — 신경망 > 규칙기반\n\n(균형 매크로)")
    _b2.success("🌍 **RPLAN 사전학습 최선**\n\n(v3, 예비 n=1)")
    _b3.success("⚖️ **규제루프** — 법규 → 100%")
    st.caption("주거형태(APT/DEH/ROW) 균형 test. **매크로 평균**(유형 동등가중)이 소버린 헤드라인 — "
               "원시분포(APT 94%)가 가리던 약형 유형을 드러냄. 여러 시드 평균.")

    def _cfglabel(version, generator, pretrain):
        g = "규칙기반" if generator == "규칙기반" else "신경망"
        return version, g, ("—" if pretrain in (None, "없음") else f"+{pretrain}")

    def _f3(x):
        return f"{x:.3f}" if isinstance(x, (int, float)) else "—"

    # 3-A) dwelling 매크로 (소버린 헤드라인)
    dwl = sorted(summ.get("dwelling", []),
                 key=lambda r: (r["version"], r["generator"], str(r.get("pretrain"))))
    if dwl:
        rowsA = []
        for r in dwl:
            v, g, pre = _cfglabel(r["version"], r["generator"], r.get("pretrain"))
            rowsA.append({"코퍼스": v, "생성기": g, "사전학습": pre,
                          "APT": _f3(r.get("APT")), "DEH": _f3(r.get("DEH")),
                          "ROW": _f3(r.get("ROW")), "매크로 adj_L1↓": _f3(r.get("macro"))})
        st.table(rowsA)
        st.caption("**반전**: 균형 매크로에선 신경망 < 규칙기반(예: v0 0.188 vs 0.205) — "
                   "규칙기반은 APT만 잘하고 DEH/ROW에서 무너짐. 소버린(전 유형)엔 신경망 우위.")

    # 3-B) 전체(마이크로) · 일반화(unseen) · 법규
    evd = {(r["version"], r["generator"], r.get("pretrain"), r["loop"]): r for r in summ["eval"]}
    und = {(r["version"], r["generator"], r.get("pretrain")): r
           for r in summ["generalization"] if r["subset"] == "unseen"}
    cfgs = []
    for r in summ["eval"]:
        k = (r["version"], r["generator"], r.get("pretrain"))
        if k not in cfgs:
            cfgs.append(k)
    rowsB = []
    for k in sorted(cfgs):
        off, on, u = evd.get((*k, "off")), evd.get((*k, "on")), und.get(k)
        if not off:
            continue
        v, g, pre = _cfglabel(*k)
        legal = (f"{(off['legal'] or 0)*100:.0f}→{(on['legal'] or 0)*100:.0f}%"
                 if on else f"{(off['legal'] or 0)*100:.0f}%")
        rowsB.append({"코퍼스": v, "생성기": g, "사전학습": pre,
                      "전체 adj_L1↓": _pm(off["adj_L1_mean"], off["adj_L1_std"]),
                      "unseen adj_L1↓": _pm(u["adj_L1_mean"], u["adj_L1_std"]) if u else "—",
                      "무결성": f"{(off['integrity'] or 0)*100:.0f}%", "법규(off→on)": legal})
    if rowsB:
        st.subheader("전체 test(마이크로) · 일반화(unseen) · 법규")
        st.table(rowsB)
        st.caption("V2V 확장(v2)은 충실도·법규 악화(법규 off v0~0.9→v2~0.4; 규제루프 on→100%). "
                   "CubiCasa 사전학습 ≈ 중립. 신경망 전체 adj_L1도 규칙기반 하회(반전). v3(+RPLAN)는 학습 후 행 추가.")
    else:
        st.info("성능 데이터 없음: `bash scripts/run_matrix.sh` 후 표시됩니다.")

    # ── 3.5) Neuro-Symbolic 구성 기여 (ablation 사다리) ──
    st.header("3. 구성요소 기여 — Neuro-Symbolic ablation 사다리")
    st.caption("생성 파이프라인을 구성요소별로 누적하며 균형 매크로 기여를 본다 — "
               "각 단계의 delta = 그 요소(Neuro/Symbolic)의 기여(논문 ablation).")
    _dwl = summ.get("dwelling", [])
    if not _dwl:
        st.info("ablation 데이터 없음: `bash scripts/run_matrix.sh` 후 표시됩니다.")
    else:
        avs = sorted({r["version"] for r in _dwl})
        abl_v = st.selectbox("코퍼스", avs, key="abl_v")
        aps = sorted({r.get("pretrain") for r in _dwl
                      if r["version"] == abl_v and r["generator"] == "신경망"})
        abl_p = st.selectbox("사전학습(신경망)", aps or ["없음"], key="abl_p")

        def _dw(gen, pre=None):
            return next((r for r in _dwl if r["version"] == abl_v and r["generator"] == gen
                         and (pre is None or r.get("pretrain") == pre)), None)

        def _evr(gen, loop, pre=None):
            return next((r for r in summ["eval"] if r["version"] == abl_v
                         and r["generator"] == gen and r["loop"] == loop
                         and (pre is None or r.get("pretrain") == pre)), None)

        def _f3(x):
            return f"{x:.3f}" if isinstance(x, (int, float)) else "—"

        def _pct(x):
            return f"{x*100:.0f}%" if isinstance(x, (int, float)) else "—"

        b_dw, b_ev = _dw("규칙기반"), _evr("규칙기반", "off")
        n_dw, n_off, n_on = _dw("신경망", abl_p), _evr("신경망", "off", abl_p), _evr("신경망", "on", abl_p)
        ladder = []
        if b_dw and b_ev:
            ladder.append({"구성": "규칙기반(통계)", "매크로 adj_L1↓": _f3(b_dw.get("macro")),
                           "법규": _pct(b_ev.get("legal")), "무결성": _pct(b_ev.get("integrity")),
                           "다양성": _f3(b_ev.get("diversity")), "기여(Δ)": "기준선"})
        if n_dw and n_off:
            d = (b_dw.get("macro") - n_dw.get("macro")) if (b_dw and isinstance(b_dw.get("macro"), (int, float))
                                                            and isinstance(n_dw.get("macro"), (int, float))) else None
            sign = ("−" if d > 0 else "+") if d is not None else ""
            ladder.append({"구성": "+ 신경망(Relation-Aware GNN)", "매크로 adj_L1↓": _f3(n_dw.get("macro")),
                           "법규": _pct(n_off.get("legal")), "무결성": _pct(n_off.get("integrity")),
                           "다양성": _f3(n_off.get("diversity")),
                           "기여(Δ)": (f"Neuro: 매크로 {sign}{abs(d):.3f}" if d is not None else "Neuro")})
        if n_on and n_off:
            dl = (n_on.get("legal") - n_off.get("legal")) if (isinstance(n_on.get("legal"), (int, float))
                                                              and isinstance(n_off.get("legal"), (int, float))) else None
            ladder.append({"구성": "+ 자기교정(Symbolic loop)",
                           "매크로 adj_L1↓": _f3(n_dw.get("macro")) if n_dw else "—",
                           "법규": _pct(n_on.get("legal")), "무결성": _pct(n_on.get("integrity")),
                           "다양성": _f3(n_on.get("diversity")),
                           "기여(Δ)": (f"Symbolic: 법규 +{dl*100:.0f}%p" if dl is not None else "Symbolic")})
        ladder.append({"구성": "+ Constrained RL (예정)", "매크로 adj_L1↓": "—", "법규": "—",
                       "무결성": "—", "다양성": "—", "기여(Δ)": "SWRL 페널티 + Space Syntax 보상"})
        if len(ladder) > 1:
            st.table(ladder)
            st.caption("Neuro(신경망)=충실도(매크로↓), Symbolic(자기교정)=법규준수(off→on). "
                       "사전학습은 별개 축(§3). v3/v4(RPLAN/결합) 학습되면 사다리에 자동 반영.")
        else:
            st.info("ablation 데이터 부족: 매트릭스 실행 후 표시.")

    # ── 4) (상세) 실제 vs 생성 예시 ──
    with st.expander("▸ 상세 — 실제 도면 vs AI 생성 (같은 program · 규칙기반 예시)"):
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

    # ── 5) (상세) 전체 매트릭스 — 시드·loop 전수 ──
    with st.expander("▸ 상세 — 전체 매트릭스 (시드·loop 전수, 다양성·신규성 포함)"):
        st.caption("핵심 요약은 §2. 여기는 **전수 원천**(데이터버전 × 생성기 × 규제루프 × 시드). "
                   "단일 소스 = runs/index.jsonl. ⚠️ '데이터버전'은 사전학습축 라벨(ds_version) — "
                   "코퍼스(v0 dual / v2 +V2V) 정확 구분은 §2.")

        def _gen_label(r):
            return f"{r['generator']}({r['arch']})" if r.get("arch") else r["generator"]

        def _sort_key(r):
            return (r.get("ds_version", "z"), 0 if r["generator"] == "규칙기반" else 1,
                    r.get("arch", ""), str(r.get("loop", "")))

        if summ["eval"]:
            st.subheader("전체 test")
            st.table([{"데이터버전": r["ds_version"], "생성기": _gen_label(r),
                       "규제루프": r["loop"], "시드": r["seeds"],
                       "인접L1↓": _pm(r["adj_L1_mean"], r["adj_L1_std"]),
                       "무결성": f"{(r['integrity'] or 0)*100:.0f}%",
                       "법규": f"{(r['legal'] or 0)*100:.0f}%",
                       "다양성": f"{(r['diversity'] or 0)*100:.0f}%",
                       "신규성": f"{(r['novelty'] or 0)*100:.0f}%"}
                      for r in sorted(summ["eval"], key=_sort_key)])
        if summ["generalization"]:
            st.subheader("일반화 — seen / unseen program")
            st.table([{"데이터버전": r["ds_version"], "생성기": _gen_label(r),
                       "subset": r["subset"], "시드": r["seeds"],
                       "인접L1↓": _pm(r["adj_L1_mean"], r["adj_L1_std"])}
                      for r in sorted(summ["generalization"], key=_sort_key)])
        if not summ["eval"]:
            ab_path = REL / "eval_ab.json"
            ab = _json.loads(ab_path.read_text(encoding="utf-8")).get("rows", []) \
                if ab_path.exists() else []
            if ab:
                st.caption("실험 원장 없음 — eval_ab.json 스냅샷 표시")
                st.table([{"버전": r["version"], "생성기": r["generator"], "규제루프": r["reg_loop"],
                           "무결성": r["integrity"], "법규": r["legal"], "인접L1↓": r["adj_L1"],
                           "다양성": r["diversity"], "신규성": r["novelty"]} for r in ab])
            else:
                st.info("비교 데이터 없음: `bash scripts/run_matrix.sh` 실행 후 표시됩니다.")

    st.stop()

# ════════════════════════════════════════════════════════════════════════════
# 🏗 도면 생성 (시연) — 자연어 → 위상 도면 + 자기교정 근거
# ════════════════════════════════════════════════════════════════════════════
if which.startswith("🏗"):
    from pathlib import Path as _Path
    from plan2graph import review as _rv
    _ROOT = _Path(__file__).resolve().parent
    st.title("🏗 도면 생성 (시연)")
    st.caption("자연어 요구 → 제약(program) → 신경망 생성 → Symbolic 자기교정 → 위상 도면. "
               "**패널2**: 자기교정 OFF∥ON 대비 · **패널3**: 위반·수정 근거. (CPU 추론)")
    _runs = sorted(p.parent.name for p in (_ROOT / "runs").glob("gen-v0-neural*/checkpoint.pt"))
    if not _runs:
        st.info("학습된 신경망 체크포인트 없음: `bash scripts/run_matrix.sh` 후 표시.")
    else:
        gtext = st.text_input("자연어 요구", "신혼부부 아파트 침실2 욕실1 거실 주방", key="ns_text")
        gc1, gc2, gc3 = st.columns([3, 1, 1])
        _def = next((r for r in _runs if "noPretrain-seed42" in r), _runs[0])
        gck = gc1.selectbox("생성기(체크포인트)", _runs, index=_runs.index(_def), key="ns_ck")
        gseed = gc2.number_input("시드", 0, 9999, 1, key="ns_seed")
        ght = gc3.selectbox("주거형태", ["자동", "APT", "DEH", "ROW"], key="ns_house",
                            help="type조건 모델(typed)일 때만 적용 — 아파트/단독/연립 구분")
        if st.button("🏗 생성 (Neuro-Symbolic)"):
            from plan2graph import text2graph as _t2g, gen_loop as _gl

            @st.cache_resource(show_spinner="생성기 로드(CPU)...")
            def _load_ng(_path):
                from plan2graph import generators as _G
                return _G.load(_path)   # arch 디스패치(set-transformer-v2 / typed …)

            try:
                prog = _t2g.parse(gtext)["program"]
                st.caption(f"제약 program: {prog}")
                ng = _load_ng(str(_ROOT / "runs" / gck / "checkpoint.pt"))
                _typed = getattr(ng, "arch", "") == "set-transformer-typed"
                st.caption(f"생성기: {gck}  ·  주거형태조건: "
                           f"{ght if (_typed and ght != '자동') else '미적용(비-typed 또는 자동)'}")

                def gfn(p, r):
                    return (ng.generate(p, r, house_type=ght)
                            if (_typed and ght != "자동") else ng.generate(p, r))
                _sd = int(gseed)
                G_off, _ = _gl.generate_compliant(gfn, prog, max_tries=1, repair=False, seed=_sd)
                v_off = _gl.verify(G_off)
                G_on, hist = _gl.generate_compliant(gfn, prog, max_tries=5, repair=True, seed=_sd)
                v_on = _gl.verify(G_on)
                st.markdown("**패널2 — 파이프라인 토글 (자기교정 OFF ∥ ON, 같은 입력)**")
                p1, p2 = st.columns(2)
                with p1:
                    st.markdown(f"자기교정 **OFF** — 위반 {v_off['n']} "
                                f"{'✅' if v_off['passed'] else '❌'}")
                    st.pyplot(_rv.render_graph_fig(G_off, title="loop off", node_size=1500,
                              font_size=11, layout="kamada"), use_container_width=True)
                with p2:
                    st.markdown(f"자기교정 **ON** — 위반 {v_on['n']} "
                                f"{'✅통과' if v_on['passed'] else '❌'}")
                    st.pyplot(_rv.render_graph_fig(G_on, title="loop on", node_size=1500,
                              font_size=11, layout="kamada"), use_container_width=True)
                st.markdown("**패널3 — Symbolic 근거 (자기교정 로그)**")
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
