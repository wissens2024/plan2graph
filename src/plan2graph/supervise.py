"""감독기(supervisor) — 생성 도면을 **cadrender 공용 Geometry + verify(단일 자)**로 채점.

자 하나(nail 1): 인접실현·고립·종횡비(A3) 검사가 cadrender.verify 한 함수에 있고,
T·G 모두 좌표는 **생성형 기하 AI(geom_gen, geom_g0)** → from_floorgeom으로 같은 자로 측정된다.
raw(autocorrect 전=기하엔진 정직점수)와 post(autocorrect 후=최종 제품) 둘 다 찍는다(raw 병기).

raw_pass = 생성 직후 도면이 통과인가(병목 직격), post_pass = 자기교정 후 통과인가.
mean_adj_realization = 위상엣지가 실제 벽맞댐으로 실현된 비율(생성 AI가 올려야 할 그 숫자).

T·G 차이는 **위상 출처만**: T=neural 위상모델 그래프, G=program+관례 인접. 좌표 생성기는 동일(geom_gen).
geom_g0 체크포인트 필요(treemap 베이스라인 폐기). 학습-실측 G 비교는 from_geomgraph 별도.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph import model_baseline as mb, gen_loop, cadrender as cr  # noqa: E402


def _adj_realization(geom) -> tuple[int, int]:
    """위상엣지(문의 방쌍) 중 실제 벽맞댐으로 실현된 수 / 전체 — verify와 같은 자(rooms_adjacent)."""
    by = {r.id: r for r in geom.rooms}
    tot = real = 0
    for d in geom.doors:
        if not d.rooms:
            continue
        tot += 1
        if cr.rooms_adjacent(by.get(d.rooms[0]), by.get(d.rooms[1])):
            real += 1
    return real, tot


def _cat(issue: str) -> str:
    return issue.split()[0] if issue else "?"


def _tline_geom(prog, gen_fn, adj, seed, net, priors):
    from plan2graph import geom_correct as gc, geom_gen as gg
    G, _ = gen_loop.generate_compliant(gen_fn, prog, max_tries=5, repair=True,
                                       seed=seed, adj_score=adj)
    rooms, edges = gc.tline_graph_to_rooms(G, priors)   # T 위상 → rooms (treemap 아님)
    boxes = gg.generate(net, rooms)                      # 생성형 기하 AI
    return cr.from_floorgeom(rooms, boxes, edges)


def _gline_geom(prog, priors, net):
    from plan2graph import geom_correct as gc, rules_arch as ra, geom_gen as gg
    rooms = gc.program_to_rooms(prog, priors)
    edges = gc.convention_edges(rooms)
    rooms, edges, _ = ra.apply_arch_program(rooms, edges)   # T와 동일 arch 룰
    boxes = gg.generate(net, rooms)                          # 생성형 기하 AI(treemap 아님)
    return cr.from_floorgeom(rooms, boxes, edges)


def supervise(programs, line: str = "tline", seed: int = 0, run_id: str = "geom_g0") -> dict:
    """program 리스트 → 헤드라인 지표(raw·post·실현율·결함분류). line=tline|gline."""
    model = mb.fit(mb._load_split("v0", "train"))
    gen_fn, adj = gen_loop.baseline_gen_fn(model)
    from plan2graph import geom_correct as gc, geom_gen as gg
    priors = gc.role_area_priors("g0")
    net = gg.load(run_id)                       # 생성형 기하 AI(T·G 공통 좌표 생성기)
    n = len(programs)
    raw_pass = post_pass = 0
    rate_sum = 0.0
    raw_cat, post_cat = Counter(), Counter()
    for i, prog in enumerate(programs):
        geom = (_tline_geom(prog, gen_fn, adj, seed + i, net, priors) if line == "tline"
                else _gline_geom(prog, priors, net))
        raw = cr.verify(geom)
        real, tot = _adj_realization(geom)
        rate_sum += (real / tot) if tot else 1.0
        cr.autocorrect(geom)
        post = geom.issues
        raw_pass += int(not raw)
        post_pass += int(not post)
        for c in {_cat(x) for x in raw}:
            raw_cat[c] += 1
        for c in {_cat(x) for x in post}:
            post_cat[c] += 1
    return {
        "line": line, "n": n,
        "raw_pass_rate": round(raw_pass / n, 3),
        "post_pass_rate": round(post_pass / n, 3),
        "mean_adj_realization": round(rate_sum / n, 3),
        "raw_defect_inputs": dict(raw_cat),
        "post_defect_inputs": dict(post_cat),
    }


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import json
    ver = sys.argv[1] if len(sys.argv) > 1 else "v0"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    test = mb._load_split(ver, "test")[:limit]
    progs = [dict(Counter(n["type"] for n in r["layout"]["nodes"]
                          if isinstance(n["id"], int))) for r in test]
    CATKO = {"R6": "인접미실현", "R7": "방고립", "R3": "문off-wall",
             "R4": "기구방밖", "R2": "폴리곤결손", "R8": "치수불가", "A3": "긴현관"}
    rows = {}
    for line in ("tline", "gline"):
        h = supervise(progs, line=line)
        rows[line] = h
    print(f"=== 감독기: 공용 자(cadrender.verify)로 T∥G 채점 ({rows['tline']['n']}개) ===")
    print(f"{'지표':22}{'T-라인':>12}{'G-라인':>12}")
    print(f"{'raw 통과율(교정전)':22}{rows['tline']['raw_pass_rate']*100:>11.1f}%"
          f"{rows['gline']['raw_pass_rate']*100:>11.1f}%")
    print(f"{'post 통과율(교정후)':22}{rows['tline']['post_pass_rate']*100:>11.1f}%"
          f"{rows['gline']['post_pass_rate']*100:>11.1f}%")
    print(f"{'평균 인접실현율':22}{rows['tline']['mean_adj_realization']*100:>11.1f}%"
          f"{rows['gline']['mean_adj_realization']*100:>11.1f}%  ← 병목")
    print("결함 발생 입력 수 (raw):")
    cats = set(rows["tline"]["raw_defect_inputs"]) | set(rows["gline"]["raw_defect_inputs"])
    for c in sorted(cats):
        t = rows["tline"]["raw_defect_inputs"].get(c, 0)
        g = rows["gline"]["raw_defect_inputs"].get(c, 0)
        print(f"   {CATKO.get(c, c):14}{t:>9}{g:>12}")
    print("ℹ T·G 좌표 = 생성형 기하 AI(geom_g0). 차이는 위상 출처(T=neural / G=program). "
          "학습-실측 G 비교는 from_geomgraph 별도.")
    out = config.release_dir(ver) / "supervise_report.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {out}")
