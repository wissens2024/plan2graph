"""출처별 검수 현황 집계 — 총/정상/격리 + 격리 사유 분포 (DATASET_DESIGN §2③).

각 그래프 레코드의 meta.status(success|quarantine)·meta.reason(위반 규칙)을 모아
"총 N개 중 정상 X · 격리 Y, 사유별 분포"를 만든다. GUI가 이를 보여주고, 사유를
눌러 해당 격리 도면을 원본∥그래프로 검수 → "왜 못 했는지" 파악 → 보정 판단으로 잇는다.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

# 무결성 위반 규칙 → 사람이 읽는 사유(rules.py R1~R5).
RULE_KO = {
    "R1_isolated_component": "분리된 덩어리(여러 세대/조각)",
    "R2_doorless_room": "문 없는 고립 방",
    "R3_unreachable_from_entrance": "현관에서 도달 불가",
    "R4_no_entrance": "현관(진입점) 없음",
    "R5_unresolved_doors": "미해소 문",
    "duplicate": "중복(동일 평면도 사본)",
    "untyped_rooms": "방 타입 미정(Undefined만)",
    "empty_svg": "빈 도면(공간 폴리곤 0)",
    "svg_parse_error": "SVG 파싱 실패(손상)",
    "empty_layout": "빈 레이아웃(방 0)",
    "": "사유 미기록",
}


def reason_label(rule: str) -> str:
    return RULE_KO.get(rule, rule or "사유 미기록")


# ── 처분(disposition): 도면 1장 → 대표 사유 1개로 상호배타 배정 ──────────────────
# 격리 레코드는 사유가 여럿 겹칠 수 있어(합>격리수), 그대로 사유별로 쪼개면 칸이 겹쳐
# '합=다운로드'가 깨진다. 그래서 우선순위 총순서로 '제일 센/근본 사유 1개'만 배정해
# 모든 칸을 상호배타로 만든다(도면 1장=한 칸). 도면의 전체 위반은 레코드 detail에 남음.
#   group: use(사용) · fix(보정필요, 살릴 수 있음) · excl(영구제외)
# 순서 = 위에서부터 우선(영구제외 먼저 → 보정필요는 근본원인 순). untyped_rooms는
# '방 폴리곤은 있고 타입만 미정'이라 재라벨로 살릴 수 있어 fix(보정필요)로 둔다.
DISPOSITION_PRIORITY = [
    ("duplicate",                    "excl", "🔁 제외 · 중복(복사본)"),
    ("svg_parse_error",              "excl", "⬜ 제외 · 변환실패(손상)"),
    ("empty_svg",                    "excl", "⬜ 제외 · 빈 도면"),
    ("empty_layout",                 "excl", "⬜ 제외 · 빈 레이아웃(방 0)"),
    ("untyped_rooms",                "fix",  "🛠 보정필요 · 방 타입 미정"),
    ("R4_no_entrance",               "fix",  "🛠 보정필요 · 현관 없음"),
    ("R1_isolated_component",        "fix",  "🛠 보정필요 · 분리 덩어리"),
    ("R3_unreachable_from_entrance", "fix",  "🛠 보정필요 · 도달 불가"),
    ("R2_doorless_room",             "fix",  "🛠 보정필요 · 문 없는 방"),
    ("R5_unresolved_doors",          "fix",  "🛠 보정필요 · 미해소 문"),
]
_USE_LABEL = "✅ 사용 · 변환·채택"
_FIX_ETC_LABEL = "🛠 보정필요 · 기타"


def disposition_of(status: str, reason: str) -> tuple[str, str]:
    """레코드 1개 → (group, 대표 라벨). 정상이면 사용, 격리면 최우선(센·근본) 사유 1개.
    group ∈ {use, fix, excl}."""
    if status == "success":
        return ("use", _USE_LABEL)
    rs = {r.strip() for r in (reason or "").split(",") if r.strip()}
    for key, group, label in DISPOSITION_PRIORITY:
        if key in rs:
            return (group, label)
    return ("fix", _FIX_ETC_LABEL)


def disposition_label(status: str, reason: str) -> str:
    return disposition_of(status, reason)[1]


def _disposition_order() -> list[str]:
    """콤보 표시 순서: 사용 → 보정필요(우선순위) → 영구제외."""
    fix = [lab for _, g, lab in DISPOSITION_PRIORITY if g == "fix"] + [_FIX_ETC_LABEL]
    excl = [lab for _, g, lab in DISPOSITION_PRIORITY if g == "excl"]
    return [_USE_LABEL] + fix + excl


def disposition_groups(summary: dict) -> dict:
    """scan_status summary → {use, fix, excl, total}. 종합 패널의 처분 3분류 집계."""
    from collections import Counter
    c: Counter = Counter()
    for _stem, (stt, rsn) in summary.get("by_id", {}).items():
        c[disposition_of(stt, rsn)[0]] += 1
    return {"use": c.get("use", 0), "fix": c.get("fix", 0),
            "excl": c.get("excl", 0), "total": sum(c.values())}


def disposition_combo(summary: dict) -> list[tuple[str, int]]:
    """scan_status summary → 정렬된 [(대표라벨, 건수)] (건수>0만). 상호배타라 합=total."""
    from collections import Counter
    c: Counter = Counter()
    for _stem, (stt, rsn) in summary.get("by_id", {}).items():
        c[disposition_label(stt, rsn)] += 1
    order = _disposition_order()
    known = [(lab, c[lab]) for lab in order if c.get(lab, 0)]
    extra = [(lab, n) for lab, n in c.items() if lab not in order and n]  # 방어
    return known + extra


def scan_status(graphs_dir: Path) -> dict:
    """graphs_dir/*.json 메타 집계.
    반환: {total, success, quarantine, reasons:{rule:count}, by_id:{stem:(status,reason)}}.
    by_id 키는 파일 stem(=graph_id, 예 'RPLAN_42007')."""
    total = success = 0
    reasons: Counter = Counter()
    by_id: dict[str, tuple[str, str]] = {}
    if graphs_dir.is_dir():
        for f in graphs_dir.glob("*.json"):
            try:
                m = json.loads(f.read_text(encoding="utf-8")).get("meta", {})
            except Exception:  # noqa: BLE001
                continue
            total += 1
            stt = m.get("status", "success")
            rsn = (m.get("reason") or "").strip()
            by_id[f.stem] = (stt, rsn)
            if stt == "success":
                success += 1
            else:   # 레코드당 사유는 중복제거(같은 규칙 다회 위반=1로 카운트)
                for r in (set(rsn.split(",")) if rsn else {""}):
                    reasons[r.strip()] += 1
    return {"total": total, "success": success, "quarantine": total - success,
            "reasons": dict(reasons.most_common()), "by_id": by_id}


def _gline_disp(g: dict) -> str:
    """G 그래프 1건(세대) → 처분 1개 ∈ {done, use, fix, excl}. gline_status와 동일 규칙.
    corrected=true=보정완료(사람), 나머지는 validation으로 자동 분류.
    [[gline-correction-not-verification]]·[[gline-single-source]]."""
    if g.get("corrected"):
        return "done"                              # 사람 보정완료 = 사용 확정
    v = g.get("validation") or {}
    passed = v.get("passed")
    if passed is None:                             # 옛 레코드 폴백
        passed = (g.get("meta", {}).get("status") == "success")
    if not passed:
        return "excl"
    if v.get("warnings"):
        return "fix"
    return "use"


def gline_status(graphs_dir: Path) -> dict:
    """G-라인 보정 회계(단일 소스 staging/gline) — corrected=true=보정완료(사람), 나머지=자동 분류.
    [[gline-correction-not-verification]]·[[gline-single-source]] 2축. 반환:
    {total, use, fix, excl, done, usable_now, usable_max, reasons, warns,
     draw:{use,fix,excl,done}, by_house:{HOUSE:{세대·도면 버킷}}}.
    세대(json 1건)와 도면(plan_id에서 _u\\d+ 제거) 둘 다 집계 — T 정본과 같은 단위 병기."""
    import re
    cnt = Counter()                                    # 세대(unit) 버킷
    reasons, warns = Counter(), Counter()
    # 도면(시트) 버킷 — 한 도면에 여러 세대 타일. 도면의 처분 = 그 세대들 중 우선순위 1개.
    _DRAW_PRIO = {"excl": 0, "fix": 1, "done": 2, "use": 3}  # 작은 값이 우선(센 사유)
    draw_disp: dict[str, str] = {}
    house_unit: dict[str, Counter] = {}
    house_draw_disp: dict[str, dict[str, str]] = {}
    if graphs_dir.is_dir():
        for f in graphs_dir.glob("*.json"):
            try:
                g = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            d = _gline_disp(g)
            cnt[d] += 1
            pid = g.get("plan_id") or f.stem
            draw = re.sub(r"_u\d+$", "", pid)
            house = pid.split("_")[0] if "_" in pid else "?"
            house_unit.setdefault(house, Counter())[d] += 1
            # 도면 대표 처분(전체·house별) — 우선순위 더 센 것으로 갱신
            if draw not in draw_disp or _DRAW_PRIO[d] < _DRAW_PRIO[draw_disp[draw]]:
                draw_disp[draw] = d
            hd = house_draw_disp.setdefault(house, {})
            if draw not in hd or _DRAW_PRIO[d] < _DRAW_PRIO[hd[draw]]:
                hd[draw] = d
            if d == "excl":
                for r in (g.get("validation") or {}).get("reasons", []):
                    reasons[r] += 1
            elif d == "fix":
                for w in (g.get("validation") or {}).get("warnings", []):
                    warns[w] += 1
    use, fix, excl, done = cnt["use"], cnt["fix"], cnt["excl"], cnt["done"]
    draw_cnt = Counter(draw_disp.values())
    by_house = {}
    for h, uc in house_unit.items():
        dc = Counter(house_draw_disp.get(h, {}).values())
        by_house[h] = {
            "unit": {"use": uc["use"], "fix": uc["fix"], "excl": uc["excl"], "done": uc["done"]},
            "draw": {"use": dc["use"], "fix": dc["fix"], "excl": dc["excl"], "done": dc["done"]},
        }
    return {"total": use + fix + excl + done, "n_drawings": len(draw_disp),
            "use": use, "fix": fix, "excl": excl, "done": done,
            "usable_now": use + done, "usable_max": use + fix + done,
            "draw": {"use": draw_cnt["use"], "fix": draw_cnt["fix"],
                     "excl": draw_cnt["excl"], "done": draw_cnt["done"]},
            "by_house": by_house,
            "reasons": dict(reasons.most_common()), "warns": dict(warns.most_common())}


def gline_plan_status(graphs_dir: Path) -> dict[str, str]:
    """도면(plan_id, _u 제거) → 대표 처분 ∈ {use,fix,excl,done}. G 검수화면 분류 드롭다운용.
    상단 지표(gline_status)와 같은 소스·같은 규칙 → 한 화면 두 회계 불일치 제거."""
    import re
    prio = {"excl": 0, "fix": 1, "done": 2, "use": 3}
    out: dict[str, str] = {}
    if graphs_dir.is_dir():
        for f in graphs_dir.glob("*.json"):
            try:
                g = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            draw = re.sub(r"_u\d+$", "", g.get("plan_id") or f.stem)
            d = _gline_disp(g)
            if draw not in out or prio[d] < prio[out[draw]]:
                out[draw] = d
    return out


def aihub_row_units(r: dict) -> int:
    """manifest 행 1개가 기여하는 '고유 세대수' = 실제 변환된 그래프 수.
    중복(사본)·미변환(변환실패·V2V대기)·비FP = 0. 세대는 고유 변환분만 센다.
    중복은 '받은 도면 수'로만 카운트(세대 재계상 안 함 = 이중계상 방지)."""
    return len(r.get("graph_ids") or [])


def aihub_t_status(manifest_path: Path) -> dict:
    """T-라인 AI-Hub 회계(정본 manifest) — 처분 use/fix/excl, 도면·세대 병기, by_house.
    gline_status와 같은 접근법(x['use']=세대, x['draw']['use']=도면)으로 종합 비교에서 동일 처리.
    세대 = 고유 변환분(Σlen(graph_ids)). 중복·미변환·비FP=0(중복은 도면수로만, 세대 재계상 X).
    도면 = manifest 행 1개(받은 raw PNG 1장). [[dataset-disposition-accounting]]."""
    rows = []
    p = Path(manifest_path)
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                try:
                    rows.append(json.loads(ln))
                except Exception:  # noqa: BLE001
                    continue
    draw, unit = Counter(), Counter()
    house_draw: dict[str, Counter] = {}
    house_unit: dict[str, Counter] = {}
    for r in rows:
        disp = r.get("disposition")
        if disp not in ("use", "fix", "excl"):
            continue
        h = r.get("house") or "?"
        n = aihub_row_units(r)
        draw[disp] += 1
        unit[disp] += n
        house_draw.setdefault(h, Counter())[disp] += 1
        house_unit.setdefault(h, Counter())[disp] += n
    by_house = {}
    for h in set(house_draw) | set(house_unit):
        dd, du = house_draw.get(h, Counter()), house_unit.get(h, Counter())
        by_house[h] = {"draw": {k: dd[k] for k in ("use", "fix", "excl")},
                       "unit": {k: du[k] for k in ("use", "fix", "excl")}}
    return {"total": unit["use"] + unit["fix"] + unit["excl"],
            "total_draw": draw["use"] + draw["fix"] + draw["excl"],
            "use": unit["use"], "fix": unit["fix"], "excl": unit["excl"], "done": 0,
            "draw": {"use": draw["use"], "fix": draw["fix"], "excl": draw["excl"], "done": 0},
            "by_house": by_house}


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.path[:0] = [str(Path(__file__).resolve().parents[2]),
                    str(Path(__file__).resolve().parents[2] / "src")]
    from plan2graph import sources
    sid = sys.argv[1] if len(sys.argv) > 1 else "rplan"
    s = scan_status(sources.graphs_dir(sid))
    print("%s: 총 %d · 정상 %d · 격리 %d" % (sid, s["total"], s["success"], s["quarantine"]))
    for r, c in s["reasons"].items():
        print("   격리사유 %-32s %6d  (%s)" % (r, c, reason_label(r)))
