"""공개 평면도 데이터셋의 근중복(near-duplicate) 비율 — 세 데이터셋에 같은 척도로.

왜: 중복이 많으면 (a) '도면 수'가 고유 평면 수를 과장하고 (b) plan_id로 split하면
train/test가 오염된다. 우리 데이터(AI-Hub)에서 확인했는데, 이게 **우리만의 문제인지
공개 데이터셋 공통 문제인지**가 중요하다 — 후자면 분야 전체에 해당하는 발견이다.

척도를 통일해야 비교가 된다. AI-Hub 그래프는 폴리곤이 있지만 RPLAN/CubiCasa 변환본은
**방 중심점+면적**만 있으므로, 세 데이터셋 모두 **중심점 집합 서명**으로 센다:
  방 중심점을 평행이동·미러 정규화 → q px 양자화 → 역할 다중집합과 함께 해시.
(AI-Hub 전용 폴리곤 서명 = scripts/dedup_index.py. 숫자가 다르면 척도 차이다.)

읽기 전용. usage: python scripts/dup_across_datasets.py [--q 2.0]
"""
from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASETS = [
    ("AI-Hub (ours)", os.path.join(ROOT, "data", "staging", "parsed", "graphs")),
    ("RPLAN", os.path.join(ROOT, "data", "releases", "parsed", "global_rplan", "graphs")),
    ("CubiCasa5K", os.path.join(ROOT, "data", "releases", "parsed", "global_cubicasa", "graphs")),
]


def rooms_of(g):
    """스키마 2종 공통 추출 → [(cx, cy, role, area)]. 없으면 []."""
    out = []
    rs = g.get("rooms")
    if isinstance(rs, dict) and rs:                      # g-0.4 (AI-Hub)
        for r in rs.values():
            c = r.get("centroid")
            if not c:
                p = r.get("polygon")
                if not p:
                    continue
                c = [sum(x for x, _ in p) / len(p), sum(y for _, y in p) / len(p)]
            out.append((float(c[0]), float(c[1]),
                        r.get("role") or r.get("base") or "?", float(r.get("area_px") or 0)))
        return out
    lay = g.get("layout") or {}                          # 0.2 (RPLAN/CubiCasa 변환본)
    for n in lay.get("nodes") or []:
        c = n.get("centroid_px") or n.get("centroid")
        if not c:
            continue
        out.append((float(c[0]), float(c[1]),
                    n.get("type") or n.get("role") or "?", float(n.get("area_px2") or 0)))
    return out


def sig(rooms, q):
    """평행이동·미러 정규화 후 중심점 양자화 + 역할 → 해시. 미러 4후보 중 사전순 최소."""
    if len(rooms) < 2:
        return None
    xs = [r[0] for r in rooms]
    ys = [r[1] for r in rooms]
    mnx, mny, mxx, mxy = min(xs), min(ys), max(xs), max(ys)
    best = None
    for fx in (1, -1):
        for fy in (1, -1):
            ox = mnx if fx == 1 else mxx
            oy = mny if fy == 1 else mxy
            form = tuple(sorted(
                (round(fx * (x - ox) / q), round(fy * (y - oy) / q), role)
                for x, y, role, _a in rooms))
            if best is None or form < best:
                best = form
    return hashlib.sha1(repr(best).encode()).hexdigest()[:16]


def bucket(key, mod=20, test=1, val=1):
    h = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % mod
    return "test" if h < test else ("val" if h < test + val else "train")


def run(tag, d, q, limit=0):
    files = sorted(glob.glob(os.path.join(d, "*.json")))
    if limit:
        files = files[:limit]
    if not files:
        print("[%s] 그래프 없음: %s" % (tag, d))
        return
    groups = collections.defaultdict(list)
    skipped = 0
    for p in files:
        try:
            g = json.load(open(p, encoding="utf-8"))
        except Exception:
            skipped += 1
            continue
        s = sig(rooms_of(g), q)
        if s is None:
            skipped += 1
            continue
        groups[s].append(os.path.basename(p)[:-5])

    n = sum(len(v) for v in groups.values())
    uniq = len(groups)
    dup_rate = 100.0 * (1 - uniq / max(n, 1))
    in_dup = sum(len(v) for v in groups.values() if len(v) > 1)
    splits = {s: {bucket(m) for m in v} for s, v in groups.items()}
    bk = {m: bucket(m) for v in groups.values() for m in v}
    ntest = sum(1 for b in bk.values() if b == "test")
    leak = sum(1 for s, v in groups.items() if "train" in splits[s]
               for m in v if bk[m] == "test")
    big = sorted((len(v) for v in groups.values()), reverse=True)

    print("=" * 74)
    print("[%s] 도면 %d (스킵 %d) → **고유 평면 %d · 중복률 %.1f%%**"
          % (tag, n, skipped, uniq, dup_rate))
    print("   사본이 2장 이상인 도면 %d장(%.1f%%) · 최다 한 평면 %d장 · 상위10 합 %d장"
          % (in_dup, 100.0 * in_dup / max(n, 1), big[0] if big else 0, sum(big[:10])))
    print("   ⚠ plan_id 해시로 split하면 test %d장 중 **%d장(%.1f%%)이 train에 사본 보유**"
          % (ntest, leak, 100.0 * leak / max(ntest, 1)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", type=float, default=2.0, help="중심점 양자화(px)")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    print("중심점 서명 · 양자화 %.1fpx · 평행이동·미러 정규화 · 역할 포함" % args.q)
    for tag, d in DATASETS:
        run(tag, d, args.q, args.limit)


if __name__ == "__main__":
    main()
