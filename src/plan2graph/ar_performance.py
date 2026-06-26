"""KorPlan-AR 성능 비교 GUI (admin.py '📈 AR 성능 비교').

라이브 소스:
  results_report.md          — RPLAN ep곡선 + 한국 매트릭스
  results_roomperm_rplan.md  — RPLAN room-perm ep곡선
  results_korean_gated_curve.md — 한국 gated ep곡선 (오케스트레이터가 작성)
  results_rplan_grid256_curve.md — RPLAN grid256 ep곡선 (오케스트레이터가 작성)
  docs/runs/ar_stats.json    — 파라미터·토큰화·GT·피크 (정적)
학습 진행 중이면 있는 것만 그린다(graceful).
"""
from __future__ import annotations
import json
import os
import re

import streamlit as st


def _read(path):
    try:
        return open(path, encoding="utf-8").read()
    except Exception:
        return ""


def _parse_curve(text, label_filter=None):
    """md 표에서 'ep\\d+' 행 → [(ep, clean)]. label_filter(row)→bool 로 행 선별."""
    pts = []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        if label_filter and not label_filter(line):
            continue
        m_ep = re.search(r"ep\s*(\d+)", line)
        m_cl = re.search(r"\*\*(\d+)%\*\*", line) or re.search(r"\|\s*(\d+)%", line)
        if m_ep and m_cl:
            pts.append((int(m_ep.group(1)), int(m_cl.group(1))))
    # ep 중복 제거(마지막 우선), 정렬
    d = {}
    for ep, cl in pts:
        d[ep] = cl
    return sorted(d.items())


def _peak(pts):
    if not pts:
        return None
    return max(pts, key=lambda x: x[1])


def _section_curve(text, header_token):
    """'## ... <header_token> ...' 헤더 다음의 표만 파싱(섹션별 곡선 분리용)."""
    grab, rows = False, []
    for ln in text.splitlines():
        if ln.strip().startswith("## "):
            grab = header_token in ln
            continue
        if grab and ln.strip().startswith("|"):
            rows.append(ln)
    return _parse_curve("\n".join(rows))


def render(root="."):
    st.title("📈 KorPlan-AR 성능 비교")
    st.caption("clean 곡선·천장(피크)·토큰화 과정·학습 과정 데이터. 학습 진행 중이면 곡선이 자동 갱신됩니다.")

    stats = {}
    sp = os.path.join(root, "docs/runs/ar_stats.json")
    if os.path.exists(sp):
        try:
            stats = json.load(open(sp, encoding="utf-8"))
        except Exception:
            stats = {}

    # ════ 용어 설명 (표준 vs 우리정의) ════
    gl = stats.get("glossary", [])
    if gl:
        with st.expander("📖 용어 설명 — 표준 / 선행연구인용 / 우리정의 / 외부 (먼저 읽기)", expanded=True):
            st.caption("실험 비참여 전문가도 이해하도록 용어 출처를 구분. ⚠️ 논문에 한 번 등장 ≠ 표준.")
            leg = stats.get("glossary_legend")
            if leg:
                st.info(leg)
            st.table([{"용어": g["term"], "구분": g["type"], "뜻": g["def"]} for g in gl])

    # ════ 0. 실험 흐름 (데이터 중심) ════
    fr = stats.get("framing", {})
    if fr:
        st.subheader("0. 실험 흐름 — 무엇을 바꿨고, 의미 있었나")
        st.success("**" + fr.get("headline", "") + "**")
        for pt in fr.get("points", []):
            st.markdown("- " + pt)
    exps = stats.get("experiments", [])
    if exps:
        badge = {"의미있음": "✅ 의미있음", "부분적": "🟡 부분적",
                 "진행중": "🔄 진행중", "의미없음": "⚪ 의미없음"}
        # 요약 표 (한눈에)
        st.markdown("**실험 요약** — 12개 실험의 질문·결과·판정 (상세는 아래 펼침)")
        st.table([{"#": e.get("id"), "실험": e.get("title"),
                   "질문(가설)": e.get("question"),
                   "판정": badge.get(e.get("verdict"), e.get("verdict"))} for e in exps])
        st.caption("판정: ✅의미있음 · 🟡부분적(천장X·연결/재현O) · 🔄진행중 · ⚪의미없음")
        # 실험별 상세 (동기·방법·before→after·함의)
        st.markdown("**실험 상세** — 각 실험을 펼쳐 동기·방법·결과·함의 확인")
        for e in exps:
            bd = badge.get(e.get("verdict"), e.get("verdict"))
            with st.expander(f"#{e.get('id')} {e.get('title')}  —  {bd}"):
                st.markdown(f"**질문(가설):** {e.get('question', '')}")
                if e.get("동기"):
                    st.markdown(f"**🎯 동기 (왜 했나):** {e['동기']}")
                if e.get("방법"):
                    st.markdown(f"**🔬 방법 (어떻게):** {e['방법']}")
                cba, caf = st.columns(2)
                cba.markdown(f"**before**\n\n{e.get('before', '')}")
                caf.markdown(f"**after**\n\n{e.get('after', '')}")
                if e.get("함의"):
                    st.success(f"**💡 함의 (무엇을 결정했나):** {e['함의']}")
                if e.get("note"):
                    st.caption("메모: " + e["note"])
        st.divider()

    # ── 곡선 수집 (strict=도면답게 우선, 없으면 loose 폴백) ──
    # strict 곡선 = clean(drawable): +대각선0 +꼭짓점>=4 +외곽(1덩어리·채움·볼록). loose=옛 selfint·overlap·span만.
    rep = _read(os.path.join(root, "results_report.md"))
    rperm = _read(os.path.join(root, "results_roomperm_rplan.md"))
    kgat = _read(os.path.join(root, "results_korean_gated_curve.md"))
    rg256 = _read(os.path.join(root, "results_rplan_grid256_curve.md"))
    ftb = _read(os.path.join(root, "results_korean_g256_ftbase_curve.md"))
    ftb100 = _read(os.path.join(root, "results_korean_g256_ftbase_b100_curve.md"))

    curves = {}
    _flag = {"strict": False}

    def _pref(strict_file, loose_text, loose_filter=None, section=None):
        st = _read(os.path.join(root, strict_file))
        if st.strip():
            return (_section_curve(st, section) if section else _parse_curve(st)), True
        return (_section_curve(loose_text, section) if section
                else _parse_curve(loose_text, loose_filter)), False

    def _addc(base, tup):
        c, isstrict = tup
        if c:
            curves[base + (" ✦strict" if isstrict else " (loose)")] = c
            if isstrict:
                _flag["strict"] = True

    _addc("RPLAN grid128 no-perm", _pref("results_rplan_strict.md", rep, lambda l: "RPLAN ep" in l))
    _addc("RPLAN room-perm+seed42", _pref("results_roomperm_rplan_strict.md", rperm, lambda l: "room-perm+seed ep" in l))
    _addc("한국 gated FT (production)", _pref("results_korean_gated_strict.md", kgat))
    _addc("RPLAN grid256 (rBoundary)", _pref("results_rplan_grid256_strict.md", rg256))
    _addc("한국 grid256 FT base ep90·플래토", _pref("results_korean_g256_ftbase_strict.md", ftb, section="ep90"))
    _addc("한국 grid256 FT base ep100·플래토", _pref("results_korean_g256_ftbase_b100_strict.md", ftb100, section="ep100"))
    _addc("한국 grid256 FT base ep110·피크", _pref("results_korean_g256_ftbase_strict.md", ftb, section="ep110"))

    # 잠정(가정) 곡선 — 실데이터 곡선이 아직 없을 때만 추가(점선 표시). 실데이터 생기면 자동 우선.
    proj = stats.get("projected", {})
    has_kor = any("gated" in k for k in curves)
    has_g256 = any("grid256" in k for k in curves)
    for pname, ppts in (proj.get("curves") or {}).items():
        if ("gated" in pname and not has_kor) or ("grid256" in pname and not has_g256):
            curves[pname] = [(int(a), int(b)) for a, b in ppts]

    # ════ 평가 방법론 ════
    mth = stats.get("methodology", {})
    if mth:
        st.subheader("📐 평가 방법론 — 왜 지표를 다르게 가져갔나 (객관성)")
        if mth.get("intro"):
            st.caption(mth["intro"])
        if mth.get("why_not_identical"):
            st.markdown("**왜 타 논문과 동일 지표를 안 쓰나** (기존 → 우리)")
            st.table([{"쟁점": r["점"], "기존(FMLM 등)": r["기존"], "우리": r["우리"]}
                      for r in mth["why_not_identical"]])
        if mth.get("why_composite_clean"):
            st.markdown("**왜 overlap 단독이 아니라 clean 복합인가** (독립 실패모드별)")
            st.table([{"실패 모드": r["실패모드"], "clean이 잡는 항": r["잡는 항"],
                       "overlap 단독 한계": r["overlap단독_한계"]}
                      for r in mth["why_composite_clean"]])
            if mth.get("clean_evidence"):
                st.warning(mth["clean_evidence"])
        me = stats.get("metric_evolution", {})
        if me.get("stages"):
            st.markdown("**지표 진화 — loose → strict(도면답게) → +repair**")
            if me.get("intro"):
                st.caption(me["intro"])
            st.table([{"단계": s["단계"], "조건": s["조건"], "한계/의미": s["한계"]} for s in me["stages"]])
        cf = mth.get("clean_formula")
        if mth.get("정의_진화"):
            st.info(mth["정의_진화"])
        if cf:
            st.markdown("**clean 공식 (제안 지표 — 정식 기술)**")
            st.markdown(cf.get("정의", ""))
            st.markdown("_loose 3조건:_")
            for cond in cf.get("조건", []):
                st.markdown("- " + cond)
            if cf.get("strict확장"):
                st.markdown("_★strict 추가 3조건 (도면답게):_")
                for cond in cf["strict확장"]:
                    st.markdown("- " + cond)
            if cf.get("한국확장"):
                st.markdown("**한국 확장(연결-clean):** " + cf["한국확장"])
            if cf.get("표기"):
                st.caption(cf["표기"])
        if mth.get("comparison"):
            st.markdown("**기존 vs 우리 — 평가 설계 비교**")
            st.table(mth["comparison"])
        if mth.get("objectivity"):
            st.markdown("**객관성 확보 방법**")
            for o in mth["objectivity"]:
                st.markdown("- " + o)
        st.divider()

    # ════ 1. 성능 곡선 + 피크 ════
    st.subheader("1. clean 곡선 & 천장(피크)")
    if _flag["strict"]:
        st.success("✦ **곡선 = strict clean(도면답게)** 기준. 옛 loose(selfint·overlap·span만)는 사선·틈·뭉개짐을 "
                   "통과시켜 품질을 과대평가했음(육안 확인). strict = +대각선0 +꼭짓점≥4 +외곽(1덩어리·채움·볼록). "
                   "예: 한국 production loose 65% → **strict 54%**.")
    else:
        st.caption("⏳ strict 재평가(clean drawable) 진행 중이면 끝난 곡선부터 strict로 표시됩니다(없으면 loose 폴백).")
    if curves:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(9, 4.5))
            for name, pts in curves.items():
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                ls = "--" if "잠정" in name else "-"
                ax.plot(xs, ys, marker="o", linestyle=ls, label=name)
                pk = _peak(pts)
                if pk:
                    ax.annotate(f"{pk[1]}%@ep{pk[0]}", xy=pk, xytext=(0, 8),
                                textcoords="offset points", fontsize=8, ha="center")
            ax.set_xlabel("epoch"); ax.set_ylabel("clean (%)")
            ax.set_title("KorPlan-AR clean(strict) vs epoch")
            ax.grid(True, alpha=0.3); ax.legend(fontsize=8, loc="best")
            st.pyplot(fig)
        except Exception as e:  # noqa: BLE001
            st.warning(f"차트 렌더 실패: {e}")
            for name, pts in curves.items():
                st.line_chart({name: dict(pts)})
        # 피크 요약 표
        st.markdown("**천장(피크) 요약**")
        rows = []
        for name, pts in curves.items():
            pk = _peak(pts)
            tag = " (잠정)" if "잠정" in name else ""
            rows.append({"모델": name, "피크 clean": f"{pk[1]}%{tag}", "피크 ep": pk[0],
                         "최종 ep": pts[-1][0], "최종 clean": f"{pts[-1][1]}%"})
        st.table(rows)
        if any("잠정" in k for k in curves):
            st.caption("⚠️ 점선=잠정(가정) 추세. " + stats.get("projected", {}).get("note", ""))
    else:
        st.info("아직 곡선 데이터 없음(학습/평가 진행 중). results_*.md 생성되면 표시됩니다.")

    # ── 생성 샘플 montage (육안 비교) ──
    mtg = stats.get("montages", [])
    if mtg:
        st.markdown("**생성 샘플 육안 (clean 도면 · 동일 seed42·n60·constrained+orthogonal)**")
        existing = [m for m in mtg if os.path.exists(os.path.join(root, m.get("path", "")))]
        if existing:
            cols = st.columns(min(3, len(existing)))
            for i, m in enumerate(existing):
                col = cols[i % len(cols)]
                col.image(os.path.join(root, m["path"]), caption=m.get("caption", ""),
                          use_container_width=True)
        if stats.get("montage_note"):
            st.caption(stats["montage_note"])

    # ── 1.5 지표 종합 (loose vs strict vs +repair, 모델비교, 파이프라인) ──
    mc = stats.get("model_compare", {})
    if mc.get("rows"):
        st.subheader("1.5 모델별 종합 — loose vs strict(도면답게) vs +repair")
        st.table([{"모델": r.get("모델"), "GT": r.get("GT"), "loose": r.get("loose"),
                   "★strict": r.get("strict"), "+repair": r.get("+repair"),
                   "single": r.get("single"), "ep": r.get("ep")} for r in mc["rows"]])
        if mc.get("note"):
            st.caption(mc["note"])
    mp = stats.get("metric_pipeline", {})
    if mp.get("rows"):
        st.markdown("**생성 품질 파이프라인 (production: GT→strict→+repair→best-of-N)**")
        st.table(mp["rows"])
        if mp.get("note"):
            st.caption(mp["note"])
    re_ = stats.get("repair_effect", {})
    if re_.get("rows"):
        st.markdown("**출력 repair 전/후 (같은 공식 before→after)**")
        st.table(re_["rows"])
        if re_.get("note"):
            st.caption(re_["note"])
    fab = stats.get("ft_base_ablation", {})
    if fab.get("rows"):
        st.markdown("**FT 베이스 ablation — 한국 grid256를 RPLAN 어느 ep에서 FT? (3종 학습·비교)**")
        st.table(fab["rows"])
        if fab.get("note"):
            st.caption(fab["note"])
    # 옛 gt_vs_gen 폴백(있으면)
    gvg = stats.get("gt_vs_gen", {})
    if gvg.get("rows"):
        st.markdown("**GT clean vs 생성 clean**")
        st.table(gvg["rows"])

    # ════ 2. 파라미터 선정 (데이터 근거) ════
    st.subheader("2. 파라미터 선정 (데이터 근거)")
    p = stats.get("params", {})
    if p:
        cols = st.columns(3)
        cols[0].markdown("**아키텍처**"); cols[0].json(p.get("architecture", {}))
        cols[1].markdown("**학습**"); cols[1].json(p.get("training", {}))
        cols[2].markdown("**표현**"); cols[2].json(p.get("representation", {}))
        st.markdown("**선정 근거**")
        st.table([{"항목": k, "근거": v} for k, v in p.get("rationale", {}).items()])

    # ════ 3. 토큰화 과정 데이터 ════
    st.subheader("3. 토큰화 과정 데이터")
    tk = stats.get("tokenization", {})
    if tk.get("korean_gate_funnel"):
        st.markdown("**한국 게이트 퍼널** (Parsed → 토큰화 한정)")
        st.table(tk["korean_gate_funnel"])
        if tk.get("line_note"):
            st.warning(tk["line_note"])
    if tk.get("korean_gt_clean"):
        st.markdown("**한국 GT clean 개선** (ungated → 코덱수정 → gated)")
        st.table(tk["korean_gt_clean"])
        ref = tk.get("gt_clean_reference", {})
        if ref:
            st.caption("참고 GT clean: RPLAN %s%% · 한국 nosnap %s%% · 한국 snap %s%%" % (
                ref.get("RPLAN_GT"), ref.get("한국_nosnap_GT"), ref.get("한국_snap_GT")))
    if tk.get("codec_fixes"):
        st.markdown("**코덱/표현 수정 효과**")
        st.table(tk["codec_fixes"])
    if tk.get("selfint_diagnosis"):
        st.markdown("**selfint 원인 진단**")
        st.json(tk["selfint_diagnosis"])

    # ════ 4. 학습 과정 데이터 (한 표: ep × 모델 strict clean + loss) ════
    st.subheader("4. 학습 과정 데이터 — 전 모델 한 표 (ep별 strict clean)")
    if curves:
        def _short(n):
            return (n.replace(" ✦strict", "").replace(" (loose)", "")
                    .replace("한국 ", "").replace("RPLAN ", "R-").replace("grid", "g")
                    .replace(" FT base ", " base").replace("·플래토", "").replace("·피크", ""))
        names = list(curves.keys())
        dmap = {n: dict(pts) for n, pts in curves.items()}
        all_eps = sorted({ep for pts in curves.values() for ep, _ in pts})
        rows = []
        for ep in all_eps:
            row = {"ep": ep}
            for n in names:
                v = dmap[n].get(ep)
                row[_short(n)] = (f"{v}%" if v is not None else "·")
            rows.append(row)
        st.table(rows)
        st.caption("빈칸(·)=해당 ep 미학습. 한국 FT=korean-ep, RPLAN=자체 ep. strict clean(도면답게). 모델별 그래프는 §1 곡선.")
        st.info("**한국 grid256 FT는 3종 모두 학습·표시**(base ep90·100·110). ★선택=**base ep90**(strict 56% 최고 > base110 50% > base100 48%) — "
                "RPLAN 피크(ep110)에서 FT하면 과특화로 전이 천장 낮아짐(§0 실험11). 도면생성 콤보엔 우승 base90만 넣음. "
                "⚠️ '한국 단독(옛 ungated)'은 ep50 단일 ckpt라 학습곡선 없음(단일점, §1.5 표의 측정값 참조).")
    # loss 곡선(로그 파싱)
    for logf, lab in [("logs_ar_k_gated_ft.log", "한국 gated"),
                      ("logs_ar_r_rb256.log", "RPLAN grid256"),
                      ("logs_ar_k_g256_ftR_b90.log", "한국 g256 FT base ep90"),
                      ("logs_ar_k_g256_ftR_b110.log", "한국 g256 FT base ep110")]:
        t = _read(os.path.join(root, logf))
        losses = re.findall(r"ep\s*(\d+)\s+loss\s+([\d.]+)", t)
        if losses:
            d = {int(e): float(l) for e, l in losses}
            st.markdown(f"**{lab} loss**")
            st.line_chart({"loss": dict(sorted(d.items()))})

    # ════ 5. 핵심 발견 ════
    st.subheader("5. 핵심 발견")
    for f in stats.get("key_findings", []):
        st.markdown(f"- {f}")

    # ════ 6. 잠정 결론 & 최종 선정 ════
    st.subheader("6. 가설별 결론 & 최종 선정")
    conc = stats.get("conclusions", [])
    if conc:
        st.markdown("**가설 → 상태 → 결론** (확정 / 잠정 / 검증중)")
        badge = {"확정": "✅ 확정", "기각": "❌ 기각"}
        st.table([{"가설": c.get("가설"),
                   "상태": badge.get(c.get("상태"), "🔄 " + str(c.get("상태"))),
                   "결론": c.get("결론")} for c in conc])
    prod = stats.get("production", {})
    if prod:
        st.success("**최종 선정 (도면 생성에 실제 사용)** — " + prod.get("원칙", ""))
        st.json(prod.get("최종_선정_구성", {}))
        if prod.get("비고"):
            st.caption(prod["비고"])
    nxt = stats.get("next_plan", {})
    if nxt:
        st.info("**다음 학습 계획 — " + nxt.get("목표", "") + "**  (" + nxt.get("상태", "") + ")")
        for r in nxt.get("권장", []):
            st.markdown("- " + r)

    if stats.get("updated"):
        st.caption(f"stats 갱신: {stats['updated']} · 잠정치는 최종 학습결과로 자동/수동 교체")
