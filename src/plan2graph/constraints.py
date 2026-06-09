"""제약 레지스트리 + 채굴기(miner) — 룰을 '코드'가 아니라 '데이터'로.

원칙: 사람이 한 장씩 보고 규칙을 깁는 게 아니라, **실제 코퍼스 통계가 규칙을 만든다.**
  - mine(): 릴리스 그래프를 훑어 방종류쌍 인접 빈도를 집계 → 현실에서 거의 없는 쌍은
    forbidden_adj, 거의 항상 함께 붙는 것은 required_adj 룰로 자동 생성.
  - 출력 레지스트리(JSON)는 gen_loop의 제너릭 엔진이 읽어 전량 검사·교정한다(별도 모듈).
  - 데이터가 바뀌면 mine() 재실행 = 룰 자동 갱신. 법/건축규칙은 손으로/법령DB로 같은 레지스트리에 합류.

레지스트리 스키마(룰 1개 = 데이터 한 줄):
  {id, source: mined|arch|legal, type, params, fix, severity, provenance}
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import config  # noqa: E402

REL = config.DATA_DIR / "releases"
OUT_DIR = config.DATA_DIR / "constraints"

# 외부/벽 등 방이 아닌 노드는 제외.
_NON_ROOM = {"exterior", "wall", "외부", "벽"}


def _load_graphs(release: str) -> list[dict]:
    d = config.release_dir(release) / "graphs"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))]


def _type_pairs(g: dict) -> tuple[set, set, Counter]:
    """그래프 1개 → (등장 방종류 집합, 직접인접 방종류쌍 집합, 동일종류 인접 카운트).

    반환:
      present : 이 도면에 등장한 방종류 집합
      adj     : 직접 엣지로 연결된 (정렬된) 방종류쌍 집합  ※ 서로 다른 종류
      self_adj: 같은 종류끼리 직접 연결된 횟수(침실-침실 등) — Counter[type]
    """
    nodes = g["layout"]["nodes"]
    id2type = {n["id"]: n.get("type") for n in nodes
               if n.get("type") and n["type"] not in _NON_ROOM}
    present = set(id2type.values())
    adj: set = set()
    self_adj: Counter = Counter()
    for e in g["layout"].get("edges", []):
        a, b = id2type.get(e.get("source")), id2type.get(e.get("target"))
        if a is None or b is None:
            continue
        if a == b:
            self_adj[a] += 1
        else:
            adj.add(tuple(sorted((a, b))))
    return present, adj, self_adj


def mine(release: str = "v0", forbid_below: float = 0.05, require_above: float = 0.90,
         min_support: int = 50) -> dict:
    """릴리스 코퍼스 → 인접쌍 통계 → 제약 레지스트리.

    핵심 통계(쌍 A,B): n_both = A·B 둘 다 등장한 도면 수,  n_adj = 그중 A-B가 직접 인접한 도면 수,
    rate = n_adj / n_both = '둘 다 있을 때 실제로 붙는 비율'.
      rate < forbid_below  & 충분한 support → forbidden_adj(현실에서 거의 안 붙음)
      rate > require_above & 충분한 support → required_adj(현실에서 거의 항상 붙음)
    동일종류쌍(침실-침실)도 같은 방식: n_both = 그 종류 2개 이상인 도면 수.
    """
    graphs = _load_graphs(release)
    n = len(graphs)
    present_ct: Counter = Counter()          # 종류별 등장 도면 수
    both_ct: Counter = Counter()             # 쌍별 둘 다 등장 도면 수
    adj_ct: Counter = Counter()              # 쌍별 직접 인접 도면 수
    multi_ct: Counter = Counter()            # 종류별 '2개 이상' 등장 도면 수(동일쌍 분모)
    self_adj_ct: Counter = Counter()         # 종류별 동일끼리 인접한 도면 수

    for g in graphs:
        present, adj, self_adj = _type_pairs(g)
        for t in present:
            present_ct[t] += 1
        # 동일종류 2개 이상?
        type_mult = Counter(nd.get("type") for nd in g["layout"]["nodes"]
                            if nd.get("type") and nd["type"] not in _NON_ROOM)
        for t, c in type_mult.items():
            if c >= 2:
                multi_ct[t] += 1
        for t in self_adj:
            self_adj_ct[t] += 1
        for pair in combinations(sorted(present), 2):
            both_ct[pair] += 1
        for pair in adj:
            adj_ct[pair] += 1

    # ── 쌍 통계표(서로 다른 종류) ──
    stats = []
    for pair, nb in both_ct.items():
        na = adj_ct.get(pair, 0)
        stats.append({"pair": list(pair), "n_both": nb, "n_adj": na,
                      "rate": round(na / nb, 4) if nb else 0.0, "same": False})
    # ── 동일종류쌍(침실-침실 등) ──
    for t, nb in multi_ct.items():
        na = self_adj_ct.get(t, 0)
        stats.append({"pair": [t, t], "n_both": nb, "n_adj": na,
                      "rate": round(na / nb, 4) if nb else 0.0, "same": True})
    stats.sort(key=lambda s: s["rate"])

    # ── 룰 자동 생성 ──
    rules = []
    for s in stats:
        if s["n_both"] < min_support:
            continue
        a, b = s["pair"]
        if s["rate"] < forbid_below:
            rules.append({
                "id": f"forbid_{a}_{b}", "source": "mined", "type": "forbidden_adj",
                "params": {"pair": [a, b]}, "fix": "hub_reroute", "severity": "high",
                "provenance": (f"{release}: 둘다등장 {s['n_both']}도면 중 직접인접 "
                               f"{s['n_adj']}({s['rate']*100:.1f}%) — 현실에서 거의 없음")})
        elif s["rate"] > require_above:
            rules.append({
                "id": f"require_{a}_{b}", "source": "mined", "type": "required_adj",
                "params": {"pair": [a, b]}, "fix": "hub_connect", "severity": "med",
                "provenance": (f"{release}: 둘다등장 {s['n_both']}도면 중 직접인접 "
                               f"{s['n_adj']}({s['rate']*100:.1f}%) — 현실에서 거의 항상")})

    return {
        "meta": {"source_release": release, "n_plans": n,
                 "forbid_below": forbid_below, "require_above": require_above,
                 "min_support": min_support},
        "rules": rules,
        "pair_stats": stats,
    }


def write(reg: dict, path: Path = None) -> Path:
    path = path or (OUT_DIR / f"mined_{reg['meta']['source_release']}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", default="v0")
    ap.add_argument("--forbid-below", type=float, default=0.05)
    ap.add_argument("--require-above", type=float, default=0.90)
    ap.add_argument("--min-support", type=int, default=50)
    a = ap.parse_args()
    reg = mine(a.release, a.forbid_below, a.require_above, a.min_support)
    out = write(reg)
    print(f"코퍼스 {reg['meta']['n_plans']:,}도면 → 룰 {len(reg['rules'])}개  ({out})")
    print("\n── 가장 안 붙는 쌍 12 (forbidden 후보) ──")
    for s in reg["pair_stats"][:12]:
        tag = "동일" if s["same"] else "    "
        print(f"  [{tag}] {s['pair'][0]:>6}-{s['pair'][1]:<6} "
              f"rate={s['rate']*100:5.1f}%  (인접 {s['n_adj']:>4}/둘다 {s['n_both']:>4})")
    print("\n── 자동 생성된 forbidden_adj 룰 ──")
    for r in reg["rules"]:
        if r["type"] == "forbidden_adj":
            print(f"  {r['id']:24} — {r['provenance']}")
