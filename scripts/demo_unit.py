"""단일세대 위상 도면 데모 — 자연어 → 제약 → 신경망 생성 → Symbolic 자기교정(근거) → 렌더.

Phase-3 단일세대 위상 파이프라인 엔드투엔드(§4-1). 추론만(GPU 불요).
- 안정 체크포인트(runs/<run_id>/checkpoint.pt) 사용 — models/ 포인터는 v3/v4 매트릭스가 덮어쓰는 중.
- type조건(아파트/단독 구분)은 후속(모델 재학습 필요) — 지금은 program(방 구성)만.

사용: python scripts/demo_unit.py "4인 가족 아파트 침실3 욕실2 LDK 안방 드레스룸"
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)
import config  # noqa
from plan2graph import text2graph, gen_loop  # noqa
from plan2graph.train_gen import NeuralGenerator  # noqa

import matplotlib  # noqa
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa
import matplotlib.font_manager as fm  # noqa
import networkx as nx  # noqa
_f = ROOT / "fonts" / "NanumGothic.ttf"
KFONT = "sans-serif"
if _f.exists():
    fm.fontManager.addfont(str(_f))
    _name = fm.FontProperties(fname=str(_f)).get_name()
    matplotlib.rcParams["font.family"] = "sans-serif"
    matplotlib.rcParams["font.sans-serif"] = [_name, "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False

# v3/v4가 안 건드리는 안정 체크포인트(균형 매트릭스 noPretrain seed42)
CKPT = ROOT / "runs" / "gen-v0-neural-set-transformer-v2-noPretrain-seed42" / "checkpoint.pt"


def render_topology(G, out, title):
    """생성 위상 그래프(좌표 없음) → spring 레이아웃 렌더. 노드=위계색, 엣지=via색."""
    pos = nx.spring_layout(G, seed=7, k=1.5, iterations=200)
    hcol = {"public": "#E8453C", "private": "#4C9BE8", "service": "#2DBE60"}
    ncol = [hcol.get(G.nodes[n].get("hierarchy"), "#bbbbbb") for n in G]
    vcol = {"door": "#333333", "open": "#E8A33D", "balcony": "#2DBE60", "entrance": "#888888"}
    ecol = [vcol.get(d.get("via"), "#999999") for _, _, d in G.edges(data=True)]
    lbl = {n: ("외부" if n == "exterior" else (G.nodes[n].get("type") or str(n))) for n in G}
    fig, ax = plt.subplots(figsize=(8, 7))
    nx.draw_networkx_edges(G, pos, ax=ax, edge_color=ecol, width=2.0)
    nx.draw_networkx_nodes(G, pos, ax=ax, node_color=ncol, node_size=950, alpha=0.92)
    nx.draw_networkx_labels(G, pos, lbl, ax=ax, font_size=8, font_family=KFONT)
    ax.set_title(title, fontsize=11)
    ax.axis("off")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)


def main(text: str):
    print("자연어 요구:", text)
    cg = text2graph.parse(text)
    program = cg["program"]
    print("제약 그래프(program):", program)

    ng = NeuralGenerator(str(CKPT))
    print("생성기:", ng.run_id)
    G, hist = gen_loop.generate_compliant(
        lambda prog, rng: ng.generate(prog, rng), program, max_tries=5, seed=1)

    print("\n=== Symbolic 자기교정 루프 (근거) ===")
    for h in hist:
        print(f"  시도{h['attempt']}: 위반 {h['violations_before']}→{h['violations_after']} "
              f"수정={h['fixes'] or '없음'} 통과={h['passed']}")
    v = gen_loop.verify(G)
    print(f"\n최종: 통과={v['passed']} (무결성={v['integrity_ok']} 법규={v['legal_ok']})")
    if v["violations"]:
        print("  잔여 위반:", [(x['kind'], x.get('rule') or x.get('reason')) for x in v["violations"]])

    n_space = sum(1 for n, d in G.nodes(data=True) if d.get("type") not in (None, "exterior"))
    n_edge = G.number_of_edges()
    print(f"\n위상: 방 {n_space} · 연결 {n_edge}")
    out = ROOT / "artifacts" / "demo_unit.png"
    render_topology(G, out, text)
    print("렌더(위상 도면):", out)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1] if len(sys.argv) > 1 else "4인 가족 아파트 침실3 욕실2 LDK 안방 드레스룸")
