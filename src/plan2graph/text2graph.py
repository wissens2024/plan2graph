"""P3-a Text-to-Graph — 자연어 요구 → 제약그래프 (규칙 기반 1차).

사업계획서 3-a: ① 모호한 요구("4인 가족 84㎡")를 구체 노드/엣지로 매핑
② hard(법규·필수) vs soft(선호) 제약 분리. 이 제약그래프가 생성 모델의 조건 입력.

1차는 규칙·키워드 파싱(노트북·GPU불요). 이후 LLM 파인튜닝으로 격상 가능(동일 출력 스키마).
출력 program은 model_baseline.generate가 그대로 소비. hard 제약은 규제 AI 루프가 검증.
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

# 방 타입 → 키워드
KEYWORDS = {
    "거실": ["거실", "리빙", "lvr"],
    "침실": ["침실", "방", "베드룸", "안방", "룸"],
    "화장실": ["화장실", "욕실", "화장", "변기", "bath", "toilet"],
    "주방": ["주방", "부엌", "키친", "주방식당"],
    "현관": ["현관", "입구"],
    "발코니": ["발코니", "베란다", "테라스"],
    "드레스룸": ["드레스룸", "드레스", "옷방", "옷장방"],
    "다목적공간": ["다목적", "알파룸", "서재", "팬트리", "다용도", "창고"],
    "실외기실": ["실외기", "세탁실"],
}
_KOR_NUM = {"한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3, "네": 4, "넷": 4,
            "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10}
ESSENTIAL = ["현관", "거실", "주방", "화장실"]   # 법규/필수(hard)


def _num_near(text: str, kws: list[str]) -> int | None:
    """개수 = 키워드 '바로 뒤' 숫자(침실 3개) 또는 '바로 앞 +개'(3개의 침실).
    면적(84㎡)이 개수로 오인되지 않도록, 단위(개/칸/실) 없으면 1~9만 인정."""
    nums = "|".join(_KOR_NUM)

    def _v(g):
        return int(g) if g.isdigit() else _KOR_NUM.get(g)
    for kw in kws:
        k = re.escape(kw)
        # 뒤: "침실 3개" / "방 두 개"
        m = re.search(rf"{k}\s*(\d+|{nums})\s*(개|칸|실|곳)?", text)
        if m:
            v = _v(m.group(1))
            if v is not None and (m.group(2) or v <= 9):
                return v
        # 앞: "3개의 침실" (단위 개 필수)
        m = re.search(rf"(\d+|{nums})\s*(개|칸|실)\s*(의\s*)?{k}", text)
        if m:
            v = _v(m.group(1))
            if v is not None:
                return v
    return None


def parse(text: str) -> dict:
    """자연어 → 제약그래프 dict."""
    t = text.strip()
    # 면적·가구원
    area = None
    am = re.search(r"(\d+)\s*(㎡|m2|m²|평)", t)
    if am:
        area = int(am.group(1)) * (3.3058 if am.group(2) == "평" else 1)
    fam = None
    fm = re.search(r"(\d+)\s*(인|명|식구)", t)
    if fm:
        fam = int(fm.group(1))

    program: dict[str, int] = {}
    for cls, kws in KEYWORDS.items():
        present = any(kw in t for kw in kws)
        n = _num_near(t, kws)
        if n is not None:
            program[cls] = n
        elif present:
            program[cls] = 1

    # 침실 추론(미지정 시 가구원 기반)
    if "침실" not in program:
        program["침실"] = 2 if (fam or 2) <= 2 else (3 if fam <= 4 else 4)
    # 필수 5요소 보장(hard)
    for e in ESSENTIAL:
        program.setdefault(e, 1)
    if area and area >= 100 and program.get("화장실", 1) < 2:
        program["화장실"] = 2          # 대형은 욕실 2 관례
    program.setdefault("발코니", 1)

    # 인접: hard(필수) / soft(선호)
    hard_adj = [["현관", "거실"]]                      # 진입
    if program.get("침실", 0) and program.get("화장실", 0) >= 2:
        hard_adj.append(["침실", "화장실"])            # 안방 전용욕실(있으면)
    soft_adj = []
    if re.search(r"LDK|엘디케이|트인|트여|오픈|개방|일체", t, re.I):
        soft_adj += [["거실", "주방"]]                 # LDK
    if re.search(r"안방|마스터|부부", t):
        soft_adj += [["침실", "드레스룸"], ["침실", "화장실"]]
    if "발코니" in program:
        soft_adj += [["거실", "발코니"]]

    privacy = {c: config.HIERARCHY.get("공간_" + c) for c in program}
    soft_pref = []
    for kw, lab in [("남향", "남향 채광"), ("채광", "채광 우선"), ("분리", "공간 분리"),
                    ("넓은", "넓은 거실"), ("수납", "수납 강조")]:
        if kw in t:
            soft_pref.append(lab)

    return {
        "request": text,
        "meta": {"area_m2": round(area, 1) if area else None, "family_size": fam},
        "program": program,
        "adjacency": {"hard": hard_adj, "soft": soft_adj},
        "privacy": privacy,
        "hard_constraints": [
            "필수 5요소(현관·거실·주방·화장실·침실)",
            "거실·침실 채광창(피난방화규칙 §17)",
            "현관에서 전 공간 도달(피난)",
        ],
        "soft_constraints": soft_pref + [f"{a}-{b} 인접 선호" for a, b in soft_adj],
    }


def _self_test() -> bool:
    cases = [
        "4인 가족, 84㎡, 침실 3개에 욕실 2개, 거실과 주방은 트인 LDK 구조, 안방에 드레스룸",
        "신혼부부 2인 59㎡, 방 2개, 남향 채광 좋은 거실",
        "30평대 다목적공간 있는 집",
    ]
    ok = True
    for c in cases:
        r = parse(c)
        p = r["program"]
        cond = all(e in p for e in ESSENTIAL) and p.get("침실", 0) >= 1
        ok = ok and cond
        print(f"· {c[:30]}…  program={p}  area={r['meta']['area_m2']} fam={r['meta']['family_size']}")
        print(f"    hard_adj={r['adjacency']['hard']} soft_adj={r['adjacency']['soft']}")
    print("text2graph self-test:", "OK" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import json
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        sys.exit(0 if _self_test() else 1)
    text = " ".join(sys.argv[1:]) or "4인 가족 84㎡ 침실3 욕실2 LDK"
    print(json.dumps(parse(text), ensure_ascii=False, indent=2))
