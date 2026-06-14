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


# ── G회계 디스크 영속 캐시 ───────────────────────────────────────────────────
# corrected_status/corrected_plan_status는 graphs/*.json(현재 40k·~1.8GB) 전수 파싱이라 1회 ~33s.
# 대시보드 rerun(위젯 조작·option_menu)마다 재호출되면 CPU가 계속 100%로 묶임(실측 버그).
# 결과를 작은 JSON으로 영속 캐시(키=파일수+디렉터리 mtime) → rerun·재시작에도 즉시 반환.
# 파일 추가/삭제(빌드·freeze·사람 보정 corrected 저장)로 mtime 바뀌면 자동 무효화(정합 유지).
def _acct_key(graphs_dir: Path) -> list:
    if not graphs_dir.is_dir():
        return [0, 0]
    n = sum(1 for _ in graphs_dir.glob("*.json"))      # 이름만 — read 없음(빠름)
    return [n, int(graphs_dir.stat().st_mtime)]


def _corrected_dir(graphs_dir: Path) -> Path:
    """사람 보정본 폴더(ADR-0008 폴더분리) — graphs/(원본) 옆 corrected/(작업)."""
    return graphs_dir.parent / "corrected"


def _corrected_ids(graphs_dir: Path) -> set:
    """corrected/ 에 저장된 보정완료 unit stem 집합(에디터 저장분)."""
    cd = _corrected_dir(graphs_dir)
    return {f.stem for f in cd.glob("*.json")} if cd.is_dir() else set()


def _acct_cached(graphs_dir: Path, name: str, compute, extra_key=None):
    """compute()를 디스크 캐시. 키 일치 시 캐시 반환, 아니면 계산 후 저장.
    extra_key: graphs/ 외 의존(예: corrected/ 변동)을 키에 더해 무효화 정합 유지
    (scan_status 등 corrected 무관 호출자는 extra_key 없이 그대로 — 보정 저장에 불필요 재계산 안 함)."""
    cache = graphs_dir.parent / f"_cache_{name}.json"   # graphs/ 밖(parent)에 둠 → mtime 영향 없음
    key = _acct_key(graphs_dir) + (list(extra_key) if extra_key else [])
    if cache.exists():
        try:
            d = json.loads(cache.read_text(encoding="utf-8"))
            if d.get("key") == key:
                return d["v"]
        except Exception:  # noqa: BLE001
            pass
    v = compute()
    try:
        cache.write_text(json.dumps({"key": key, "v": v}, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return v


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


def scan_status(graphs_dir: Path, use_cache: bool = True) -> dict:
    """graphs_dir/*.json 메타 집계.
    반환: {total, success, quarantine, reasons:{rule:count}, by_id:{stem:(status,reason)}}.
    by_id 키는 파일 stem(=graph_id, 예 'RPLAN_42007').

    ⚠️ corrected 40k 전수 파싱 = ~26s. corrected_status와 달리 함수 자체 캐시가 없어 호출자마다(검수
    화면 by_id 등) 매번 26s 재계산되던 버그 → _acct_cached로 디스크 영속(키=파일수+mtime).
    빌드/freeze/보정으로 파일 바뀌면 자동 무효화. by_id의 tuple은 JSON 왕복서 list로 바뀌므로
    캐시 경유 시 tuple로 정규화한다."""
    if use_cache:
        v = _acct_cached(graphs_dir, "scan_status", lambda: scan_status(graphs_dir, use_cache=False))
        v["by_id"] = {k: tuple(t) for k, t in v.get("by_id", {}).items()}   # list→tuple 정규화
        return v
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


def _corrected_disp(g: dict) -> str:
    """G 그래프 1건(세대) → 처분 1개 ∈ {done, use, fix, excl}. corrected_status와 동일 규칙.
    corrected=true=보정완료(사람), 나머지는 validation으로 자동 분류.
    [[corrected-correction-not-verification]]·[[corrected-single-source]]."""
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


def corrected_status(graphs_dir: Path, use_cache: bool = True) -> dict:
    """Corrected 보정 회계(단일 소스 staging/corrected) — corrected=true=보정완료(사람), 나머지=자동 분류.
    [[corrected-correction-not-verification]]·[[corrected-single-source]] 2축. 반환:
    {total, use, fix, excl, done, usable_now, usable_max, reasons, warns,
     draw:{use,fix,excl,done}, by_house:{HOUSE:{세대·도면 버킷}}}.
    세대(json 1건)와 도면(plan_id에서 _u\\d+ 제거) 둘 다 집계 — T 정본과 같은 단위 병기.
    40k 전수 파싱(~33s)이라 디스크 캐시(use_cache) — rerun·재시작에도 즉시(_acct_cached)."""
    if use_cache:
        return _acct_cached(graphs_dir, "corrected_status", lambda: corrected_status(graphs_dir, use_cache=False),
                            extra_key=_acct_key(_corrected_dir(graphs_dir)))
    import re
    cnt = Counter()                                    # 세대(unit) 버킷
    reasons, warns = Counter(), Counter()
    # 도면(시트) 버킷 — 한 도면에 여러 세대 타일. 도면의 처분 = 그 세대들 중 우선순위 1개.
    _DRAW_PRIO = {"excl": 0, "fix": 1, "done": 2, "use": 3}  # 작은 값이 우선(센 사유)
    draw_disp: dict[str, str] = {}
    house_unit: dict[str, Counter] = {}
    house_draw_disp: dict[str, dict[str, str]] = {}
    if graphs_dir.is_dir():
        corrected = _corrected_ids(graphs_dir)         # ADR-0008: corrected/ 저장분 = 보정완료(done)
        for f in graphs_dir.glob("*.json"):
            try:
                g = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            d = "done" if f.stem in corrected else _corrected_disp(g)
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


def corrected_plan_status(graphs_dir: Path, use_cache: bool = True) -> dict[str, str]:
    """도면(plan_id, _u 제거) → 대표 처분 ∈ {use,fix,excl,done}. G 검수화면 분류 드롭다운용.
    상단 지표(corrected_status)와 같은 소스·같은 규칙 → 한 화면 두 회계 불일치 제거.
    40k 전수 파싱(~33s)이라 디스크 캐시(use_cache) — rerun·재시작에도 즉시(_acct_cached)."""
    if use_cache:
        return _acct_cached(graphs_dir, "corrected_plan_status",
                            lambda: corrected_plan_status(graphs_dir, use_cache=False),
                            extra_key=_acct_key(_corrected_dir(graphs_dir)))
    import re
    prio = {"excl": 0, "fix": 1, "done": 2, "use": 3}
    out: dict[str, str] = {}
    if graphs_dir.is_dir():
        corrected = _corrected_ids(graphs_dir)         # ADR-0008: corrected/ 저장분 = 보정완료(done)
        for f in graphs_dir.glob("*.json"):
            try:
                g = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            draw = re.sub(r"_u\d+$", "", g.get("plan_id") or f.stem)
            d = "done" if f.stem in corrected else _corrected_disp(g)
            if draw not in out or prio[d] < prio[out[draw]]:
                out[draw] = d
    return out


def aihub_row_units(r: dict, _fp_units=None) -> int:
    """manifest 행 1개의 세대수 = 변환된 그래프 수(len graph_ids). 그 외 모두 0.

    ⚠️ 중복본(reason=duplicate)도 0 — byte-identical 사본이라 그 세대는 '원본' 행에서
    이미 세었다. 예전 규칙 '중복=원본(dup_of) 세대수'는 **같은 물리 세대를 사본 수만큼
    중복집계**하는 버그였다(증명: 중복 18,231행이 원본 9,893개를 가리켜 세대 24,949를
    재계상 → 전체 23,679 → 48,628로 부풀음). 세대는 '실제 추출된 고유 세대'만 센다(G와 동일).
    못 세는 행(비FP·미변환 보정필요)도 0. (_fp_units 인자는 옛 시그니처 호환용·미사용.)"""
    return len(r.get("graph_ids") or [])


# ── AI-Hub 「라벨 구성 × 처분」 분류 콤보 — T·G 검수 공용 단일 소스(ADR-0005) ──────
# 카테고리 = 데이터 특성(라벨 구성: dual/방만/구조만/objocr/중복/비FP) × 처분(사용/보정필요/제외).
# T검수(🏢)·G검수(🧩) 둘 다 이 '같은' 콤보를 써야 비교 가능(§3 불변식). G가 제 validation으로
# 제멋대로 todo/use/fix 나누던 것을 폐기하고 이걸로 통일.
AIHUB_LABEL = {
    ("use", "dual"): "✅ 사용 · dual(직접변환)",
    ("use", "dual_dedup_merge"): "✅ 사용 · dual(직접변환)",   # 중복라벨복구 → dual 통합
    ("use", "v2v_str_recovered"): "✅ 사용 · 방만→V2V STR복구",
    ("use", "v2v_spa_recovered"): "✅ 사용 · 구조만→V2V SPA복구",
    ("fix", "convert_failed"): "🛠 보정필요 · 변환실패(dual)",
    ("fix", "spa_only_pending"): "🛠 보정필요 · 방만(V2V 대기)",
    ("fix", "str_only_pending"): "🛠 보정필요 · 구조만(V2V 대기)",
    ("fix", "objocr"): "🛠 보정필요 · OBJ/OCR만(공간라벨 없음)",
    ("excl", "nonfp"): "🚫 제외 · 비-FP(평면도 아님)",
    ("excl", "duplicate"): "🚫 제외 · 중복(사본)",
}
AIHUB_LABEL_ORDER = list(dict.fromkeys(AIHUB_LABEL.values()))


def aihub_label_of(row: dict) -> str:
    """manifest 행 → 「라벨 구성 × 처분」 카테고리 라벨(데이터 특성 기준)."""
    return AIHUB_LABEL.get((row.get("disposition"), row.get("reason")),
                           f"{row.get('disposition')}·{row.get('reason')}")


def aihub_label_combo(manifest_path: Path) -> dict:
    """검수 분류 콤보(T·G 공용) — manifest 전량(받은 원본)을 「라벨 구성 × 처분」으로 집계.
    반환 {order:[라벨], draw:{라벨:도면}, unit:{라벨:세대}, sig2label:{지문:라벨},
          total_draw, total_unit}. 합 = 받은 원본(43,219). G검수도 이 분모·카테고리를 쓴다."""
    rows = []
    p = Path(manifest_path)
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                try:
                    rows.append(json.loads(ln))
                except Exception:  # noqa: BLE001
                    continue
    draw, unit, sig2label = Counter(), Counter(), {}
    for r in rows:
        lab = aihub_label_of(r)
        draw[lab] += 1
        unit[lab] += aihub_row_units(r)
        fp = r.get("fingerprint")
        if fp:
            sig2label[fp] = lab
    return {"order": [l for l in AIHUB_LABEL_ORDER if draw.get(l)],
            "draw": dict(draw), "unit": dict(unit), "sig2label": sig2label,
            "total_draw": sum(draw.values()), "total_unit": sum(unit.values())}


# provenance(라벨 구성) → Corrected 처분 라벨. 데이터 특성 기준(T §2와 동일 논리):
# dual·V2V복구 = 사용, objocr(이미지직접) = 보정필요. geomgraph 검증(역할미상 등)은 분류 축 아님(보정 상세).
GLINE_PROV_LABEL = {
    "dual": "✅ 사용 · dual(직접변환)",
    "spa_only": "✅ 사용 · 방만→V2V STR복구",
    "str_only": "✅ 사용 · 구조만→V2V SPA복구",
    "objocr": "🛠 보정필요 · OBJ/OCR만(이미지직접 검출)",
}
GLINE_LABEL_ORDER = list(dict.fromkeys(GLINE_PROV_LABEL.values())) + [
    "🛠 보정필요 · 미빌드(SPA보유·추출 전)", "🚫 제외 · 비-FP(평면도 아님)", "🚫 제외 · 중복(사본)"]


def corrected_label_combo(manifest_path: Path, graphs_dir: Path) -> dict:
    """G검수 분류 콤보 — **Corrected 실제 데이터**(corrected graphs)로 「라벨 구성 × 처분」 집계.
    T검수와 같은 카테고리 어휘·43,219 분모(받은 원본)지만, 처분·세대는 G가 실제 추출한 것:
    provenance(dual/방만/구조만/objocr)별 G 세대. 중복·비FP=manifest 제외. 미빌드=보정필요.
    ※ Parsed manifest 처분(변환실패·V2V대기)을 뿌리던 버그 폐기 — 그건 T 개념이지 G가 아님.
    세대 = G 그래프 수(중복행엔 0 → 더블카운트 없음). 반환 = aihub_label_combo와 동일 스키마."""
    # G 그래프: 지문(sig) → provenance, 세대수
    g_prov: dict[str, str] = {}
    g_units: Counter = Counter()
    if graphs_dir.is_dir():
        for f in graphs_dir.glob("*.json"):
            try:
                g = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            uid = g.get("unit_id") or f.stem               # house_FP_sig_uN
            sig = uid.split("_FP_")[-1].rsplit("_u", 1)[0] if "_FP_" in uid else uid
            g_prov[sig] = (g.get("provenance") or {}).get("source", "?")
            g_units[sig] += 1
    rows = []
    p = Path(manifest_path)
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                try:
                    rows.append(json.loads(ln))
                except Exception:  # noqa: BLE001
                    continue
    draw, unit, sig2label = Counter(), Counter(), {}
    for r in rows:
        reason, fp = r.get("reason"), r.get("fingerprint")
        if reason == "duplicate":
            lab = "🚫 제외 · 중복(사본)"
        elif reason == "nonfp":
            lab = "🚫 제외 · 비-FP(평면도 아님)"
        elif fp in g_prov:                                 # G가 빌드함 → provenance 특성으로 분류
            lab = GLINE_PROV_LABEL.get(g_prov[fp], "🛠 보정필요 · 미빌드(SPA보유·추출 전)")
        else:                                              # SPA계열인데 G 미빌드 → 보정필요
            lab = "🛠 보정필요 · 미빌드(SPA보유·추출 전)"
        draw[lab] += 1
        if reason not in ("duplicate", "nonfp") and fp in g_units:
            unit[lab] += g_units[fp]                       # 세대=G 그래프 수, 원본 행에만(중복=별 카테고리)
        sig2label[fp] = lab
    return {"order": [l for l in GLINE_LABEL_ORDER if draw.get(l)],
            "draw": dict(draw), "unit": dict(unit), "sig2label": sig2label,
            "total_draw": sum(draw.values()), "total_unit": sum(unit.values())}


def aihub_t_status(manifest_path: Path) -> dict:
    """Parsed AI-Hub 회계(정본 manifest) — 처분 use/fix/excl, 도면·세대 병기, by_house.
    corrected_status와 같은 접근법(x['use']=세대, x['draw']['use']=도면)으로 종합 비교에서 동일 처리.
    세대 규칙: use=Σlen(graph_ids), 그 외(보정필요·제외 중복·비FP)=0(못 세거나 원본서 이미 셈).
    ※ 중복본을 원본 세대수로 더하던 옛 규칙은 중복집계 버그라 폐기(aihub_row_units 참고).
    도면 = manifest 행 1개(받은 raw PNG 1장). [[dataset-essence-is-numbers-categories]]."""
    rows = []
    p = Path(manifest_path)
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                try:
                    rows.append(json.loads(ln))
                except Exception:  # noqa: BLE001
                    continue

    def _units(r):                                 # 행 1개의 세대 수(변환된 그래프만)
        return aihub_row_units(r)

    draw, unit = Counter(), Counter()
    house_draw: dict[str, Counter] = {}
    house_unit: dict[str, Counter] = {}
    for r in rows:
        disp = r.get("disposition")
        if disp not in ("use", "fix", "excl"):
            continue
        h = r.get("house") or "?"
        n = _units(r)
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
