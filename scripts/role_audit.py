#!/usr/bin/env python3
"""role_audit — 우리 역할 배정이 맞는지 AI-Hub OCR 인쇄 라벨로 감사한다. 읽기 전용.

조인(2026-11-13 수정): plan_id = {house}_FP_{sig}_u{n} → inspect_excluded.build_index(split)
가 sig → {SPA/STR/OBJ/OCR: 원천 zip 엔트리(PNG)} 를 준다. **bbox+글자가 들어있는 COCO JSON은
원천(PNG) zip이 아니라 라벨 zip(TL_OCR/VL_OCR)에 있다.** 그래서 sig → OCR 9자리 key →
라벨 zip의 /{house}_FP_OCR_{key}.json 으로 두 단계로 조인한다.

한 sheet(sig)가 층평면도면 단위세대별로 _u0.._uN 로 쪼개져 있다. OCR 글자는 sheet 전체에
찍히므로 **같은 sig의 모든 유닛 방을 한 풀로 합쳐** 판정한다(안 합치면 다른 유닛 글자가
전부 '방 밖'으로 샌다).

글자 중심이 어느 방에도 안 들어가면 '방 밖'으로 따로 세고 일치율에서 뺀다.
여러 방이 겹치면 가장 작은 방을 택한다.
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys
import zipfile

sys.path.insert(0, "/home/ju/plan2graph/src")
sys.path.insert(0, "/home/ju/plan2graph")

from shapely.geometry import Point, Polygon        # noqa: E402
from shapely.strtree import STRtree                # noqa: E402

from plan2graph import inspect_excluded as ix      # noqa: E402
from plan2graph.wallcycle_codec import ROLE_ALIAS   # noqa: E402

EDITS = "/home/ju/plan2graph/data/staging/corrected/edits"
RAW = "/home/ju/plan2graph/data/raw/aihub/01-1.정식개방데이터/"
OCR_LABEL_ZIPS = [RAW + "Training/02.라벨링데이터/TL_OCR.zip",
                  RAW + "Validation/02.라벨링데이터/VL_OCR.zip"]

# ── 인쇄 표기 → 우리 역할 어휘 (화이트리스트). 여기 없는 문자열은 '미분류'로
#    따로 세고 일치율에서 제외한다(문서 텍스트·조각난 글자상자·어휘 없는 공간).
NORM = {
    # 침실 계열
    "침실": "침실", "안방": "안방", "부부침실": "안방", "주침실": "안방",
    "자녀방": "침실", "서재": "침실", "할머니방": "침실", "노인방": "침실",
    # 거실
    "거실": "거실", "가족실": "거실", "거실/침실": "거실", "거실침실": "거실",
    # 주방
    "주방": "주방", "주방/식당": "주방", "주방및식당": "주방", "식당": "주방",
    "보조주방": "주방", "주방/식당/거실": "주방",
    # 욕실(화장실 표기 포함)
    "욕실": "욕실", "화장실": "화장실", "부부욕실": "욕실", "공용욕실": "욕실",
    "가족욕실": "욕실", "샤워실": "욕실", "전용욕실": "전용욕실",
    "전용화장실": "전용화장실", "장애인화장실": "화장실", "욕실/화장실": "욕실",
    # 발코니·테라스
    "발코니": "발코니", "외부발코니": "발코니", "개방형발코니": "발코니",
    "서비스발코니": "발코니", "테라스": "테라스", "데크": "테라스",
    # 현관·전실·복도
    "현관": "현관", "메인현관": "현관", "전실": "전실", "욕실전실": "전실",
    "비상용승강기전실": "전실", "복도": "복도", "통로": "복도",
    # 수납·다목적
    "드레스룸": "드레스룸", "드레스실": "드레스룸", "워크인클로젯": "드레스룸",
    "펜트리": "펜트리", "팬트리": "펜트리",
    "다용도실": "다목적공간", "세탁실": "세탁실", "창고": "다목적공간",
    "보일러실": "다목적공간", "다락": "다목적공간", "다락방": "다목적공간",
    "파우더룸": "파우더룸",
    # 설비·기타 공간
    "실외기실": "실외기실", "실외기": "실외기실", "대피공간": "대피공간",
    "알파룸": "알파룸", "알파공간": "알파룸", "계단실": "계단실",
    "ELEV.홀": "엘리베이터홀", "ELEV홀": "엘리베이터홀", "ELEV.HALL": "엘리베이터홀",
    "EVEL.홀": "엘리베이터홀", "ELVE.홀": "엘리베이터홀", "EVEV.홀": "엘리베이터홀",
    "홀": "엘리베이터홀", "ELEV.": "엘리베이터홀", "ELEV": "엘리베이터홀",
    "EVEL.": "엘리베이터홀", "E.V": "엘리베이터홀", "E.V.": "엘리베이터홀",
}

# 느슨한 비교용 '같은 공간 계열'
FAMILY = {
    "침실": "침실", "안방": "침실",
    "욕실": "욕실", "화장실": "욕실", "전용욕실": "욕실", "전용화장실": "욕실",
    "발코니": "발코니", "테라스": "발코니",
    "다목적공간": "다목적", "세탁실": "다목적", "펜트리": "다목적",
    "현관": "현관/전실", "전실": "현관/전실",
    "엘리베이터홀": "코어", "계단실": "코어",
    "드레스룸": "드레스룸", "파우더룸": "파우더룸", "거실": "거실",
    "주방": "주방", "복도": "복도", "실외기실": "실외기실",
    "대피공간": "대피공간", "알파룸": "알파룸",
}


def fam(r):
    return FAMILY.get(r, r)


def canon(role):
    """우리 역할 문자열의 표기 통일(코덱 ROLE_ALIAS: 팬트리→펜트리 등). 정책이 아니라 철자."""
    r = (role or "").strip()
    return ROLE_ALIAS.get(r, r) or "(없음)"


def main():
    n_plan = int(sys.argv[1]) if len(sys.argv) > 1 else 400

    idx = {}
    for split in ("Training", "Validation"):
        idx.update(ix.build_index(split))

    # 라벨 zip 색인: entry 이름 → zip 경로
    label_at = {}
    for p in OCR_LABEL_ZIPS:
        for n in zipfile.ZipFile(p).namelist():
            if "_FP_OCR_" in n:
                label_at[n] = p
    zc = {}

    def zf(p):
        if p not in zc:
            zc[p] = zipfile.ZipFile(p)
        return zc[p]

    pat = re.compile(r"^([A-Z]+)_FP_([0-9a-f]{8}_\d+)(?:_u(\d+))?\.json$")
    sheets = collections.defaultdict(list)
    for f in os.listdir(EDITS):
        m = pat.match(f)
        if m:
            sheets[m.group(2)].append(f)

    joinable = []
    for sig, fs in sheets.items():
        d = idx.get(sig)
        if not d or "OCR" not in d:
            continue
        ent = "/%s_FP_OCR_%s.json" % (d["OCR"]["house"], d["OCR"]["key"])
        if ent in label_at:
            joinable.append((sig, sorted(fs), ent))
    joinable.sort()
    import random
    random.Random(7).shuffle(joinable)
    print("sheet 총 %d · OCR 조인 가능 %d (%.1f%%) · 표본 %d"
          % (len(sheets), len(joinable), 100.0 * len(joinable) / len(sheets),
             min(n_plan, len(joinable))), file=sys.stderr)

    agree = collections.Counter()
    famcnt = collections.Counter()
    conf = collections.Counter()
    by_role = collections.defaultdict(collections.Counter)
    by_text = collections.defaultdict(collections.Counter)
    by_prov = collections.defaultdict(collections.Counter)
    by_house = collections.defaultdict(collections.Counter)
    unmapped = collections.Counter()
    room_vote = collections.Counter()
    per_sheet = []
    n_sheet = n_room = n_txt = n_map = 0

    for sig, fs, ent in joinable[:n_plan]:
        try:
            coco = json.loads(zf(label_at[ent]).read(ent))
        except Exception:
            continue
        polys, roles, provs, areas = [], [], [], []
        house = fs[0].split("_")[0]
        for f in fs:
            try:
                g = json.load(open(os.path.join(EDITS, f), encoding="utf-8"))
            except Exception:
                continue
            spa = ((g.get("provenance") or {}).get("spa")) or "?"
            for r in (g.get("rooms") or {}).values():
                p = r.get("polygon") or []
                if len(p) < 3:
                    continue
                q = Polygon([(x, y) for x, y in p]).buffer(0)
                if q.is_empty or q.geom_type != "Polygon":
                    continue
                polys.append(q)
                roles.append(canon(r.get("role")))
                provs.append(spa)
                areas.append(q.area)
        if not polys:
            continue
        n_sheet += 1
        n_room += len(polys)
        tree = STRtree(polys)
        hits_per_room = collections.defaultdict(list)
        s_ok = s_bad = 0
        for a in coco.get("annotations", []):
            txt = ((a.get("attributes") or {}).get("OCR") or "").strip()
            txt = re.sub(r"\s+", "", txt)
            b = a.get("bbox")
            if not txt or not b:
                continue
            n_txt += 1
            t = NORM.get(txt)
            if t is None:
                unmapped[txt] += 1
                continue
            n_map += 1
            c = Point(b[0] + b[2] / 2.0, b[1] + b[3] / 2.0)
            cand = [i for i in tree.query(c) if polys[i].contains(c)]
            if not cand:
                agree["방 밖"] += 1
                by_text[t]["방 밖"] += 1
                continue
            i = min(cand, key=lambda j: areas[j])
            hit, prov = roles[i], provs[i]
            ok = (hit == t)
            fok = (fam(hit) == fam(t))
            agree["일치" if ok else "불일치"] += 1
            famcnt["일치" if fok else "불일치"] += 1
            by_role[hit]["일치" if ok else "불일치"] += 1
            by_role[hit]["계열일치" if fok else "계열불일치"] += 1
            by_text[t]["일치" if ok else "불일치"] += 1
            by_prov[prov]["일치" if ok else "불일치"] += 1
            by_prov[prov]["계열일치" if fok else "계열불일치"] += 1
            # 구성 교란 통제: '안방 세분'·'기타 미배정' 두 계통을 뺀 비교
            if not ({t, hit} == {"침실", "안방"} or hit == "기타"):
                by_prov[prov]["통제일치" if ok else "통제불일치"] += 1
            # 같은 글자에 대해 역할별 노출이 다르니, 인쇄이름별로도 쪼갠다
            by_prov[prov]["t:" + t + ":" + ("o" if ok else "x")] += 1
            by_house[house]["일치" if ok else "불일치"] += 1
            if ok:
                s_ok += 1
            else:
                s_bad += 1
                conf[(t, hit)] += 1
            hits_per_room[i].append((t, hit))
        for k, v in hits_per_room.items():
            ts = {t for t, _ in v}
            role = v[0][1]
            room_vote["글자1개" if len(v) == 1 else "글자여럿"] += 1
            if len(ts) > 1:
                room_vote["한방안글자불일치"] += 1
            maj = collections.Counter(t for t, _ in v).most_common(1)[0][0]
            room_vote["방단위일치" if maj == role else "방단위불일치"] += 1
        per_sheet.append((sig, s_ok, s_bad))

    def pct(a, b):
        return (100.0 * a / (a + b)) if (a + b) else float("nan")

    print("\n== 규모 ==")
    print("조인 sheet %d · 방 폴리곤 %d · OCR 글자 %d · 방이름으로 매핑된 글자 %d (%.1f%%) · 미분류 %d"
          % (n_sheet, n_room, n_txt, n_map, 100.0 * n_map / max(1, n_txt),
             sum(unmapped.values())))
    inside = agree["일치"] + agree["불일치"]
    print("매핑된 글자 중 방 안 %d (%.1f%%) · 방 밖 %d (%.1f%%)"
          % (inside, 100.0 * inside / max(1, n_map),
             agree["방 밖"], 100.0 * agree["방 밖"] / max(1, n_map)))

    print("\n== 역할 일치율(방 안 글자 %d개 기준) ==" % inside)
    print("엄격(정확히 같은 역할): 일치 %d / 불일치 %d → %.1f%%"
          % (agree["일치"], agree["불일치"], pct(agree["일치"], agree["불일치"])))
    print("계열(침실≈안방·욕실≈화장실 등): 일치 %d / 불일치 %d → %.1f%%"
          % (famcnt["일치"], famcnt["불일치"], pct(famcnt["일치"], famcnt["불일치"])))
    print("방 단위(한 방에 글자 여럿이면 과반): 일치 %d / 불일치 %d → %.1f%%"
          % (room_vote["방단위일치"], room_vote["방단위불일치"],
             pct(room_vote["방단위일치"], room_vote["방단위불일치"])))
    print("  글자 1개인 방 %d · 글자 여럿인 방 %d(그중 서로 다른 이름 %d)"
          % (room_vote["글자1개"], room_vote["글자여럿"], room_vote["한방안글자불일치"]))

    print("\n== 가장 많이 틀린 조합 (인쇄 → 우리 역할), 상위 25 ==")
    for (t, hit), c in conf.most_common(25):
        print("   %-8s → %-8s %6d" % (t, hit, c))

    print("\n== 불일치 %d개 분해 ==" % agree["불일치"])
    SMALL = {"욕실", "화장실", "전용욕실", "전용화장실", "파우더룸", "드레스룸",
             "펜트리", "세탁실", "다목적공간", "대피공간", "실외기실"}
    BIG = {"안방", "침실", "거실", "주방", "복도", "발코니", "전실", "현관"}
    cls = collections.Counter()
    for (t, hit), c in conf.items():
        if {t, hit} == {"침실", "안방"}:
            cls["A 안방/침실 표기 차이(우리가 안방으로 세분)"] += c
        elif {t, hit} == {"테라스", "발코니"}:
            cls["B 테라스/발코니 표기 차이"] += c
        elif hit == "기타":
            cls["C 우리 역할이 '기타'(역할 미배정)"] += c
        elif fam(t) == fam(hit):
            cls["D 같은 계열 안 표기 차이(욕실/화장실 등)"] += c
        elif t in SMALL and hit in BIG:
            cls["E 작은 방이 큰 방에 먹힘(폴리곤 흡수 의심)"] += c
        else:
            cls["F 나머지(진짜 역할 혼동)"] += c
    for k, c in sorted(cls.items()):
        print("   %-42s %6d (%4.1f%%)" % (k, c, 100.0 * c / max(1, agree["불일치"])))
    excl_a = agree["불일치"] - cls["A 안방/침실 표기 차이(우리가 안방으로 세분)"]
    print("   → A(안방 세분)만 빼면 엄격 일치율 %.1f%%  [%d / %d]"
          % (100.0 * agree["일치"] / (agree["일치"] + excl_a), agree["일치"],
             agree["일치"] + excl_a))
    print("   서로 다른 (인쇄→역할) 조합 %d종 · 상위 25종이 불일치의 %.1f%%"
          % (len(conf), 100.0 * sum(c for _, c in conf.most_common(25))
             / max(1, agree["불일치"])))

    print("\n== 우리 역할별 일치율 ==")
    print("   %-10s %7s %7s %7s %7s" % ("우리역할", "글자수", "엄격%", "계열%", "불일치"))
    for r, c in sorted(by_role.items(), key=lambda kv: -(kv[1]["일치"] + kv[1]["불일치"])):
        n = c["일치"] + c["불일치"]
        if n < 5:
            continue
        print("   %-10s %7d %7.1f %7.1f %7d"
              % (r, n, pct(c["일치"], c["불일치"]),
                 pct(c["계열일치"], c["계열불일치"]), c["불일치"]))

    print("\n== 인쇄된 이름별 ==")
    print("   %-10s %7s %7s %7s" % ("인쇄이름", "방안글자", "엄격%", "방밖"))
    for t, c in sorted(by_text.items(), key=lambda kv: -(kv[1]["일치"] + kv[1]["불일치"])):
        n = c["일치"] + c["불일치"]
        if n < 5:
            continue
        print("   %-10s %7d %7.1f %7d" % (t, n, pct(c["일치"], c["불일치"]), c["방 밖"]))

    print("\n== provenance.spa 별 일치율 (핵심 질문: V2V 검출 방이 더 틀리나) ==")
    print("   %-10s %8s %8s %8s %8s %8s"
          % ("spa", "글자수", "엄격%", "계열%", "통제n", "통제%"))
    provs_sorted = sorted(by_prov.items(),
                          key=lambda kv: -(kv[1]["일치"] + kv[1]["불일치"]))
    for p, c in provs_sorted:
        n = c["일치"] + c["불일치"]
        print("   %-10s %8d %8.1f %8.1f %8d %8.1f"
              % (p, n, pct(c["일치"], c["불일치"]), pct(c["계열일치"], c["계열불일치"]),
                 c["통제일치"] + c["통제불일치"], pct(c["통제일치"], c["통제불일치"])))
    print("   (통제 = 안방/침실 표기쌍과 우리 역할 '기타'를 뺀 나머지)")
    print("\n   인쇄이름 × provenance.spa 엄격 일치율 (같은 글자끼리 비교)")
    head = [p for p, _ in provs_sorted]
    print("   %-10s" % "인쇄이름" + "".join("%16s" % p for p in head))
    tot = collections.Counter()
    for t, c in by_text.items():
        tot[t] = c["일치"] + c["불일치"]
    for t, _ in tot.most_common(14):
        line = "   %-10s" % t
        for p in head:
            cc = by_prov[p]
            a, b = cc["t:" + t + ":o"], cc["t:" + t + ":x"]
            line += "%16s" % ("%5d %5.1f%%" % (a + b, pct(a, b)) if a + b else "-")
        print(line)

    print("\n== 주택유형(house)별 엄격 일치율 ==")
    for h, c in sorted(by_house.items(), key=lambda kv: -(kv[1]["일치"] + kv[1]["불일치"])):
        n = c["일치"] + c["불일치"]
        print("   %-6s %8d %8.1f" % (h, n, pct(c["일치"], c["불일치"])))

    print("\n== 도면(sheet) 단위 분포 ==")
    use = [(s, a, b) for s, a, b in per_sheet if a + b >= 3]
    print("   글자 3개 이상인 sheet %d장" % len(use))
    for lo, hi in ((100, 101), (90, 100), (75, 90), (50, 75), (0, 50)):
        k = [1 for _, a, b in use if lo <= 100.0 * a / (a + b) < hi]
        print("   일치율 %3d~%3d%%: %5d장 (%.1f%%)"
              % (lo, hi, len(k), 100.0 * len(k) / max(1, len(use))))
    zero = [1 for _, a, b in per_sheet if a + b == 0]
    print("   방 안 글자가 0개인 sheet %d장" % len(zero))

    print("\n== 미분류(일치율 계산 제외) 상위 25 ==")
    for t, c in unmapped.most_common(25):
        print("   %-14s %6d" % (t, c))


if __name__ == "__main__":
    main()
