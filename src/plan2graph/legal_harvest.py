"""법규 수집기 — 주거·공간배치 관련 건축 법령을 API로 대량 수집해 규정 DB 구축.

사업계획서 1단계 "모듈형 법규 규칙 DB": 강행규정을 우선 선별 → 예외조항 확장.
여기서는 핵심 법령군을 훑어 '수치/강행 규정을 담은 조문'을 추출해 카탈로그화한다.
이 카탈로그가 SWRL/규칙 변환의 원천이며, 실제 검사 구현(rules_legal.RULES)은 그 부분집합.

산출: legal/catalog.json (법령×조문 규정 카탈로그) — 근거 추적·확장의 기반.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph import law_api  # noqa: E402

# 주거 공간배치·건축 인허가에 직접 관련된 핵심 법령군
TARGET_LAWS = [
    "건축법",
    "건축법 시행령",
    "건축법 시행규칙",
    "건축물의 피난·방화구조 등의 기준에 관한 규칙",
    "건축물의 설비기준 등에 관한 규칙",
    "주택법",
    "주택건설기준 등에 관한 규정",
    "주택건설기준 등에 관한 규칙",
    "주차장법 시행령",
    "장애인·노인·임산부 등의 편의증진 보장에 관한 법률 시행령",
]

# 수치·단위(규정의 정량 신호)
_UNIT = re.compile(
    r"\d+(?:\.\d+)?\s?(제곱미터|㎡|평방미터|세제곱미터|미터|미만|센티미터|센티|밀리미터"
    r"|\bm\b|\bcm\b|\bmm\b|분의|퍼센트|%|명|대|개소|층|시간|분|도|배|인)")
# 강행 의무 표현
_OBLIG = re.compile(r"(하여야 한다|해야 한다|이상|이하|초과|미만|금지|설치해야|"
                    r"설치하여야|확보|두어야|않아야|아니 된다|이내)")
# 공간배치 관련 키워드(우선순위 태깅)
_SPACE_KW = ("거실", "침실", "채광", "환기", "피난", "계단", "복도", "난간", "경계벽",
             "면적", "반자", "출입구", "방화", "직통", "대피", "일조", "차음", "주차",
             "현관", "발코니", "승강기", "경사로", "단차")
# 절차·행정 규정(공간 설계 강행규정 아님) — 제목 기준 분류
_PROCEDURAL = ("허가", "신고", "승인", "등록", "예치금", "공개", "설계", "감리", "위원회",
               "벌칙", "과태료", "수수료", "보험", "협회", "권한", "위임", "청문",
               "용도변경", "정의", "적용", "심의", "변경", "유지·관리", "점검", "이행강제금",
               "표시·광고", "분양", "공급", "사업계획", "조경")
# 방-단위 검사에 직접 쓰일 핵심 설계 강행 태그
_DESIGN_TAGS = ("거실", "침실", "채광", "환기", "피난", "계단", "복도", "난간",
                "경계벽", "반자", "직통", "대피", "발코니", "경사로", "단차", "현관")


MANIFEST = ROOT / "legal" / "law_manifest.json"


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _save_manifest(laws_meta: list[dict]) -> None:
    """수집 시점의 각 법령 버전(MST·시행일)을 기록 — 개정 감지 기준."""
    import json
    cur = {}
    if MANIFEST.exists():
        cur = json.loads(MANIFEST.read_text(encoding="utf-8")).get("laws", {})
    for lm in laws_meta:
        cur[lm["name"]] = {"mst": lm["mst"], "효력시행": lm.get("효력시행"),
                           "checked_at": _now()}
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps({"updated_at": _now(), "laws": cur},
                                   ensure_ascii=False, indent=2), encoding="utf-8")


def check_updates() -> list[dict]:
    """현행 법령(force 재조회)의 MST/시행일을 manifest와 비교 → 개정된 법령 목록.
    법령 개정 시 새 MST·시행일자가 발급되므로 이를 변경 신호로 사용.
    """
    import json
    manifest = (json.loads(MANIFEST.read_text(encoding="utf-8")).get("laws", {})
                if MANIFEST.exists() else {})
    changes = []
    for name in TARGET_LAWS:
        res = law_api.search_law(name, force=True)  # 캐시 무시, 현행 조회
        hit = next((r for r in res if r["name"] == name), res[0] if res else None)
        if not hit:
            continue
        old = manifest.get(hit["name"])
        if old is None:
            changes.append({"law": hit["name"], "status": "new",
                            "mst": hit["mst"], "효력시행": hit.get("효력시행")})
        elif old.get("mst") != hit["mst"]:
            changes.append({"law": hit["name"], "status": "amended",
                            "old_mst": old.get("mst"), "new_mst": hit["mst"],
                            "old_시행": old.get("효력시행"), "new_시행": hit.get("효력시행")})
    return changes


def refresh(changes: list[dict] | None = None) -> dict:
    """개정 감지된 법령의 원문 캐시를 갱신하고 카탈로그를 재수집.
    changes 미지정 시 check_updates()로 자동 감지. 변경 없으면 재수집 생략.
    """
    import json
    changes = check_updates() if changes is None else changes
    if not changes:
        return {"changed": 0, "message": "현행 법령과 일치 — 갱신 불필요"}
    # 변경 법령의 본문 캐시 삭제(다음 articles() 호출 시 force 재조회 위해)
    for c in changes:
        mst = c.get("new_mst") or c.get("mst")
        cp = law_api.CACHE / f"law_{mst}.xml"
        if cp.exists():
            cp.unlink()
    old_cat = (json.loads((ROOT / "legal" / "catalog.json").read_text(encoding="utf-8"))
               if (ROOT / "legal" / "catalog.json").exists() else {"provisions": []})
    old_keys = {(p["law"], p["article_no"]) for p in old_cat.get("provisions", [])}
    res = harvest(verbose=False)
    new_cat = json.loads((ROOT / "legal" / "catalog.json").read_text(encoding="utf-8"))
    new_keys = {(p["law"], p["article_no"]) for p in new_cat["provisions"]}
    return {"changed_laws": [c["law"] for c in changes],
            "added": len(new_keys - old_keys), "removed": len(old_keys - new_keys),
            "n_provisions": res["provisions"]}


def _is_provision(text: str) -> bool:
    return bool((_UNIT.search(text) or "분의" in text) and _OBLIG.search(text))


def _tags(text: str) -> list[str]:
    return [k for k in _SPACE_KW if k in text]


def harvest(verbose: bool = True) -> dict:
    """핵심 법령군을 훑어 규정 조문 카탈로그 생성."""
    laws_meta = []
    catalog = []
    for name in TARGET_LAWS:
        res = law_api.search_law(name)
        # 정확명 우선
        hit = next((r for r in res if r["name"] == name), res[0] if res else None)
        if not hit:
            if verbose:
                print(f"  [미발견] {name}")
            continue
        arts = law_api.articles(hit["mst"])
        prov = 0
        for a in arts:
            if not a["text"] or not _is_provision(a["text"]):
                continue
            tags = _tags(a["text"])
            if not tags:           # 공간배치 무관 규정은 제외(소음·구조계산 등)
                continue
            prov += 1
            title = a["title"] or ""
            is_proc = any(k in title for k in _PROCEDURAL)
            is_design = (not is_proc) and any(t in _DESIGN_TAGS for t in tags)
            catalog.append({
                "law": hit["name"], "mst": hit["mst"], "law_id": hit["law_id"],
                "article_no": a["no"], "title": a["title"],
                "kind": "procedural" if is_proc else ("design" if is_design else "general"),
                "tags": tags,
                "numbers": re.findall(r"\d+(?:분의\s?\d+)?(?:\.\d+)?\s?[^\s,。.]{0,4}",
                                      a["text"])[:8],
                "text": a["text"][:500],
            })
        laws_meta.append({"name": hit["name"], "mst": hit["mst"],
                          "n_articles": len(arts), "n_provisions": prov})
        if verbose:
            print(f"  {hit['name']:42} 조문 {len(arts):3} → 규정 {prov}")
    import json
    out = ROOT / "legal" / "catalog.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    n_design = sum(1 for c in catalog if c["kind"] == "design")
    payload = {
        "schema": "plan2graph-legal-catalog/0.1",
        "source": "국가법령정보센터 Open API",
        "harvested_at": _now(),
        "description": "주거·공간배치 관련 정량 규정 조문 카탈로그. SWRL/기계규칙 변환의 원천. "
                       "kind=design(방·공간 설계 강행) / procedural(허가·신고 등 절차) / general. "
                       "실제 구현 규칙은 legal/rules.json.",
        "laws": laws_meta,
        "n_provisions": len(catalog),
        "n_design": n_design,
        "provisions": catalog,
    }
    _save_manifest(laws_meta)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"laws": len(laws_meta), "provisions": len(catalog), "path": str(out)}


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import json
    cmd = sys.argv[1] if len(sys.argv) > 1 else "harvest"
    if cmd == "check":      # 개정 여부만 확인(현행 재조회)
        ch = check_updates()
        print("개정 감지:" if ch else "현행과 일치 — 변경 없음")
        for c in ch:
            print(" ", json.dumps(c, ensure_ascii=False))
    elif cmd == "refresh":  # 개정분 재수집
        print(json.dumps(refresh(), ensure_ascii=False, indent=2))
    else:                   # harvest (최초 수집)
        print("주거·건축 법령 수집 중...")
        r = harvest()
        print(f"\n수집 완료: 법령 {r['laws']}개 · 규정 {r['provisions']}개 → {r['path']}")
