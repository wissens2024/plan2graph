"""End-to-end 배치 테스트 — 다양한 자연어 N개로 도면 생성하고 결함을 자동 채점.

채점 카테고리:
  P 프로그램불일치   : 요구한 방 구성 ≠ 생성된 방 구성
  V verify위반       : R1~R4(고립·문없음·현관도달·현관존재) — 최종(loop on) 기준
  Fb 위상-금지인접   : 채굴 forbidden_adj 쌍이 위상에 엣지로 존재
  Rq 위상-필수누락   : 채굴 required_adj 쌍인데 둘 다 있으면서 위상에 엣지 없음
  Gu 배치-실현실패   : 위상엣지인데 배치에서 벽 안 맞댐(문 안 그려짐) — 비율
  Gi 배치-방고립     : 실내 문(실현)이 0개인 방(=문 없는 방처럼 보임)
  Nc 자기교정무효    : 결함 있는데 off==on (자기교정이 아무것도 안 함)

사용: python scripts/batch_test_gen.py <checkpoint.pt> [seed]
"""
import sys
from collections import Counter

from plan2graph import text2graph, gen_loop, generators, floorgeom, constraints

EXT = floorgeom.EXTERIOR

INPUTS = [
    "신혼부부 아파트 침실2 욕실1 거실 주방",
    "1인 가구 원룸 침실1 욕실1 주방",
    "4인 가족 84㎡ 침실3 욕실2 거실 주방 드레스룸",
    "노부부 아파트 침실2 욕실1 거실 주방 발코니",
    "30평 아파트 침실3 욕실2 거실 주방 안방 드레스룸 발코니",
    "오피스텔 침실1 욕실1 거실 주방",
    "대가족 침실4 욕실2 거실 주방 다목적실",
    "침실1 욕실1 거실 주방",
    "신혼 빌라 침실2 욕실1 LDK",
    "침실3 욕실2 거실 주방 발코니 실외기실",
    "25평 아파트 침실2 욕실1 거실 주방 드레스룸",
    "침실2 욕실2 거실 주방 발코니",
    "1.5룸 침실1 거실 주방 욕실1",
    "펜트하우스 침실4 욕실3 거실 주방 드레스룸 다목적실 발코니",
    "침실2 욕실1 주방",
    "침실5 욕실3 거실 주방",
    "33평 아파트 안방 침실2 욕실2 거실 주방 드레스룸 발코니",
    "투룸 침실2 욕실1 주방 거실",
    "침실1 욕실1",
    "84㎡ 아파트 침실3 욕실2 거실 주방 발코니 다목적실 드레스룸",
]


def _rtype(G, n):
    return G.nodes[n].get("type")


def _edge_typepairs(G):
    pairs = Counter()
    for u, v in G.edges:
        if u == EXT or v == EXT:
            continue
        a, b = _rtype(G, u), _rtype(G, v)
        if a and b:
            pairs[tuple(sorted((a, b)))] += 1
    return pairs


def analyze(G_on, G_off, reg, program):
    out = {}
    types = [_rtype(G, n) for G in [G_on] for n in G.nodes if n != EXT]
    gen_ct = Counter(t for t in types if t)
    present = set(gen_ct)
    epairs = _edge_typepairs(G_on)

    # P 프로그램 불일치
    pm = []
    for t, req in program.items():
        got = gen_ct.get(t, 0)
        if got != req:
            pm.append(f"{t} 요구{req}≠생성{got}")
    out["P"] = pm

    # V verify(on)
    v = gen_loop.verify(G_on)
    out["V"] = [f"[{x.get('kind')}] {x.get('msg') or x.get('rule')}" for x in v["violations"]]

    # Fb 금지 인접 존재 / Rq 필수 누락
    fb, rq = [], []
    for r in reg["rules"]:
        a, b = r["params"]["pair"]
        key = tuple(sorted((a, b)))
        if r["type"] == "forbidden_adj" and epairs.get(key):
            fb.append(f"{a}-{b}")
        if r["type"] == "required_adj" and a in present and b in present and not epairs.get(key):
            rq.append(f"{a}-{b}")
    out["Fb"], out["Rq"] = fb, rq

    # 배치
    rects = floorgeom.layout_rooms(G_on)
    st = floorgeom.layout_stats(G_on, rects)
    out["adj_rate"] = st["adj_rate"]
    out["adj"] = (st["adj_realized"], st["adj_total"])
    # Gu 실현 실패한 위상엣지
    gu = []
    for u, w in G_on.edges:
        if u == EXT or w == EXT:
            continue
        if u in rects and w in rects and not floorgeom._shared_edge(rects[u], rects[w]):
            gu.append(f"{_rtype(G_on,u)}-{_rtype(G_on,w)}")
    out["Gu"] = gu
    # Gi 배치상 고립(실현 문 0개)
    gi = []
    for n in G_on.nodes:
        if n == EXT:
            continue
        nbrs = [m for m in G_on.neighbors(n) if m != EXT]
        realized = sum(1 for m in nbrs
                       if n in rects and m in rects and floorgeom._shared_edge(rects[n], rects[m]))
        if nbrs and realized == 0:
            gi.append(_rtype(G_on, n))
    out["Gi"] = gi
    out["present"] = present
    out["epairs"] = epairs

    # Nc 자기교정 무효 (off==on 엣지셋 동일한데 결함 존재)
    same = set(map(frozenset, ((u, v) for u, v in G_off.edges))) == \
        set(map(frozenset, ((u, v) for u, v in G_on.edges)))
    has_defect = bool(out["V"] or out["Fb"] or out["Rq"] or out["Gi"])
    out["Nc"] = same and has_defect
    return out


def main():
    ckpt = sys.argv[1]
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    reg = constraints.mine("v0")  # 룰 채굴(현재 데이터)
    ng = generators.load(ckpt)
    lines = [f"checkpoint: {ckpt}", f"seed: {seed}", f"채굴 룰: {len(reg['rules'])}개", ""]
    agg = Counter()
    rate_sum = 0.0
    # 위상모델 룰별 채점 누적
    req_both, req_hit = Counter(), Counter()   # 필수쌍: 둘다등장 / 그중 엣지존재(=리콜)
    forb_both, forb_hit = Counter(), Counter()  # 금지쌍: 둘다등장 / 그중 엣지존재(=위반)
    self_seen = Counter()                        # 침실-침실 등 동일쌍 위상 발생 입력수
    for i, text in enumerate(INPUTS, 1):
        prog = text2graph.parse(text)["program"]

        def gfn(p, r):
            return ng.generate(p, r)
        G_off, _ = gen_loop.generate_compliant(gfn, prog, max_tries=1, repair=False, seed=seed)
        G_on, _ = gen_loop.generate_compliant(gfn, prog, max_tries=5, repair=True, seed=seed)
        a = analyze(G_on, G_off, reg, prog)
        rate_sum += a["adj_rate"]
        # 룰별 위상 채점
        for r in reg["rules"]:
            x, y = r["params"]["pair"]
            key = tuple(sorted((x, y)))
            both = x in a["present"] and y in a["present"]
            hit = bool(a["epairs"].get(key))
            if r["type"] == "required_adj" and both:
                req_both[key] += 1
                req_hit[key] += hit
            if r["type"] == "forbidden_adj" and both:
                forb_both[key] += 1
                forb_hit[key] += hit
        if a["epairs"].get(("침실", "침실")):
            self_seen["침실-침실"] += 1
        for k in ["P", "V", "Fb", "Rq", "Gu", "Gi"]:
            if a[k]:
                agg[k] += 1
        if a["Nc"]:
            agg["Nc"] += 1
        lines.append(f"[{i:02d}] {text}")
        lines.append(f"     program={prog}")
        lines.append(f"     실현율 {a['adj'][0]}/{a['adj'][1]}={a['adj_rate']*100:.0f}%"
                     f"  | P:{a['P'] or '-'}")
        lines.append(f"     V:{a['V'] or '-'}")
        lines.append(f"     Fb금지:{a['Fb'] or '-'}  Rq필수누락:{a['Rq'] or '-'}")
        lines.append(f"     Gu실현실패:{a['Gu'] or '-'}")
        lines.append(f"     Gi방고립:{a['Gi'] or '-'}  Nc자기교정무효:{a['Nc']}")
        lines.append("")
    n = len(INPUTS)
    lines.append("=" * 60)
    lines.append(f"종합({n}개 입력) — 결함 발생 입력 수:")
    cat = {"P": "프로그램불일치", "V": "verify위반", "Fb": "금지인접", "Rq": "필수누락",
           "Gu": "배치실현실패", "Gi": "방고립", "Nc": "자기교정무효"}
    for k, name in cat.items():
        lines.append(f"   {k} {name:10}: {agg[k]:2d}/{n}")
    lines.append(f"   평균 배치 실현율: {rate_sum/n*100:.0f}%")
    lines.append("")
    lines.append("── 위상모델 채점: 필수 인접 리콜(둘다 등장한 입력 중 모델이 실제 연결한 비율) ──")
    for key in sorted(req_both, key=lambda k: req_hit[k] / req_both[k]):
        b, h = req_both[key], req_hit[key]
        lines.append(f"   {key[0]}-{key[1]:<6}: {h}/{b} = {h/b*100:3.0f}%  (실데이터 필수)")
    lines.append("── 위상모델 채점: 금지 인접 위반(둘다 등장 중 모델이 잘못 연결) ──")
    for key in sorted(forb_both, key=lambda k: -forb_hit[k]):
        b, h = forb_both[key], forb_hit[key]
        if b:
            lines.append(f"   {key[0]}-{key[1]:<6}: {h}/{b} = {h/b*100:3.0f}%  (실데이터 금지)")
    lines.append(f"── 침실-침실 직접연결 발생: {self_seen['침실-침실']}/{n} 입력 ──")
    report = "\n".join(lines)
    out = constraints.OUT_DIR.parent / "batch_report.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"리포트 저장: {out}")
    print("\n".join(lines[-12:]))


if __name__ == "__main__":
    main()
