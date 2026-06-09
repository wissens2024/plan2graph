"""G-라인 자동 베이스라인 배치 추출 — 코퍼스 전량 → geometry-rich 그래프(schema g-0.3).

원칙([[gline-auto-svg-then-human-upgrade]]): **프로그램이 1차 완전 그래프를 자동 생성**한다
(사람 없이 성립). 사람(알바)은 그 위에서 GUI(topoedit)로 잔여를 손보정(corrected=true)한다.
이 스크립트 = 그 자동 베이스라인(corrected=false) 생성기.

위상 추론은 **T-라인의 검증된 방식 재사용**(topology.build_graph) — 문 법선 probe·개방통로
(open_passages, OPEN_* 파라미터)·발코니 통로·iter_units(현관 가진 연결요소=세대). 즉 문-only가
아니라 최대한 연결된 그래프를 자동으로 만든다(파라미터 튜닝으로 가용률 극대화, 잔여=알바).
※ T/G 스키마는 안 섞는다(ADR-0002) — 재사용하는 건 위상 '추론 방식'이고 산출은 G 스키마(rooms).

소스 두 가지:
  - aihub : zip 코퍼스(aihub_source, 115 실데이터). SPA+STR+OBJ+OCR 지문병합. dual만 빌드.
  - dir   : linked 디렉터리(topoedit.scan_dir, 로컬 스모크).
공통:  dr → topology.build_graph(문·open·balcony) → iter_units(세대 분리)
        → State(role·polygon·fixtures) → suggest_roles(자동 역할) → geomgraph.build(g-0.3)
        → data/staging/gline/graphs/{plan}_u{i}.json (ADR-0002 분리, 옵션 A)
        + _manifest.json : 숫자·분류·사유(use/fix/excl) — 데이터셋 본질=숫자·카테고리

검증(geomgraph.validate) 기준 처분:
  excl = 격리(hard: 면적없음·방부족·필수공간없음·위상단절)
  fix  = 통과지만 soft 경고(역할미상·현관없음·문폭없음) → 알바 보정 대상
  use  = 통과 + 무경고

실행(서버 115, raw zip 보유):
  python scripts/build_gline_auto.py --source aihub --house APT
  python scripts/build_gline_auto.py --source aihub --open-min-ratio 0.25   # 파라미터 튜닝
  python scripts/build_gline_auto.py                                        # 로컬 dir 스모크
"""
from __future__ import annotations

import argparse
import json
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

OUT_DIR = config.DATA_DIR / "staging" / "gline"      # 옵션 A: 자동 베이스라인 전용(사람 검증완료는 topo_human)
GRAPHS_DIR = OUT_DIR / "graphs"
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


def _iter_plans(source: str, src_dir: Path, house: str | None):
    """소스별 (plan_id, house, dr) 제너레이터. aihub=zip 코퍼스(dual만), dir=linked 디렉터리."""
    if source == "aihub":
        from plan2graph import aihub_source as A
        for rec in A.scan(house=house):
            if "STR" not in rec["labels"]:          # dual(SPA+STR)만 — 문/벽 없으면 위상 빈약
                continue
            try:
                dr, _png = A.load(rec)
            except Exception as e:  # noqa: BLE001
                print(f"  [skip] {rec['plan_id']}: load 실패 {e}")
                continue
            yield rec["plan_id"], rec["house"], dr
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
            yield rp.plan_id, rp.house, dr


def build_corpus(source: str = "dir", src_dir: Path | None = None,
                 house: str | None = None, limit: int | None = None) -> dict:
    """코퍼스 전량 → gline/graphs/*.json + 매니페스트(숫자·분류·사유)."""
    GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
    disp, reasons, warns = Counter(), Counter(), Counter()
    n_units = n_plans = 0
    t0 = time.time()
    for plan_id, phouse, dr in _iter_plans(source, src_dir, house):
        if limit and n_plans >= limit:
            break
        n_plans += 1
        for st in _states_from_dr(dr, plan_id, phouse):
            for nid, role in T.suggest_roles(st, dr).items():    # 자동 역할(OCR·기구·면적)
                T.set_role(st, nid, role)
            g = GG.build(st, dr)
            g["unit_id"] = st.plan_id
            g["corrected"] = False                                # 자동 베이스라인(seed)
            disp[_disposition(g)] += 1
            for r in g["validation"]["reasons"]:
                reasons[r] += 1
            for w in g["validation"]["warnings"]:
                warns[w] += 1
            (GRAPHS_DIR / f"{st.plan_id}.json").write_text(
                json.dumps(g, ensure_ascii=False), encoding="utf-8")
            n_units += 1
    use, fix, excl = disp.get("use", 0), disp.get("fix", 0), disp.get("excl", 0)
    man = {
        "schema_version": GG.SCHEMA_VERSION, "corrected": False,
        "source": source, "source_dir": (str(src_dir) if source == "dir" else None),
        "house": house or "ALL",
        "open_min_ratio": config.OPEN_MIN_RATIO, "open_max_gap_px": config.OPEN_MAX_GAP_PX,
        "n_plans": n_plans, "n_units": n_units,
        # ── 자동 분류(T-라인 구조) + 보정 회계([[gline-correction-not-verification]]) ──
        "분류_자동": {"사용": use, "보정필요": fix, "제외": excl},
        "사용가능_현재_자동": use,                 # 사람 손 없이 바로 사용가능
        "사용가능_상한_전부보정시": use + fix,      # 제외 빼고 모두 보정완료 가정(= n_units - 제외)
        "증량여지_보정필요→보정완료": fix,          # ⭐사람 보정이 키우는 건 이것뿐(사용→보정완료는 +0)
        "제외_보증불가": excl,
        "보정완료_사람": 0,                        # 자동 베이스라인은 0. 이후 ledger(사람)서 증가
        "제외_사유": dict(reasons.most_common()),
        "보정필요_경고": dict(warns.most_common()),
        "built_sec": round(time.time() - t0, 1),
    }
    MANIFEST.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    return man


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
    a = ap.parse_args()
    if a.open_min_ratio is not None:
        config.OPEN_MIN_RATIO = a.open_min_ratio      # build_graph가 호출 시점에 읽음
    if a.open_max_gap is not None:
        config.OPEN_MAX_GAP_PX = a.open_max_gap
    if a.source == "dir" and not a.dir:
        ap.error("--source dir 는 코퍼스 디렉터리 인자가 필요합니다. 정식 빌드는 --source aihub(115 zip).")
    man = build_corpus(source=a.source, src_dir=Path(a.dir) if a.dir else None,
                       house=a.house, limit=a.limit)
    print(json.dumps(man, ensure_ascii=False, indent=2))
    print(f"\n→ {GRAPHS_DIR}  ({man['n_units']} units, {man['disposition']})")
