"""G-라인 자동 베이스라인 배치 추출 — 코퍼스 전량 → geometry-rich 그래프(schema g-0.3).

원칙([[gline-auto-svg-then-human-upgrade]]): **프로그램이 1차 완전 그래프를 자동 생성**한다
(사람 없이 성립). 사람(알바)은 그 위에서 GUI(topoedit)로 잔여를 손보정(corrected=true)한다.
이 스크립트 = 그 자동 베이스라인(corrected=false) 생성기.

위상 추론은 **T-라인의 검증된 방식 재사용**(topology.build_graph) — 문 법선 probe·개방통로
(open_passages, OPEN_* 파라미터)·발코니 통로·iter_units(현관 가진 연결요소=세대). 즉 문-only가
아니라 최대한 연결된 그래프를 자동으로 만든다(파라미터 튜닝으로 가용률 극대화, 잔여=알바).
※ T/G 스키마는 안 섞는다(ADR-0002) — 재사용하는 건 위상 '추론 방식'이고 산출은 G 스키마(rooms).

소스 두 가지:
  - aihub : zip 코퍼스(aihub_source, 115 실데이터). SPA+STR+OBJ+OCR 지문병합. **SPA 보유 FP 전량**(dual+spa_only). 각 세대마다 SVG 베이스라인도 기록(사람 보정 substrate).
  - dir   : linked 디렉터리(topoedit.scan_dir, 로컬 스모크).
두 단계로 **명확히 분리**(한 작업으로 뭉치지 말 것 — 개념이 꼬인다):
  ① 선행 = **SVG 변환** (svg_convert): 원본 → SVG (사람 보정 substrate).
       dr → topology.build_graph(문·open·balcony) → iter_units(세대 분리)
       → State(role·polygon·fixtures) → suggest_roles → to_svg → REC_DIR/{plan}_u{i}.svg
  ② 빌드 = **SVG→그래프** (build_graphs): 저장 SVG(+dr) → geomgraph.build(g-0.3)
       → gline/graphs/{plan}_u{i}.json (ADR-0002 분리) + _manifest.json(숫자·분류·사유).
       **반복 가능** — 파라미터·SVG 보정 후 재빌드로 보정필요→사용가능 전환.
  ※ "빌드"=그래프 만드는 단계(T·G 공통). 원본→SVG는 빌드 아니라 'SVG 변환'.

검증(geomgraph.validate) 기준 처분:
  excl = 격리(hard: 면적없음·방부족·필수공간없음·위상단절)
  fix  = 통과지만 soft 경고(역할미상·현관없음·문폭없음) → 알바 보정 대상
  use  = 통과 + 무경고

실행(서버 115, raw zip 보유):
  python scripts/build_gline_auto.py --source aihub --house APT --stage svg    # ① 선행만
  python scripts/build_gline_auto.py --source aihub --house APT --stage build  # ② 빌드만(반복)
  python scripts/build_gline_auto.py --source aihub --house APT                # all(①후②)
  python scripts/build_gline_auto.py --source aihub --open-min-ratio 0.25      # 파라미터 튜닝
  python scripts/build_gline_auto.py --stage svg                               # 로컬 dir 스모크
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph import topoedit as T  # noqa: E402
from plan2graph import geomgraph as GG  # noqa: E402
from plan2graph import topology as TP  # noqa: E402
from plan2graph.topoedit import Node, State  # noqa: E402

OUT_DIR = config.DATA_DIR / "staging" / "gline"      # G 단일 진실(ADR-0003): 자동+사람 보정 공존
GRAPHS_DIR = OUT_DIR / "graphs"                       # topoedit(사람)도 같은 폴더에 corrected=true 저장
MANIFEST = OUT_DIR / "_manifest.json"

# T-라인 via → G via 도메인({door, open}) 매핑. exterior/entrance는 방-방 아님 → 드롭.
VIA_MAP = {"door": "door", "open": "open", "balcony": "open"}


def _disposition(g: dict) -> str:
    """validation → use/fix/excl (T-라인 처분모델과 대칭)."""
    v = g.get("validation", {})
    if not v.get("passed", False):
        return "excl"
    return "fix" if v.get("warnings") else "use"


def _states_from_dr(dr, plan_id: str, house: str):
    """dr → 세대별 State(yield). 위상=topology.build_graph(문·open·balcony) 재사용.
    iter_units가 현관 가진 연결요소를 세대로 분리 → 각 세대는 정의상 연결(위상단절 격감).
    코어·노이즈 방은 build_graph가 자동 제외."""
    G = TP.build_graph(dr)
    units, _noise = TP.iter_units(G, min_rooms=2)
    for ui, U in enumerate(units):
        nodes = {}
        for n in U.nodes:
            if n == TP.EXTERIOR:
                continue
            r = dr.rooms[n]
            if r.polygon is None:
                continue
            base = G.nodes[n].get("type") or r.class_name.replace("공간_", "")
            cx, cy = r.centroid if r.centroid else (0.0, 0.0)
            nodes[n] = Node(id=n, base=base, role=base, source="label",
                            cx=float(cx), cy=float(cy), polygon=r.polygon,
                            fixtures=list(G.nodes[n].get("objects") or []),
                            area_px=float(r.area_px))
        edges = []
        for u, v, d in U.edges(data=True):
            if u == TP.EXTERIOR or v == TP.EXTERIOR:
                continue
            via = VIA_MAP.get(d.get("via"))
            if via and u in nodes and v in nodes:
                edges.append({"a": u, "b": v, "via": via, "source": "auto"})
        if len(nodes) >= 2:
            yield State(plan_id=f"{plan_id}_u{ui}", house=house, nodes=nodes, edges=edges)


def _provenance(rec: dict) -> dict:
    """scan rec → 출처(provenance). 실라벨(zip) vs 예측(__file__: predicted_img=이미지직접,
    predicted=페어)로 구성 분류 → dual/spa_only/str_only/objocr. g0(dual)·g1(추가) freeze 필터
    + ADR-0005 검수 콤보(구성×처분)용. 새 버킷 아님 — T-라인 라벨 구성과 동일 어휘."""
    labels = rec.get("labels", {})

    def kind(lt):
        v = labels.get(lt)
        if not v:
            return None
        if v[0] != "__file__":
            return "label"                                  # 실라벨(zip)
        return "v2v_img" if "predicted_img" in str(v[1]) else "v2v"   # 예측(이미지직접 vs 페어)

    spa, strk = kind("SPA"), kind("STR")
    if spa == "label" and strk == "label":
        src = "dual"
    elif spa == "label":
        src = "spa_only"
    elif strk == "label":
        src = "str_only"
    else:
        src = "objocr"                                      # 실 SPA/STR 없음 — 예측으로 방 확보
    return {"source": src, "spa": spa, "str": strk}


def _iter_plans(source: str, src_dir: Path, house: str | None):
    """소스별 (plan_id, house, dr, provenance) 제너레이터. aihub=zip 코퍼스(dual+예측복구),
    dir=linked 디렉터리. provenance = 출처(dual/spa_only/str_only/objocr) — freeze·콤보용."""
    if source == "aihub":
        from plan2graph import aihub_source as A
        # SPA(방) 보유 FP 전량 — dual·spa_only(실라벨) + str_only·objocr(예측 SPA로 방 확보).
        # STR 없으면 위상이 빈약해 fix(보정필요)로 분류되지만 정상(보정대상).
        for rec in A.scan(house=house):
            try:
                dr, _png = A.load(rec)
            except Exception as e:  # noqa: BLE001
                print(f"  [skip] {rec['plan_id']}: load 실패 {e}")
                continue
            yield rec["plan_id"], rec["house"], dr, _provenance(rec)
    else:
        plans = T.scan_dir(src_dir)
        if house:
            plans = [p for p in plans if p.house == house]
        for rp in plans:
            try:
                dr, _png = T.load_plan(rp)
            except Exception as e:  # noqa: BLE001
                print(f"  [skip] {rp.plan_id}: load 실패 {e}")
                continue
            yield rp.plan_id, rp.house, dr, {"source": "dir", "spa": "label", "str": "label"}


def svg_convert(source: str = "dir", src_dir: Path | None = None,
                house: str | None = None, limit: int | None = None) -> dict:
    """① 선행단계 = **SVG 변환**: 원본 → 자동 State → SVG. REC_DIR/*.svg 기록까지만.
    그래프·검증·처분 회계 없음 — 사람 보정 substrate를 깐다. 소스 바뀔 때만 재실행.
    (빌드[②]는 별도: build_graphs가 이 SVG들을 읽어 그래프를 뽑는다.)"""
    T.REC_DIR.mkdir(parents=True, exist_ok=True)         # SVG 변환 출력 위치
    n_units = n_plans = 0
    t0 = time.time()
    for plan_id, phouse, dr, _prov in _iter_plans(source, src_dir, house):
        if limit and n_plans >= limit:
            break
        n_plans += 1
        for st in _states_from_dr(dr, plan_id, phouse):
            for nid, role in T.suggest_roles(st, dr).items():    # 자동 역할(OCR·기구·면적)
                T.set_role(st, nid, role)
            svg = T.to_svg(st, dr)                                # 원본(자동 State) → SVG
            (T.REC_DIR / f"{st.plan_id}.svg").write_text(svg, encoding="utf-8")
            n_units += 1
    man = {"stage": "svg_convert", "source": source,
           "source_dir": (str(src_dir) if source == "dir" else None),
           "house": house or "ALL", "n_plans": n_plans, "n_units_svg": n_units,
           "built_sec": round(time.time() - t0, 1)}
    print(f"① SVG 변환 완료: {n_units} units → {T.REC_DIR}")
    return man


def build_graphs(source: str = "dir", src_dir: Path | None = None,
                 house: str | None = None, limit: int | None = None) -> dict:
    """② 빌드단계 = **SVG→그래프**: 저장된 REC_DIR/*.svg(+원본 dr) → geometry-rich 그래프
    + 처분 회계(use/fix/excl) + 매니페스트. **반복 가능** — 파라미터·SVG 보정 후 재빌드로
    보정필요→사용가능 전환. SVG 없으면 skip(선행[①] 먼저 돌려야 함)."""
    GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
    disp, reasons, warns, info = Counter(), Counter(), Counter(), Counter()
    comp, comp_draw = Counter(), Counter()      # 구성(provenance.source) — 세대·도면별(ADR-0005 콤보)
    n_units = n_plans = n_missing = n_err = 0
    t0 = time.time()
    for plan_id, phouse, dr, prov in _iter_plans(source, src_dir, house):
        if limit and n_plans >= limit:
            break
        n_plans += 1
        comp_draw[prov["source"]] += 1                            # 도면(시트) 단위 구성
        try:                                                      # 위상/세대분리 실패 도면 스킵
            _states = list(_states_from_dr(dr, plan_id, phouse))
        except Exception as e:  # noqa: BLE001
            print(f"  [skip-plan] {plan_id}: states 실패 {e}")
            n_err += 1
            continue
        for st in _states:
            svg_path = T.REC_DIR / f"{st.plan_id}.svg"
            if not svg_path.exists():                             # 선행(SVG 변환) 안 된 세대
                n_missing += 1
                continue
            try:                                                  # 한 세대 기하 실패가 전체 빌드를 안 죽이게
                svg = svg_path.read_text(encoding="utf-8")        # 저장 SVG(사람 보정 반영분 포함)
                st_svg = T.state_from_svg(svg, dr, st.plan_id, st.house)
                g = GG.build(st_svg, dr)                          # SVG → 그래프 (enhance_roles_g 포함)
            except Exception as e:  # noqa: BLE001
                print(f"  [skip-unit] {st.plan_id}: build 실패 {e}")
                n_err += 1
                continue
            g["unit_id"] = st.plan_id
            g["corrected"] = False                                # 자동 베이스라인
            g["provenance"] = prov                                # 출처(dual/spa_only/str_only/objocr) — freeze·콤보
            comp[prov["source"]] += 1
            disp[_disposition(g)] += 1
            for r in g["validation"]["reasons"]:
                reasons[r] += 1
            for w in g["validation"]["warnings"]:
                warns[w] += 1
            for ii in g["validation"].get("info", []):
                info[ii] += 1
            (GRAPHS_DIR / f"{st.plan_id}.json").write_text(
                json.dumps(g, ensure_ascii=False), encoding="utf-8")
            n_units += 1
    use, fix, excl = disp.get("use", 0), disp.get("fix", 0), disp.get("excl", 0)
    man = {
        "schema_version": GG.SCHEMA_VERSION, "stage": "build", "corrected": False,
        "source": source, "source_dir": (str(src_dir) if source == "dir" else None),
        "house": house or "ALL",
        "open_min_ratio": config.OPEN_MIN_RATIO, "open_max_gap_px": config.OPEN_MAX_GAP_PX,
        "n_plans": n_plans, "n_units": n_units, "n_svg_missing": n_missing, "n_err": n_err,
        # ── 자동 분류(T-라인 구조) + 보정 회계([[gline-correction-not-verification]]) ──
        "분류_자동": {"사용": use, "보정필요": fix, "제외": excl},
        "사용가능_현재_자동": use,                 # 사람 손 없이 바로 사용가능
        "사용가능_상한_전부보정시": use + fix,      # 제외 빼고 모두 보정완료 가정(= n_units - 제외)
        "증량여지_보정필요→보정완료": fix,          # ⭐사람 보정이 키우는 건 이것뿐(사용→보정완료는 +0)
        "제외_보증불가": excl,
        "제외_사유": dict(reasons.most_common()),
        "보정필요_경고": dict(warns.most_common()),
        "정보_측정결손": dict(info.most_common()),   # 문폭없음 등 — 사용 차단 아님
        # ── 구성(provenance) — dual/spa_only/str_only/objocr (ADR-0005 라벨구성 축, freeze g0/g1) ──
        "구성_세대": dict(comp.most_common()),
        "구성_도면": dict(comp_draw.most_common()),
        "built_sec": round(time.time() - t0, 1),
    }
    MANIFEST.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    if n_missing:
        print(f"  [주의] SVG 없는 세대 {n_missing} skip — '--stage svg'(선행) 먼저 돌리세요.")
    print(f"② 빌드 완료: {n_units} units, 분류 {man['분류_자동']} → {GRAPHS_DIR}")
    return man


def build_corpus(source: str = "dir", src_dir: Path | None = None,
                 house: str | None = None, limit: int | None = None) -> dict:
    """편의 래퍼 — ① 선행(SVG 변환) 후 ② 빌드(SVG→그래프)를 연달아 실행.
    정식 두 단계는 svg_convert()·build_graphs()를 따로 호출(한 작업으로 뭉치지 말 것)."""
    sv = svg_convert(source, src_dir, house, limit)
    man = build_graphs(source, src_dir, house, limit)
    man["n_units_svg"] = sv["n_units_svg"]
    return man


def revalidate() -> dict:
    """저장된 gline 그래프를 **현재 검증기로 재검증** — validation/meta 갱신 + 매니페스트 재생성.
    재빌드 없이 분류 정책 변경(예: 문폭없음 강등)을 반영. 기하 재계산 없음 → 빠름."""
    disp, reasons, warns, info = Counter(), Counter(), Counter(), Counter()
    n = 0
    for f in sorted(GRAPHS_DIR.glob("*.json")):
        try:
            g = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        n += 1
        GG.enhance_roles_g(g)                  # 기타방 역할 보강(저장 그래프에 직접 적용 — 재빌드 없이)
        v = GG.validate(g)
        g["validation"] = v
        if "meta" in g:
            g["meta"]["status"] = "success" if v["passed"] else "quarantine"
            g["meta"]["reason"] = ",".join(v["reasons"])
        f.write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
        disp[_disposition(g)] += 1
        for r in v["reasons"]:
            reasons[r] += 1
        for w in v["warnings"]:
            warns[w] += 1
        for ii in v.get("info", []):
            info[ii] += 1
    use, fix, excl = disp.get("use", 0), disp.get("fix", 0), disp.get("excl", 0)
    man = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    man.update({
        "schema_version": GG.SCHEMA_VERSION, "n_units": n, "revalidated": True,
        "분류_자동": {"사용": use, "보정필요": fix, "제외": excl},
        "사용가능_현재_자동": use, "사용가능_상한_전부보정시": use + fix,
        "증량여지_보정필요→보정완료": fix, "제외_보증불가": excl,
        "제외_사유": dict(reasons.most_common()),
        "보정필요_경고": dict(warns.most_common()),
        "정보_측정결손": dict(info.most_common()),
    })
    MANIFEST.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    return man


# 버전별 출처 필터(T v0=dual정상 / v1=추가본과 대칭). provenance.source로 거른다.
VERSION_SOURCES = {
    "g0": ("dual",),                                       # dual 정상변환만
    "g1": ("dual", "spa_only", "str_only", "objocr"),      # + 추가본(V2V·objocr 복구)
}


def freeze(version: str, sources=None) -> dict:
    """staging/gline(현재) → releases/gline/<version> 동결([[gline-version-plan]]·[[staging-is-current]]).
    **provenance.source로 거른다**(g0=dual / g1=+추가, VERSION_SOURCES). 학습 입력(geom.jsonl)을 잠근다.
    출처 없는(구버전) 그래프는 필터 시 제외하고 그 수를 명시(silent truncation 금지)."""
    srcs = tuple(sources) if sources is not None else VERSION_SOURCES.get(version)
    rel = config.RELEASES_DIR / "gline" / version
    rel.mkdir(parents=True, exist_ok=True)
    n = 0
    by_src, no_prov = Counter(), 0
    with (rel / "geom.jsonl").open("w", encoding="utf-8") as w:
        for f in sorted(GRAPHS_DIR.glob("*.json")):
            try:
                g = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            src = (g.get("provenance") or {}).get("source")
            if srcs is not None:                              # 필터 지정 시 출처로 거름
                if src is None:
                    no_prov += 1
                    continue
                if src not in srcs:
                    continue
            w.write(json.dumps(g, ensure_ascii=False) + "\n")
            n += 1
            by_src[src or "?"] += 1
    man = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    man["version"] = version
    man["n_graphs"] = n
    man["freeze_sources"] = list(srcs) if srcs else "all"
    man["freeze_구성"] = dict(by_src.most_common())
    if no_prov:
        man["freeze_skipped_no_provenance"] = no_prov         # 구버전(출처미기록) 제외 수 명시
    (rel / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    out = {"version": version, "n_graphs": n, "sources": list(srcs) if srcs else "all",
           "구성": dict(by_src.most_common()), "path": str(rel)}
    if no_prov:
        out["skipped_no_provenance"] = no_prov
    return out


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=("aihub", "dir"), default="dir",
                    help="aihub=zip 코퍼스(115 실데이터) · dir=linked 디렉터리(로컬 스모크)")
    ap.add_argument("dir", nargs="?", default=None,
                    help="dir 소스일 때 코퍼스 디렉터리(linked 포맷). 정식 빌드는 --source aihub")
    ap.add_argument("--house", default=None, help="APT|DEH|ROW (생략=전부)")
    ap.add_argument("--limit", type=int, default=None, help="도면 수 상한(테스트)")
    # 위상 자동 추론 파라미터 튜닝(T-라인 open_passages) — 가용률 극대화용
    ap.add_argument("--open-min-ratio", type=float, default=None,
                    help="개방통로 벽 미피복 비율 임계(낮을수록 더 많이 연결). 기본 config")
    ap.add_argument("--open-max-gap", type=float, default=None,
                    help="개방통로 최대 간격 px(클수록 더 멀어도 연결). 기본 config")
    ap.add_argument("--stage", choices=("svg", "build", "all"), default="all",
                    help="svg=① 선행(SVG 변환)만 · build=② 빌드(SVG→그래프)만(기존 SVG에서·반복용) · all=둘 다")
    ap.add_argument("--revalidate", action="store_true",
                    help="재빌드 없이 저장 그래프를 현재 검증기로 재검증·매니페스트 갱신")
    ap.add_argument("--freeze", metavar="VER", default=None,
                    help="staging/gline → releases/gline/<VER> 동결(g0, g1…). 학습 입력 잠금")
    a = ap.parse_args()
    if a.freeze:
        info = freeze(a.freeze)
        print(json.dumps(info, ensure_ascii=False, indent=2))
        sys.exit(0)
    if a.open_min_ratio is not None:
        config.OPEN_MIN_RATIO = a.open_min_ratio      # build_graph가 호출 시점에 읽음
    if a.open_max_gap is not None:
        config.OPEN_MAX_GAP_PX = a.open_max_gap
    if a.revalidate:
        man = revalidate()
    else:
        if a.source == "dir" and not a.dir:
            ap.error("--source dir 는 코퍼스 디렉터리 인자가 필요합니다. 정식 빌드는 --source aihub(115 zip).")
        _sd = Path(a.dir) if a.dir else None
        if a.stage == "svg":                       # ① 선행만
            man = svg_convert(source=a.source, src_dir=_sd, house=a.house, limit=a.limit)
        elif a.stage == "build":                   # ② 빌드만(기존 SVG에서)
            man = build_graphs(source=a.source, src_dir=_sd, house=a.house, limit=a.limit)
        else:                                       # all = ① 후 ②
            man = build_corpus(source=a.source, src_dir=_sd, house=a.house, limit=a.limit)
    print(json.dumps(man, ensure_ascii=False, indent=2))
    if "분류_자동" in man:                          # 빌드/올/재검증 결과
        print(f"\n→ {GRAPHS_DIR}  ({man['n_units']} units, 분류 {man['분류_자동']})")
    else:                                           # 선행(SVG 변환)만
        print(f"\n→ {T.REC_DIR}  (SVG {man.get('n_units_svg', 0)} units 변환)")
