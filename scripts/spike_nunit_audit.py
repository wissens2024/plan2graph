"""Spike A — N세대 연결 감사: 코어 포함 시 멀티유닛 시트가 한 floor 그래프로 합쳐지나?

config.CORE_CLASSES=() 로 코어(엘베/계단/홀)를 노드에 포함시켜 시트 전체그래프를 빌드하고,
연결성분 수를 센다. 1성분=유닛들이 코어로 연결(코드만으로 N세대 위상 복원 가능),
다성분=문/복도 갭(복도형은 '복도' 클래스가 없음). 멀티유닛 시트 표본으로 비율 측정.
"""
from __future__ import annotations
import sys, json, collections
from pathlib import Path
import networkx as nx

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)
import config  # noqa
CORE = set(config.CORE_CLASSES)          # 원래 코어 집합(측정용)
config.CORE_CLASSES = ()                 # monkeypatch: 코어를 노드로 포함

from plan2graph.build_dataset import build_label_index, _read_entry  # noqa: E402
from plan2graph.coco import load_coco_bytes  # noqa: E402
from plan2graph.geometry import assemble_drawing  # noqa: E402
from plan2graph.topology import build_graph, EXTERIOR  # noqa: E402

SPLIT = "Training"
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 80


def main():
    pairs = json.loads((config.INTERIM_DIR / f"linked_spa_str_{SPLIT.lower()}.json")
                       .read_text(encoding="utf-8"))["pairs"]
    li = build_label_index(SPLIT)
    # 멀티유닛 지문(원래 ≥2세대 생성) — manifest
    multi = {}
    for line in (config.DATA_DIR / "staging" / "aihub" / "manifest.jsonl").open(encoding="utf-8"):
        d = json.loads(line)
        if d.get("became_graph") and len(d.get("graph_ids") or []) >= 2:
            multi[d["fingerprint"]] = len(d["graph_ids"])

    comp = collections.Counter()
    core_present = unified = 0
    n = 0
    rows = []
    for fp, nunits in multi.items():
        if fp not in pairs:
            continue
        entries = {}
        for label, key in pairs[fp].items():
            ie = li.get((label, key))
            if ie:
                entries[label] = ie
        if "SPA" not in entries or "STR" not in entries:
            continue
        try:
            docs = [load_coco_bytes(_read_entry(*entries[l]), source=entries[l][1])
                    for l in ("SPA", "STR")]
            dr = assemble_drawing(docs)
            G = build_graph(dr, graph_id=fp)
        except Exception as e:  # noqa
            continue
        G2 = G.copy()
        if G2.has_node(EXTERIOR):
            G2.remove_node(EXTERIOR)
        if G2.number_of_nodes() == 0:
            continue
        ncomp = nx.number_connected_components(G2)
        has_core = any(G.nodes[x].get("type") in CORE for x in G2)
        comp[1 if ncomp == 1 else ("2" if ncomp == 2 else "3+")] += 1
        core_present += int(has_core)
        unified += int(ncomp == 1)
        n += 1
        if len(rows) < 8:
            rows.append((fp, nunits, ncomp, has_core))
        if n >= LIMIT:
            break

    print(f"멀티유닛 표본 {n}개 (원래 ≥2세대 시트, 코어 포함 빌드)")
    print(f"  코어 노드 존재: {core_present}/{n} ({100*core_present//max(n,1)}%)")
    print(f"  연결성분 분포: {dict(comp)}")
    print(f"  >>> 한 floor 그래프로 통합(1성분): {unified}/{n} "
          f"({100*unified//max(n,1)}%) = 코드만으로 N세대 위상 복원 가능 비율")
    print(f"  >>> 분절(2+성분): {n-unified}/{n} = 문/복도(복도형) 갭")
    print("  샘플:", [(r[0][:13], f"{r[1]}세대", f"{r[2]}성분", "코어O" if r[3] else "코어X") for r in rows])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
