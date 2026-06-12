#!/usr/bin/env python3
"""make_ppt.py — 논문 중간 발표자료(.pptx) 생성. 실제 측정값·구축과정 기반.

실행: python scripts/make_ppt.py [out.pptx]
"""
import sys
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE

# ── 팔레트 ──
INDIGO = RGBColor(0x4F, 0x46, 0xE5)
DARK = RGBColor(0x1F, 0x29, 0x37)
GRAY = RGBColor(0x6B, 0x72, 0x80)
LIGHT = RGBColor(0xF3, 0xF4, 0xF6)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
GREEN = RGBColor(0x05, 0x96, 0x69)
AMBER = RGBColor(0xD9, 0x77, 0x06)
RED = RGBColor(0xDC, 0x26, 0x26)
BLUE = RGBColor(0x25, 0x63, 0xEB)

EMU_W, EMU_H = Inches(13.333), Inches(7.5)
prs = Presentation()
prs.slide_width = EMU_W
prs.slide_height = EMU_H
BLANK = prs.slide_layouts[6]
FONT = "맑은 고딕"


def _set(tf_run, size, color=DARK, bold=False, font=FONT):
    tf_run.font.size = Pt(size)
    tf_run.font.color.rgb = color
    tf_run.font.bold = bold
    tf_run.font.name = font


def box(slide, x, y, w, h, fill=None, line=None, round_=False):
    shp = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if round_ else MSO_SHAPE.RECTANGLE,
        x, y, w, h)
    shp.shadow.inherit = False
    if fill is None:
        shp.fill.background()
    else:
        shp.fill.solid()
        shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line
        shp.line.width = Pt(1)
    return shp


def text(slide, x, y, w, h, runs, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
         space=4):
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    for i, r in enumerate(runs):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(space)
        if isinstance(r, tuple):
            txt, kw = r[0], r[1]
        else:
            txt, kw = r, {}
        p.level = kw.get("level", 0)
        run = p.add_run()
        run.text = ("•  " if kw.get("bullet") else "") + txt
        _set(run, kw.get("size", 16), kw.get("color", DARK), kw.get("bold", False))
    return tb


def header(slide, idx, title, kicker=None):
    box(slide, 0, 0, EMU_W, Inches(1.15), fill=WHITE)
    box(slide, 0, Inches(1.15), EMU_W, Pt(3), fill=INDIGO)
    box(slide, 0, 0, Inches(0.18), Inches(1.15), fill=INDIGO)
    if kicker:
        text(slide, Inches(0.55), Inches(0.18), Inches(11), Inches(0.3),
             [(kicker, {"size": 12, "color": INDIGO, "bold": True})])
    text(slide, Inches(0.55), Inches(0.42), Inches(12), Inches(0.7),
         [(title, {"size": 26, "color": DARK, "bold": True})])
    text(slide, Inches(12.4), Inches(0.45), Inches(0.7), Inches(0.4),
         [(str(len(prs.slides._sldIdLst)), {"size": 14, "color": GRAY})], align=PP_ALIGN.RIGHT)


def slide():
    s = prs.slides.add_slide(BLANK)
    box(s, 0, 0, EMU_W, EMU_H, fill=WHITE)
    return s


def img(slide, path, x, y, w=None, h=None):
    return slide.shapes.add_picture(path, x, y, width=w, height=h)


def chip(slide, x, y, w, label, value, color):
    box(slide, x, y, w, Inches(1.15), fill=LIGHT, round_=True)
    box(slide, x, y, Inches(0.1), Inches(1.15), fill=color)
    text(slide, x + Inches(0.25), y + Inches(0.12), w - Inches(0.3), Inches(0.4),
         [(label, {"size": 13, "color": GRAY, "bold": True})])
    text(slide, x + Inches(0.25), y + Inches(0.45), w - Inches(0.3), Inches(0.6),
         [(value, {"size": 26, "color": color, "bold": True})])


def table(slide, x, y, w, rows, colw, header_fill=INDIGO, fs=13):
    nr, nc = len(rows), len(rows[0])
    h = Inches(0.42) * nr
    tbl = slide.shapes.add_table(nr, nc, x, y, w, h).table
    for j, cw in enumerate(colw):
        tbl.columns[j].width = cw
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            c = tbl.cell(i, j)
            c.margin_top = c.margin_bottom = Pt(2)
            c.margin_left = c.margin_right = Pt(6)
            c.vertical_anchor = MSO_ANCHOR.MIDDLE
            c.fill.solid()
            c.fill.fore_color.rgb = header_fill if i == 0 else (
                WHITE if i % 2 else LIGHT)
            p = c.text_frame.paragraphs[0]
            p.alignment = PP_ALIGN.CENTER if j > 0 else PP_ALIGN.LEFT
            r = p.add_run()
            r.text = str(val)
            _set(r, fs, WHITE if i == 0 else DARK, bold=(i == 0))
    return tbl


def flow(slide, x, y, steps, w_each=Inches(1.5), color=INDIGO):
    cx = x
    for i, st in enumerate(steps):
        b = box(slide, cx, y, w_each, Inches(0.95), fill=LIGHT, line=color, round_=True)
        tf = b.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        for k, ln in enumerate(st.split("\n")):
            pp = p if k == 0 else tf.add_paragraph()
            pp.alignment = PP_ALIGN.CENTER
            rr = pp.add_run()
            rr.text = ln
            _set(rr, 11, DARK, bold=(k == 0))
        cx = cx + w_each
        if i < len(steps) - 1:
            ar = slide.shapes.add_textbox(cx, y, Inches(0.32), Inches(0.95))
            ar.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
            pa = ar.text_frame.paragraphs[0]
            pa.alignment = PP_ALIGN.CENTER
            rn = pa.add_run()
            rn.text = "→"
            _set(rn, 18, color, bold=True)
            cx = cx + Inches(0.32)


# ════════════════════════════════════════════════════════════════════════════
# 1. 표지
s = slide()
box(s, 0, 0, EMU_W, EMU_H, fill=DARK)
box(s, 0, Inches(4.55), EMU_W, Pt(4), fill=INDIGO)
text(s, Inches(0.9), Inches(2.1), Inches(11.5), Inches(2),
     [("한국형 소버린 아파트 평면도 생성 AI", {"size": 40, "color": WHITE, "bold": True}),
      ("법규-인식 · 완전도면(문·창·기구·치수) · 한국 데이터 주권", {"size": 18, "color": RGBColor(0xC7, 0xD2, 0xFE)})],
     space=14)
text(s, Inches(0.9), Inches(4.8), Inches(11.5), Inches(1.5),
     [("논문 중간 발표 — 데이터셋 구축 · 파이프라인 · AI 모델 · 성능", {"size": 16, "color": RGBColor(0x9C, 0xA3, 0xAF)}),
      ("plan2graph · 2026", {"size": 13, "color": GRAY})], space=8)

# ════════════════════════════════════════════════════════════════════════════
# 2. 연구 배경·목표
s = slide()
header(s, 2, "연구 배경 · 목표", "BACKGROUND")
text(s, Inches(0.55), Inches(1.45), Inches(12.2), Inches(1),
     [("건축가가 실제로 쓸 수 있는 “완전한” 한국 아파트 평면도를 생성하는 AI.", {"size": 18, "bold": True}),
      ("기존 SOTA(RPLAN 류)는 벡터 방·벽 레이아웃까지만 — 문·창·기구·치수·법규는 다루지 않음.",
       {"size": 14, "color": GRAY})], space=8)
chip(s, Inches(0.55), Inches(3.0), Inches(3.9), "산출물 ①", "생성형 도면(이미지)", INDIGO)
chip(s, Inches(4.7), Inches(3.0), Inches(3.9), "산출물 ②", "AutoCAD DXF", BLUE)
chip(s, Inches(8.85), Inches(3.0), Inches(3.9), "방식", "generate→verify→correct", GREEN)
text(s, Inches(0.55), Inches(4.5), Inches(12.2), Inches(2.2),
     [("핵심 차별성(Novelty) — 논문 기여", {"size": 16, "bold": True, "color": INDIGO}),
      ("① 법규-인식 생성: 한국 건축법규(채광 §17①·환기·피난·면적)를 생성에 강제 (필드 최초 시도)", {"size": 15, "bullet": True}),
      ("② 완전 도면: 레이아웃을 넘어 문·창·기구·치수까지 채운 작도 가능 도면", {"size": 15, "bullet": True}),
      ("③ 한국 소버린: 한국 아파트 데이터로 학습한 자체 소유 엔진(외부 모델 단순 호출 아님)", {"size": 15, "bullet": True})], space=8)

# ════════════════════════════════════════════════════════════════════════════
# 3. 전체 흐름
s = slide()
header(s, 3, "전체 파이프라인", "OVERVIEW")
flow(s, Inches(0.5), Inches(1.7),
     ["데이터\n3개 출처", "통일 그래프\n(g-0.3)", "위상 모델\n(방 연결)", "기하 모델\nT / G", "neuro-symbolic\n보정·법규", "렌더\n이미지+DXF"],
     w_each=Inches(1.72))
text(s, Inches(0.55), Inches(3.0), Inches(12.2), Inches(0.6),
     [("T·G = “같은 파이프라인, 기하모델만 다른” A/B 비교 (ADR-0007)", {"size": 14, "color": INDIGO, "bold": True})])
table(s, Inches(0.55), Inches(3.6), Inches(12.2),
      [["축", "T-라인 (baseline)", "G-라인 (소버린)"],
       ["기하모델", "약한 박스 (“위상+약한기하=못그림” 증명)", "강한 diffusion 엔진 (자체)"],
       ["데이터", "자동 추출 그래프", "+ 사람(알바) SVG 보정"],
       ["역할", "비교 기준선", "본 제품"]],
      [Inches(2.2), Inches(5.5), Inches(4.5)])
text(s, Inches(0.55), Inches(6.4), Inches(12.2), Inches(0.6),
     [("비교 지표: FID · 법규준수율 · 완성도 — 기하모델 효과를 격리해 정량 비교", {"size": 13, "color": GRAY})])

# ════════════════════════════════════════════════════════════════════════════
# (신규) 핵심 숫자 한눈에
s = slide()
header(s, 0, "핵심 숫자 한눈에", "AT A GLANCE")
_cw, _gap, _x0 = Inches(3.95), Inches(0.2), Inches(0.55)
chip(s, _x0, Inches(1.7), _cw, "받은 원본 (3출처)", "129,007", DARK)
chip(s, _x0 + _cw + _gap, Inches(1.7), _cw, "사용 가능", "94,320", GREEN)
chip(s, _x0 + 2 * (_cw + _gap), Inches(1.7), _cw, "온전(AI-Hub APT)", "24,706 · 64.7%", INDIGO)
chip(s, _x0, Inches(3.2), _cw, "엔진 역할 / 방", "13 / 18", BLUE)
chip(s, _x0 + _cw + _gap, Inches(3.2), _cw, "DiffPlanner RPLAN FID", "1.23", GREEN)
chip(s, _x0 + 2 * (_cw + _gap), Inches(3.2), _cw, "통일 그래프 (G)", "40,495 세대", DARK)
text(s, Inches(0.55), Inches(4.9), Inches(12.2), Inches(1.9),
     [("데이터 129,007 → 통일 그래프 40,495세대 → 자체 엔진(13역할/18방) → 완전 도면+DXF",
       {"size": 17, "bold": True, "color": DARK}),
      ("박스 회귀 사망(정답 99% 겹침) → diffusion 엔진 전환 · neuro-symbolic으로 완전성 확보",
       {"size": 14, "color": GRAY})], space=10)

# ════════════════════════════════════════════════════════════════════════════
# 4. 데이터 출처·규모
s = slide()
header(s, 4, "데이터셋 구축 ① — 출처 · 규모", "DATASET")
text(s, Inches(0.55), Inches(1.4), Inches(12), Inches(0.5),
     [("3개 공개 데이터셋을 하나의 통일 그래프 규격으로 흡수", {"size": 16, "bold": True})])
table(s, Inches(0.55), Inches(2.1), Inches(12.2),
      [["출처", "받은 원본", "성격", "용도"],
       ["AI-Hub (한국)", "43,219", "한국 아파트·단독·연립 평면도", "소버린 파인튜닝 (핵심)"],
       ["RPLAN", "80,788", "대규모 벡터 평면도", "글로벌 사전학습"],
       ["CubiCasa5k", "5,000", "실측 벡터 평면도", "글로벌 사전학습"],
       ["합계", "129,007", "— ", "— "]],
      [Inches(3.0), Inches(2.3), Inches(4.4), Inches(2.5)])
chip(s, Inches(0.55), Inches(4.9), Inches(3.9), "받은 원본 합", "129,007", DARK)
chip(s, Inches(4.7), Inches(4.9), Inches(3.9), "✅ 사용 가능", "94,320", GREEN)
chip(s, Inches(8.85), Inches(4.9), Inches(3.9), "🛠 보정·복구 필요", "11,793", AMBER)
text(s, Inches(0.55), Inches(6.3), Inches(12), Inches(0.5),
     [("제외 22,894 (중복 사본·비-평면도) — 처분(사용/보정필요/제외) 상호배타, 합 = 다운로드 원본", {"size": 12, "color": GRAY})])

# ════════════════════════════════════════════════════════════════════════════
# 5. 데이터 회계
s = slide()
header(s, 5, "데이터셋 구축 ② — 회계(처분)", "DATASET")
text(s, Inches(0.55), Inches(1.4), Inches(12), Inches(0.5),
     [("검수 본질 = 숫자 × 카테고리(사용/보정필요/제외) × 사유 — GUI·코드·문서 단일 소스",
       {"size": 15, "bold": True})])
table(s, Inches(0.55), Inches(2.2), Inches(12.2),
      [["출처", "✅ 사용", "🛠 보정필요", "🚫 제외", "합(=다운로드)"],
       ["AI-Hub", "10,921", "9,412", "22,886", "43,219"],
       ["RPLAN", "80,371", "417", "0", "80,788"],
       ["CubiCasa5k", "3,028", "1,964", "8", "5,000"],
       ["합계", "94,320", "11,793", "22,894", "129,007"]],
      [Inches(3.0), Inches(2.3), Inches(2.3), Inches(2.3), Inches(2.3)])
text(s, Inches(0.55), Inches(5.1), Inches(12.2), Inches(1.8),
     [("핵심 원칙", {"size": 15, "bold": True, "color": INDIGO}),
      ("“보정필요” = 버리는 게 아니라 살릴 수 있는 데이터 (자동 재변환 + 사람 보정으로 → 사용으로 승급)", {"size": 14, "bullet": True}),
      ("AI-Hub는 도면 1장 = 여러 세대 → 도면수 ≠ 세대수 (세대 = 실제 추출된 그래프 수)", {"size": 14, "bullet": True})], space=7)

# ════════════════════════════════════════════════════════════════════════════
# 6. AI-Hub 처리 파이프라인
s = slide()
header(s, 6, "데이터셋 구축 ③ — AI-Hub 처리 파이프라인", "DATASET")
flow(s, Inches(0.5), Inches(1.65),
     ["원본 PNG\n(라벨)", "V2V/YOLO\n검출", "SVG 변환\n(완전기하)", "사람 보정\n(알바)", "build\n통일그래프", "검증\n(위상·역할)"],
     w_each=Inches(1.72))
text(s, Inches(0.55), Inches(3.0), Inches(12.2), Inches(0.5),
     [("provenance(추출 출처)로 데이터 특성·신뢰도 구분 — 선언적 필터로 구성 분리", {"size": 14, "color": INDIGO, "bold": True})])
table(s, Inches(0.55), Inches(3.6), Inches(12.2),
      [["provenance", "의미", "처분"],
       ["dual", "방(SPA)+구조(STR) 둘 다 라벨 — 직접 변환", "✅ 사용 (최고품질)"],
       ["spa_only / str_only", "한쪽만 → V2V(YOLO)로 나머지 복구", "✅ 사용 (복구)"],
       ["objocr", "OBJ/OCR만 (공간 라벨 없음) — 이미지 직접 검출", "🛠 보정필요"]],
      [Inches(3.0), Inches(6.2), Inches(3.0)])
text(s, Inches(0.55), Inches(5.9), Inches(12.2), Inches(0.9),
     [("V2V(vector→vector) YOLO 검출: SPA mAP 0.90+ · 게이트 통과율 기준 평가 (mAP 아님)", {"size": 13, "color": GRAY, "bullet": True}),
      ("G-라인 통일 그래프 빌드 결과: 18,503 도면 / 40,495 세대", {"size": 13, "color": GRAY, "bullet": True})], space=5)

# ════════════════════════════════════════════════════════════════════════════
# 7. 품질 게이트
s = slide()
header(s, 7, "데이터셋 구축 ④ — 품질 게이트(온전/보정필요)", "DATASET")
text(s, Inches(0.55), Inches(1.4), Inches(12.2), Inches(0.6),
     [("“온전한 데이터만 학습” — 구성 기반 자동 분류기(좌표 불필요, 검수·학습 단일 소스)", {"size": 15, "bold": True})])
chip(s, Inches(0.55), Inches(2.2), Inches(5.9), "온전 (사용) — AI-Hub APT 38,203 중", "24,706  (64.7%)", GREEN)
chip(s, Inches(6.85), Inches(2.2), Inches(5.9), "보정필요", "13,485  (35.3%)", AMBER)
text(s, Inches(0.55), Inches(3.7), Inches(12.2), Inches(3),
     [("보정필요 판정 규칙 · 사유 분포 (실측)", {"size": 15, "bold": True, "color": INDIGO}),
      ("현관 ≠ 1 (다세대 병합/누락) — 5,142", {"size": 14, "bullet": True}),
      ("발코니 과다(>4, 라벨 폭주) — 4,904   ·   거실 ≠ 1 — 4,521", {"size": 14, "bullet": True}),
      ("침실 < 화장실(면적 모순) — 3,060   ·   거실 오라벨 — 634   ·   기타 과다 — 527", {"size": 14, "bullet": True}),
      ("→ 온전 데이터 방 수: 중앙값 14 · p95 17 → 엔진 방 수용량 18로 결정", {"size": 14, "bold": True, "color": DARK})], space=8)

# ════════════════════════════════════════════════════════════════════════════
# 8. 데이터셋 구성(variant)
s = slide()
header(s, 8, "데이터셋 구축 ⑤ — 구성(variant)이 곧 실험 변수", "DATASET")
text(s, Inches(0.55), Inches(1.4), Inches(12.2), Inches(0.6),
     [("같은 엔진을 다른 데이터 구성으로 학습 → 다른 모델 → 도면 품질 차이 = 비교의 본체", {"size": 15, "bold": True})])
table(s, Inches(0.55), Inches(2.2), Inches(12.2),
      [["라인", "구성", "세대수", "보정 종류"],
       ["AI-Hub (T)", "dual", "7,465", "자동(재변환) — T·G 공유"],
       ["AI-Hub (T)", "dual + 보정", "23,679", "자동(재변환)"],
       ["AI-Hub (G)", "dual", "8,700", "자동 + 알바(사람 SVG)"],
       ["AI-Hub (G)", "dual + 보정", "27,683", "자동 + 알바"],
       ["글로벌(사전학습)", "RPLAN / CubiCasa", "56,053 / —", "—"]],
      [Inches(3.0), Inches(3.2), Inches(2.5), Inches(3.5)])
text(s, Inches(0.55), Inches(5.6), Inches(12.2), Inches(1.2),
     [("보정 2종: 자동(neuro-symbolic 재변환, 보정필요→사용)은 T·G 공유 · 알바(사람 SVG)는 G에만 추가",
       {"size": 13, "color": GRAY, "bullet": True}),
      ("비교 매트릭스 = 구성 × 기하모델(T/G) × 사전학습(있음/없음)", {"size": 13, "color": GRAY, "bullet": True})], space=6)

# ════════════════════════════════════════════════════════════════════════════
# 9. AI 모델 — 박스회귀 폐기
s = slide()
header(s, 9, "AI 모델 ① — 박스 회귀(폐기): 측정으로 사망 확정", "AI MODEL")
text(s, Inches(0.55), Inches(1.45), Inches(12.2), Inches(0.6),
     [("첫 시도: 위상 그래프 → 방별 박스 좌표 회귀 (Transformer)", {"size": 16, "bold": True})])
chip(s, Inches(0.55), Inches(2.3), Inches(3.9), "정답 박스 겹침", "99%", RED)
chip(s, Inches(4.7), Inches(2.3), Inches(3.9), "데이터 4.6배·겹침손실", "효과 없음", RED)
chip(s, Inches(8.85), Inches(2.3), Inches(3.9), "결론", "표현 한계", RED)
text(s, Inches(0.55), Inches(3.8), Inches(12.2), Inches(2.8),
     [("왜 죽었나 (실측 근거)", {"size": 15, "bold": True, "color": INDIGO}),
      ("정답(ground-truth) 박스부터 99% 겹침 — 모델이 아니라 “박스 표현” 자체의 한계", {"size": 14, "bullet": True}),
      ("데이터 4.6배 증량·겹침 억제 손실 추가에도 동일하게 박스 붕괴", {"size": 14, "bullet": True}),
      ("배치 실현율 평균 62%(방 많을수록 33%) → 문 증발·화장실 고립", {"size": 14, "bullet": True}),
      ("→ 교훈: 위상/모델 문제가 아니라 “기하 실현”이 병목 — 생성형 기하(diffusion)로 전환", {"size": 14, "bold": True, "color": DARK})], space=8)

# ════════════════════════════════════════════════════════════════════════════
# 10. AI 모델 — 소버린 엔진
s = slide()
header(s, 10, "AI 모델 ② — 소버린 엔진(SOTA 부품 조립 + 한국형 확장)", "AI MODEL")
text(s, Inches(0.55), Inches(1.4), Inches(12.2), Inches(0.6),
     [("단일 생성 엔진 — SOTA의 “기법(부품)”만 인용·조립 + 우리 novelty", {"size": 15, "bold": True})])
table(s, Inches(0.55), Inches(2.1), Inches(12.2),
      [["빌린 부품 (인용 base)", "역할"],
       ["DiffPlanner (2025)", "벡터 직접 diffusion 골격 · 경계조건 (코드 보유·검증)"],
       ["GSDiff (AAAI'25)", "정렬 손실 · 자기지도(연결 포인트)"],
       ["HouseDiffusion (CVPR'23)", "이산+연속 디노이즈"],
       ["FMLM (2026)", "유효성 제약(markup)"]],
      [Inches(4.0), Inches(8.2)])
text(s, Inches(0.55), Inches(4.6), Inches(12.2), Inches(2.2),
     [("한국형 확장 (구현 완료·검증)", {"size": 15, "bold": True, "color": INDIGO}),
      ("3-stage(node→adjacency→partitioning) 아키텍처: num_category 6→13(한국 역할) · max_rooms 8→18",
       {"size": 14, "bullet": True}),
      ("RPLAN 사전학습 → 한국 파인튜닝 (전이학습) · 학습 → 샘플 → 도면+DXF 파이프라인 E2E 검증", {"size": 14, "bullet": True})], space=8)

# ════════════════════════════════════════════════════════════════════════════
# 11. neuro-symbolic 완성
s = slide()
header(s, 11, "AI 모델 ③ — neuro-symbolic 완성 (SOTA가 안 하는 부분)", "AI MODEL")
text(s, Inches(0.55), Inches(1.4), Inches(12.2), Inches(0.7),
     [("엔진은 방 레이아웃까지 — 문·창·치수·기구·법규는 “있는 데이터로 의미 추론”해 채움", {"size": 15, "bold": True})])
table(s, Inches(0.55), Inches(2.3), Inches(12.2),
      [["채울 공백", "추론 방법"],
       ["축척(scale)", "표준 여닫이문 폭(800mm) 앵커 → mm/px (절대값은 사람 길이입력 보조)"],
       ["문 방향 / 위치", "방 인접 관계에서 유도 (맞닿은 경계 중점)"],
       ["창문", "거주방의 외곽 접한 벽에 배치"],
       ["기구(침대·소파·싱크…)", "역할별 규칙 카탈로그 (벽에 붙여·문 반대쪽)"],
       ["법규 준수", "rules_legal: 채광 §17① · 환기 · 피난 · 면적 (국가법령 API)"]],
      [Inches(3.4), Inches(8.8)])
text(s, Inches(0.55), Inches(5.7), Inches(12.2), Inches(1),
     [("생성 → 검증(rules_legal) → 보정 → 재생성 루프 — 거친 출력은 학습 개선 신호", {"size": 13, "color": GRAY})])

# ════════════════════════════════════════════════════════════════════════════
# (신규) 완전 도면 예시 (이미지)
s = slide()
header(s, 0, "완전 도면 예시 — neuro-symbolic으로 채운 결과", "RESULTS")
img(s, "docs/figs/complete_drawing.png", Inches(0.4), Inches(1.45), h=Inches(5.65))
text(s, Inches(8.55), Inches(1.6), Inches(4.5), Inches(4.2),
     [("이 도면 1장에 담긴 것", {"size": 16, "bold": True, "color": INDIGO}),
      ("방 + 면적(㎡) · 외벽/내벽", {"size": 14, "bullet": True}),
      ("가구: 침대·소파·TV·식탁·싱크/레인지·냉장고·변기·세면·샤워·신발장", {"size": 14, "bullet": True}),
      ("창(파랑) · 문(빨강) · 치수 12,653 × 9,513 mm", {"size": 14, "bullet": True}),
      ("척도: 여닫이문 폭 800mm 앵커로 추론 (11.9 mm/px)", {"size": 14, "bullet": True}),
      ("같은 도면 → AutoCAD DXF 동시 출력", {"size": 14, "bullet": True, "bold": True, "color": GREEN})],
     space=10)
text(s, Inches(8.55), Inches(5.85), Inches(4.5), Inches(1.4),
     [("SOTA는 방·벽 레이아웃까지 — 우리는 가구·치수·DXF까지 채운 “완전 도면”",
       {"size": 13, "color": GRAY})])

# ════════════════════════════════════════════════════════════════════════════
# 12. 성능·측정
s = slide()
header(s, 12, "성능 · 측정", "RESULTS")
chip(s, Inches(0.55), Inches(1.4), Inches(3.9), "DiffPlanner RPLAN FID", "1.23", GREEN)
chip(s, Inches(4.7), Inches(1.4), Inches(3.9), "clean-fid", "1.34", GREEN)
chip(s, Inches(8.85), Inches(1.4), Inches(3.9), "평가셋", "12,002", DARK)
text(s, Inches(0.55), Inches(2.8), Inches(12.2), Inches(4),
     [("측정으로 확정된 사실 (흔들지 않는 베이스라인)", {"size": 15, "bold": True, "color": INDIGO}),
      ("DiffPlanner 베이스라인 재현·검증: RPLAN FID 1.23(pytorch-fid)/1.34(clean-fid), 12,002 test", {"size": 14, "bullet": True}),
      ("균형 벤치마크(주거형태 매크로평균): 신경망 0.188 > 규칙기반 0.205 — “규칙기반 최강”은 APT 편중 착시", {"size": 14, "bullet": True}),
      ("V2V 확장은 생성 학습엔 역효과: adj_L1 0.074→0.104 · 법규 0.94→0.37 (수량 ≠ 품질)", {"size": 14, "bullet": True}),
      ("SOTA 4편 전부 벡터 방·벽 레이아웃까지 — 창·가구·치수는 아무도 안 함(최신 FMLM 포함)", {"size": 14, "bullet": True}),
      ("평가 축 = FID(품질) + 법규준수율(SOTA≈0) + 완성도", {"size": 14, "bold": True, "color": DARK})], space=9)

# ════════════════════════════════════════════════════════════════════════════
# 13. 시스템·도구
s = slide()
header(s, 13, "시스템 · 도구 (재현 가능한 프로그램)", "SYSTEM")
text(s, Inches(0.55), Inches(1.4), Inches(12.2), Inches(0.6),
     [("결과물 = “내가 돌린 숫자”가 아니라 “비교 가능한 프로그램” (단일 소스 · A/B 한 화면)", {"size": 15, "bold": True})])
table(s, Inches(0.55), Inches(2.2), Inches(12.2),
      [["구성요소", "내용"],
       ["Streamlit 대시보드", "검수·회계·데이터구성·학습/재학습 조작·도면생성·법규 DB"],
       ["도면 생성 화면", "사전학습 × 파인튜닝(데이터 구성+세대수) → 학습 → 샘플 → 이미지+DXF"],
       ["웹 보정 에디터", "문 방향 클릭·벽 드래그 즉시반영 (Streamlit 한계 보완)"],
       ["cadrender 공용 코어", "T·G 공용 렌더 → 이미지(matplotlib) + DXF(ezdxf) + 자기교정"]],
      [Inches(3.4), Inches(8.8)])
text(s, Inches(0.55), Inches(5.1), Inches(12.2), Inches(1.4),
     [("인프라: 한국형 엔진 13/18 아키텍처 패치·검증 · 통일 그래프 회계 영속 캐시(27s→0.05s)",
       {"size": 13, "color": GRAY, "bullet": True}),
      ("하이브리드 프론트: 분석·비교 = Streamlit · 편집(클릭·드래그) = 웹 SVG", {"size": 13, "color": GRAY, "bullet": True})], space=6)

# ════════════════════════════════════════════════════════════════════════════
# 14. 현재 상태
s = slide()
header(s, 14, "현재 상태 — 학습 진행 중", "STATUS")
chip(s, Inches(0.55), Inches(1.45), Inches(3.9), "엔진 아키텍처", "✅ 완료·검증", GREEN)
chip(s, Inches(4.7), Inches(1.45), Inches(3.9), "샘플→렌더(이미지+DXF)", "✅ E2E 검증", GREEN)
chip(s, Inches(8.85), Inches(1.45), Inches(3.9), "엔진 학습", "🔄 진행 중", AMBER)
text(s, Inches(0.55), Inches(2.9), Inches(12.2), Inches(3.5),
     [("진척", {"size": 15, "bold": True, "color": INDIGO}),
      ("품질 게이트·통일 그래프·엔진 데이터 변환(온전만·13/18)·아키텍처 — 완료", {"size": 14, "bullet": True}),
      ("3-stage 학습 게이트 통과(한국 데이터, loss 수렴·무오류) · 샘플→렌더 파이프라인 완성", {"size": 14, "bullet": True}),
      ("현재: RPLAN 사전학습 + 한국 파인튜닝 학습 진행(다중 단계) · 짧은 ARM-B 런으로 첫 도면 선행 확보 중", {"size": 14, "bullet": True}),
      ("렌더 검증 예시(정답 그래프 기준): 방·문·창·기구·치수·DXF 전부 생성 — 파이프라인 작동 확인", {"size": 14, "bullet": True})], space=9)

# ════════════════════════════════════════════════════════════════════════════
# 15. 향후 계획
s = slide()
header(s, 15, "향후 계획 · 논문", "NEXT")
text(s, Inches(0.55), Inches(1.5), Inches(6.1), Inches(4.5),
     [("로드맵", {"size": 16, "bold": True, "color": INDIGO}),
      ("학습 완료 → 동결 test 샘플링", {"size": 14, "bullet": True}),
      ("T ∥ G · 데이터구성 · 사전학습 비교", {"size": 14, "bullet": True}),
      ("FID · 법규준수율 · 완성도 정량 평가", {"size": 14, "bullet": True}),
      ("알바 보정 → 더 좋은 그래프 → 재학습 (반복 루프)", {"size": 14, "bullet": True}),
      ("성공 기준 = FID 동급 AND 법규·완전 압도", {"size": 14, "bold": True, "color": DARK})], space=10)
box(s, Inches(6.9), Inches(1.5), Inches(5.85), Inches(4.4), fill=LIGHT, round_=True)
text(s, Inches(7.2), Inches(1.7), Inches(5.3), Inches(4),
     [("목표 논문", {"size": 16, "bold": True, "color": INDIGO}),
      ("A. 한국 아파트 데이터셋·벤치마크", {"size": 14, "bullet": True}),
      ("B. 완전 도면 생성(문·창·기구·치수) 방법", {"size": 14, "bullet": True}),
      ("C. 법규-인식 생성 (필드 최초)", {"size": 14, "bullet": True}),
      ("D. 자체 소버린 엔진 (SOTA 부품+novelty)", {"size": 14, "bullet": True})], space=12)

# ════════════════════════════════════════════════════════════════════════════
# 16. 마무리
s = slide()
box(s, 0, 0, EMU_W, EMU_H, fill=DARK)
box(s, 0, Inches(3.5), EMU_W, Pt(3), fill=INDIGO)
text(s, Inches(0.9), Inches(2.3), Inches(11.5), Inches(1.5),
     [("요약", {"size": 18, "color": INDIGO, "bold": True}),
      ("한국 데이터 129,007 → 통일 그래프 → 자체 소버린 엔진(13/18) → 완전 도면+DXF",
       {"size": 24, "color": WHITE, "bold": True})], space=12)
text(s, Inches(0.9), Inches(4.1), Inches(11.5), Inches(2),
     [("박스 회귀 사망 → diffusion 엔진 전환 · neuro-symbolic으로 SOTA가 안 하는 완전성 확보",
       {"size": 15, "color": RGBColor(0xC7, 0xD2, 0xFE)}),
      ("차별성 = 법규-인식 · 완전 도면 · 한국 소버린 — 비교 가능한 프로그램으로 검증 진행 중",
       {"size": 15, "color": RGBColor(0x9C, 0xA3, 0xAF)})], space=10)

out = sys.argv[1] if len(sys.argv) > 1 else "plan2graph_중간발표.pptx"
prs.save(out)
print("saved:", out, "·", len(prs.slides._sldIdLst), "slides")
