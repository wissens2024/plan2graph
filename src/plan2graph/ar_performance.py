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
        rows = []
        for e in exps:
            rows.append({"#": e.get("id"), "실험": e.get("title"),
                         "질문(가설)": e.get("question"),
                         "before": e.get("before"), "after": e.get("after"),
                         "판정": badge.get(e.get("verdict"), e.get("verdict")),
                         "메모": e.get("note")})
        st.table(rows)
        st.caption("판정: ✅의미있음 · 🟡부분적(천장X·연결/재현O) · 🔄진행중 · ⚪의미없음")
        st.divider()

    # ── 곡선 수집 ──
    rep = _read(os.path.join(root, "results_report.md"))
    rperm = _read(os.path.join(root, "results_roomperm_rplan.md"))
    kgat = _read(os.path.join(root, "results_korean_gated_curve.md"))
    rg256 = _read(os.path.join(root, "results_rplan_grid256_curve.md"))

    curves = {}
    c = _parse_curve(rep, lambda l: "RPLAN ep" in l)
    if c:
        curves["RPLAN (grid128, no-perm)"] = c
    c = _parse_curve(rperm, lambda l: "room-perm+seed ep" in l)
    if c:
        curves["RPLAN room-perm+seed42"] = c
    c = _parse_curve(kgat)
    if c:
        curves["한국 gated FT (snap+tol3.5)"] = c
    c = _parse_curve(rg256)
    if c:
        curves["RPLAN grid256 (rBoundary)"] = c

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
        cf = mth.get("clean_formula")
        if cf:
            st.markdown("**clean 공식 (제안 지표 — 정식 기술)**")
            st.markdown(cf.get("정의", ""))
            for cond in cf.get("조건", []):
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

    # ── GT clean(데이터 천장) vs 생성 clean(모델 피크) ──
    gvg = stats.get("gt_vs_gen", {})
    if gvg.get("rows"):
        st.markdown("**GT clean(데이터 천장) vs 생성 clean(모델 피크)**")
        st.table(gvg["rows"])
        if gvg.get("note"):
            st.caption(gvg["note"])

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

    # ════ 4. 학습 과정 데이터 (per-epoch 곡선 표 + loss) ════
    st.subheader("4. 학습 과정 데이터")
    if curves:
        for name, pts in curves.items():
            with st.expander(f"{name} — per-epoch clean ({len(pts)} pts)"):
                st.table([{"ep": ep, "clean": f"{cl}%"} for ep, cl in pts])
    # loss 곡선(로그 파싱)
    for logf, lab in [("logs_ar_k_gated_ft.log", "한국 gated"),
                      ("logs_ar_r_rb256.log", "RPLAN grid256")]:
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
        badge = {"확정": "✅ 확정"}
        st.table([{"가설": c.get("가설"),
                   "상태": badge.get(c.get("상태"), "🔄 " + str(c.get("상태"))),
                   "결론": c.get("결론")} for c in conc])
    prod = stats.get("production", {})
    if prod:
        st.success("**최종 선정 (도면 생성에 실제 사용)** — " + prod.get("원칙", ""))
        st.json(prod.get("최종_선정_구성", {}))
        if prod.get("비고"):
            st.caption(prod["비고"])

    if stats.get("updated"):
        st.caption(f"stats 갱신: {stats['updated']} · 잠정치는 최종 학습결과로 자동/수동 교체")
