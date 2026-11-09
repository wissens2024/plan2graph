"""작업자 간 일치도(IAA) — 중복 도면이 만들어 준 자연 실험으로 측정한다.

왜 필요한가: 데이터를 공개하면 가장 먼저 받는 질문이 "주석자 간 일치도는?"다. 우리는
IAA용 중복 작업을 **설계하지 않았지만**, 같은 평면이 데이터셋에 여러 번 들어 있고 작업자
6명이 그 사본들을 모르고 각각 보정했다. 즉 **독립 이중 주석 쌍이 이미 존재한다.**

측정 대상 = 한 중복 그룹 안에서 **전파본이 아닌(= 사람이 직접 연) 보정본이 2개 이상**인 그룹.
전파본은 한 사람 판단의 사본이므로 반드시 제외한다(안 하면 자기 자신과 비교해 100%가 나온다).

비교는 그룹 정규 프레임에서 한다(`dedup.shape_sig`의 평행이동·미러 변환 → `corr_cells`).
방은 중심점 거리로 짝짓고, 짝지어진 방에서 역할 일치를 센다.

보고 지표:
  방 분해 일치   = 2·짝지어진 방 / (A 방 수 + B 방 수)   ← 합치기/나누기 판단이 같았나
  역할 일치      = 역할 같은 짝 / 짝지어진 방            ← 이름 붙이기가 같았나
  도면 완전 일치 = 방 수·위치·역할이 전부 같은 쌍의 비율   ← 가장 엄격(참고용)

읽기 전용. usage: python scripts/iaa_duplicates.py [--tol 15] [--gated-only]
"""
from __future__ import annotations

import argparse
import collections
import itertools
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from plan2graph.dedup import corr_cells, shape_sig  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGING = os.path.join(ROOT, "data", "staging")
PARSED = os.path.join(STAGING, "parsed", "graphs")
EDITS = os.path.join(STAGING, "corrected", "edits")
INDEX = os.path.join(STAGING, "dedup_index.json")
GATED = os.path.join(STAGING, "korean_gated_ids.json")


def load_gated(path):
    if not os.path.exists(path):
        return set()
    d = json.load(open(path, encoding="utf-8"))
    if isinstance(d, dict):
        for k in ("ids", "plan_ids", "kept", "items"):
            if isinstance(d.get(k), list):
                return set(str(x) for x in d[k])
        return set(str(x) for x in d)
    return set(str(x) for x in d)


def jread(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None


def match(a, b, tol):
    """정규 프레임 중심점으로 그리디 짝짓기 → (짝 수, 역할 같은 짝 수, 갈린 역할쌍)."""
    cand = []
    for i, (x, y, _r) in enumerate(a):
        for j, (x2, y2, _r2) in enumerate(b):
            d = max(abs(x - x2), abs(y - y2))
            if d <= tol:
                cand.append((d, i, j))
    cand.sort()
    ua, ub, n, same, diff = set(), set(), 0, 0, []
    for _d, i, j in cand:
        if i in ua or j in ub:
            continue
        ua.add(i)
        ub.add(j)
        n += 1
        if a[i][2] == b[j][2]:
            same += 1
        else:
            diff.append(tuple(sorted((a[i][2], b[j][2]))))
    return n, same, diff


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tol", type=float, default=15.0, help="방 짝짓기 허용오차(px)")
    ap.add_argument("--gated-only", action="store_true", help="학습 코퍼스 도면만")
    ap.add_argument("--max-pairs", type=int, default=6, help="그룹당 비교할 쌍 수 상한")
    args = ap.parse_args()

    idx = json.load(open(INDEX, encoding="utf-8"))
    gated = load_gated(GATED)

    tot = collections.Counter()
    conf = collections.Counter()      # 역할 불일치 쌍 (a, b) → 횟수
    per_group = []
    for gid, g in idx["groups"].items():
        corrected = g.get("corrected") or []
        if len(corrected) < 2:
            continue
        direct = []
        for pid in corrected:
            if args.gated_only and pid not in gated:
                continue
            ge = jread(os.path.join(EDITS, "%s.json" % pid))
            if ge is None or (ge.get("meta") or {}).get("propagated"):
                continue          # 전파본 = 남의 판단 사본 → IAA 표본 아님
            gp = jread(os.path.join(PARSED, "%s.json" % pid))
            if gp is None:
                continue
            _sig, t = shape_sig(gp)
            if t is None:
                continue
            cells, _roles = corr_cells(ge, t)
            if cells:
                direct.append((pid, cells))
        if len(direct) < 2:
            continue
        tot["groups"] += 1
        gp_pairs = 0
        for (pa, ca), (pb, cb) in itertools.combinations(direct, 2):
            if gp_pairs >= args.max_pairs:
                break
            gp_pairs += 1
            n, same, diff = match(ca, cb, args.tol)
            tot["pairs"] += 1
            tot["rooms_a"] += len(ca)
            tot["rooms_b"] += len(cb)
            tot["matched"] += n
            tot["role_same"] += same
            tot["exact"] += 1 if (len(ca) == len(cb) == n == same) else 0
            for key in diff:
                conf[key] += 1
            per_group.append((gid, len(ca), len(cb), n, same))

    R = tot["rooms_a"] + tot["rooms_b"]
    M = tot["matched"] or 1
    print("=" * 76)
    print("작업자 간 일치도 — 같은 도면을 2명 이상이 **독립적으로** 보정한 쌍")
    print("  대상 그룹 %d · 비교 쌍 %d · 방 %d개(A %d + B %d)%s"
          % (tot["groups"], tot["pairs"], R, tot["rooms_a"], tot["rooms_b"],
             "  [학습 코퍼스만]" if args.gated_only else ""))
    print("-" * 76)
    print("  방 분해 일치   %6.1f%%   (2·짝 %d / 방 %d) — 합치기·나누기 판단이 같았나"
          % (200.0 * tot["matched"] / max(R, 1), tot["matched"], R))
    print("  역할 일치      %6.1f%%   (%d / 짝 %d) — 짝지어진 방의 이름이 같았나"
          % (100.0 * tot["role_same"] / M, tot["role_same"], tot["matched"]))
    print("  도면 완전 일치 %6.1f%%   (%d / %d 쌍) — 가장 엄격(참고)"
          % (100.0 * tot["exact"] / max(tot["pairs"], 1), tot["exact"], tot["pairs"]))
    print("-" * 76)
    print("역할이 갈린 쌍 상위 (전체 %d건)" % sum(conf.values()))
    for (a, b), n in conf.most_common(15):
        print("  %-12s ↔ %-12s %5d  (%.1f%%)"
              % (a, b, n, 100.0 * n / max(sum(conf.values()), 1)))

    # 짝짓기는 위치로만 하므로 역할을 같게 보면 그 쌍만 일치로 바뀐다 → 누적 보정은 정확하다.
    print("-" * 76)
    print("불일치 중 '표기·도출로 설명되는' 부분을 걷어내면")
    ladder = [(("팬트리", "펜트리"), "철자 통일(코드 alias로 해결)"),
              (("안방", "침실"), "안방 = 침실 중 최대 1개(도출값)"),
              (("욕실", "화장실"), "에디터에 화장실이 없다 = 손 안 댄 parsed 라벨")]
    acc = tot["role_same"]
    print("  %-42s %6.2f%%" % ("원본", 100.0 * acc / M))
    for key, why in ladder:
        acc += conf.get(tuple(sorted(key)), 0)
        print("  + %-40s %6.2f%%   %s"
              % ("%s↔%s 동일시" % key, 100.0 * acc / M, why))


if __name__ == "__main__":
    main()
