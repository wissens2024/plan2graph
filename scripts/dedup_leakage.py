"""train/test 누수 + 고유 평면 수 실측 — 중복이 많은 데이터셋의 두 함정.

왜 필요한가: `build_token_dataset._bucket`은 **plan_id**를 해시해 split한다. 그 주석의
'누수 방지'는 재현성(같은 id는 늘 같은 split)을 뜻하고, **같은 평면의 사본이 train과 test로
흩어지는 것은 막지 못한다.** 중복률이 57%(gated는 74%)라 이건 실제로 일어난다.

같이 재는 두 번째 함정: **학습 코퍼스의 '도면 수'는 고유 평면 수가 아니다.** 사본 가중
없이 학습하면 몇 평면을 수백 번 본다.

읽기 전용. usage:
  python scripts/dedup_leakage.py                  # gated + parsed 전체
  python scripts/dedup_leakage.py --ids <json>     # 다른 id 목록으로
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGING = os.path.join(ROOT, "data", "staging")
INDEX = os.path.join(STAGING, "dedup_index.json")
GATED = os.path.join(STAGING, "korean_gated_ids.json")


def bucket(key, mod=20, test=1, val=1):
    """build_token_dataset._bucket 와 동일한 결정론 split."""
    h = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % mod
    return "test" if h < test else ("val" if h < test + val else "train")


def load_ids(path):
    d = json.load(open(path, encoding="utf-8"))
    if isinstance(d, dict):
        for k in ("ids", "plan_ids", "kept", "items"):
            if isinstance(d.get(k), list):
                return set(str(x) for x in d[k])
        return set(str(x) for x in d)
    return set(str(x) for x in d)


def report(tag, pool, sig_of):
    bk = {p: bucket(p) for p in pool}
    cnt = collections.Counter(bk.values())
    splits = collections.defaultdict(set)
    members = collections.defaultdict(list)
    for p in pool:
        s = sig_of[p]
        splits[s].add(bk[p])
        members[s].append(p)
    straddle = [s for s, v in splits.items() if len(v) > 1]
    leak = {b: [p for p in pool if bk[p] == b and "train" in splits[sig_of[p]]]
            for b in ("test", "val")}

    print("=" * 76)
    print("[%s] 도면 %d → **고유 평면 %d개** (평균 %.1f사본 · 중복률 %.1f%%)"
          % (tag, len(pool), len(members), len(pool) / max(len(members), 1),
             100.0 * (1 - len(members) / max(len(pool), 1))))
    print("  도면 단위 split: %s" % dict(cnt))
    print("  여러 split에 걸친 그룹 %d / %d (%.1f%%)"
          % (len(straddle), len(splits), 100.0 * len(straddle) / max(len(splits), 1)))
    for b in ("test", "val"):
        print("  ⚠ %s 도면 %d장 중 **%d장(%.1f%%)이 train에 사본이 있다**"
              % (b, cnt[b], len(leak[b]), 100.0 * len(leak[b]) / max(cnt[b], 1)))

    gcnt = collections.Counter()
    for s in splits:
        gcnt[bucket(s)] += len(members[s])
    print("  → **그룹 서명으로 해시하면** %s · 걸친 그룹 0" % dict(gcnt))

    hist = collections.Counter()
    for s, ms in members.items():
        hist[min(len(ms), 20)] += 1
    top = sorted(members.items(), key=lambda kv: -len(kv[1]))[:10]
    print("  사본 수 분포(그룹): %s   (20 = 20장 이상)"
          % dict(sorted(hist.items())))
    print("  상위 10 평면이 차지하는 도면 %d장 (%.1f%%) · 최다 한 평면 %d장"
          % (sum(len(v) for _s, v in top), 100.0 * sum(len(v) for _s, v in top) / len(pool),
             len(top[0][1]) if top else 0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default=INDEX)
    ap.add_argument("--ids", default=GATED, help="학습 id 목록 json")
    args = ap.parse_args()

    idx = json.load(open(args.index, encoding="utf-8"))
    sig_of = {m: s for s, v in idx["groups"].items() for m in v["members"]}

    if os.path.exists(args.ids):
        ids = [p for p in load_ids(args.ids) if p in sig_of]
        report("학습 코퍼스 %s" % os.path.basename(args.ids), ids, sig_of)
    report("parsed 전체", list(sig_of), sig_of)
    print("=" * 76)
    print("해석: 헤드라인 지표(strict·drawable·clean)는 eval_ar_geom이 무조건 생성으로 재므로")
    print("      test split을 읽지 않는다 → 그 숫자는 누수와 무관하다. 영향은 held-out GT")
    print("      비교 지표와 리뷰어 신뢰. 수정 = build_token_dataset._bucket 을 그룹 서명으로.")


if __name__ == "__main__":
    main()
