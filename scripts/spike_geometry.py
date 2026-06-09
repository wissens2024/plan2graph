"""Spike B — 단일세대 기하 배치: (방+면적+인접) → 방 영역 → 렌더. program→도면 end-to-end 증명.

규칙기반(학습 불요): 그래프 spring 레이아웃으로 상대위치 시드 → 면적비례 격자 영역성장(region growing).
실제 v0 그래프의 위상·면적만 쓰고 좌표는 버린 뒤 재배치 → 실제 인접을 얼마나 보존하나 측정.
산출: artifacts/spike_geom_<gid>.png + 인접보존/면적충실 지표. (스파이크 — 품질 아닌 타당성 증명)
"""
from __future__ import annotations
import sys, json, collections
from pathlib import Path
import numpy as np
import networkx as nx
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)
import config  # noqa

# 한글 폰트(라벨)
for f in (ROOT / "fonts" / "NanumGothic.ttf",):
    if f.exists():
        import matplotlib.font_manager as fm
        fm.fontManager.addfont(str(f))
        matplotlib.rc("font", family=fm.FontProperties(fname=str(f)).get_name())
        break


def load_graph(rec):
    G = nx.Graph()
    for n in rec["layout"]["nodes"]:
        if isinstance(n["id"], int):
            G.add_node(n["id"], type=n["type"], area=float(n.get("area_px2") or 1.0))
    for e in rec["layout"]["edges"]:
        s, t = e["source"], e["target"]
        if isinstance(s, int) and isinstance(t, int) and e["via"] in ("door", "open", "balcony"):
            if G.has_node(s) and G.has_node(t):
                G.add_edge(s, t)
    return G


def place(G, grid=40, ar=1.25):
    areas = {n: G.nodes[n]["area"] for n in G}
    tot = sum(areas.values()) or 1.0
    H, W = grid, int(grid * ar)
    ncells = W * H
    target = {n: max(1, round(ncells * areas[n] / tot)) for n in G}
    pos = nx.spring_layout(G, seed=7, k=1.6, iterations=250)
    xs = [p[0] for p in pos.values()]; ys = [p[1] for p in pos.values()]
    rx, ry = (max(xs) - min(xs) + 1e-9), (max(ys) - min(ys) + 1e-9)
    owner = -np.ones((W, H), dtype=int)
    claimed = {n: 0 for n in G}
    frontier = {n: collections.deque() for n in G}
    for n in G:                                   # 시드(충돌 시 가까운 빈칸으로)
        x = int((pos[n][0] - min(xs)) / rx * (W - 1))
        y = int((pos[n][1] - min(ys)) / ry * (H - 1))
        if owner[x, y] != -1:
            best = None
            for r in range(1, max(W, H)):
                for dx in range(-r, r + 1):
                    for dy in range(-r, r + 1):
                        xx, yy = x + dx, y + dy
                        if 0 <= xx < W and 0 <= yy < H and owner[xx, yy] == -1:
                            best = (xx, yy); break
                    if best: break
                if best: break
            if best: x, y = best
        owner[x, y] = n; claimed[n] += 1; frontier[n].append((x, y))
    active = set(G)                                # 면적목표까지 동시 영역성장
    while active:
        for n in list(active):
            if claimed[n] >= target[n] or not frontier[n]:
                if not frontier[n]: active.discard(n)
                continue
            x, y = frontier[n].popleft()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                xx, yy = x + dx, y + dy
                if 0 <= xx < W and 0 <= yy < H and owner[xx, yy] == -1:
                    owner[xx, yy] = n; claimed[n] += 1; frontier[n].append((xx, yy))
                    if claimed[n] >= target[n]: break
            if claimed[n] >= target[n]: active.discard(n)
    free = [(x, y) for x in range(W) for y in range(H) if owner[x, y] == -1]
    while free:                                   # 잔여 빈칸 인접 소유자로 채움
        nf = []
        for (x, y) in free:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                xx, yy = x + dx, y + dy
                if 0 <= xx < W and 0 <= yy < H and owner[xx, yy] != -1:
                    owner[x, y] = owner[xx, yy]; break
            if owner[x, y] == -1: nf.append((x, y))
        if len(nf) == len(free): break
        free = nf
    return owner, W, H, target, claimed


def metrics(G, owner, W, H):
    touch = set()
    for x in range(W):
        for y in range(H):
            o = owner[x, y]
            if o < 0: continue
            for dx, dy in ((1, 0), (0, 1)):
                xx, yy = x + dx, y + dy
                if 0 <= xx < W and 0 <= yy < H and owner[xx, yy] >= 0 and owner[xx, yy] != o:
                    touch.add(tuple(sorted((o, owner[xx, yy]))))
    edges = set(tuple(sorted(e)) for e in G.edges())
    return len(edges & touch), len(edges)


def render(G, owner, W, H, out, title):
    uniq = sorted({G.nodes[n]["type"] for n in G})
    cidx = {t: i for i, t in enumerate(uniq)}
    cmap = plt.cm.tab20
    img = np.ones((H, W, 3))
    for x in range(W):
        for y in range(H):
            if owner[x, y] >= 0:
                img[y, x] = cmap(cidx[G.nodes[owner[x, y]]["type"]] % 20)[:3]
    fig, ax = plt.subplots(figsize=(7, 5.5))
    ax.imshow(img, origin="lower", interpolation="nearest")
    for n in G:                                   # 방 라벨 = 셀 무게중심
        cells = np.argwhere(owner == n)
        if len(cells):
            cx, cy = cells[:, 0].mean(), cells[:, 1].mean()
            ax.text(cx, cy, G.nodes[n]["type"].replace("공간_", ""),
                    ha="center", va="center", fontsize=8, color="black")
    ax.set_title(title, fontsize=10); ax.axis("off")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=110, bbox_inches="tight"); plt.close(fig)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    gdir = config.release_dir("v0") / "graphs"
    files = sorted(gdir.glob("*.json"))
    # 방 5~8개 단일세대 몇 장 샘플(결정적)
    picks = []
    for f in files:
        rec = json.loads(f.read_text(encoding="utf-8"))
        nr = sum(1 for n in rec["layout"]["nodes"] if isinstance(n["id"], int))
        if 5 <= nr <= 8:
            picks.append((f, rec))
        if len(picks) >= 5:
            break
    tot_p = tot_e = 0
    for f, rec in picks:
        G = load_graph(rec)
        if G.number_of_nodes() < 2: continue
        owner, W, H, target, claimed = place(G)
        p, e = metrics(G, owner, W, H)
        tot_p += p; tot_e += e
        area_err = np.mean([abs(claimed[n] - target[n]) / target[n] for n in G])
        out = ROOT / "artifacts" / f"spike_geom_{f.stem}.png"
        render(G, owner, W, H, out, f"{f.stem}  방{G.number_of_nodes()}  인접보존 {p}/{e}")
        print(f"{f.stem}: 방{G.number_of_nodes()} 인접 {p}/{e} 보존 "
              f"면적오차 {area_err*100:.0f}% → {out.name}")
    print(f"\n총 인접보존: {tot_p}/{tot_e} ({100*tot_p//max(tot_e,1)}%)  "
          f"— program/위상 → 기하배치 → 렌더 end-to-end {'성공' if tot_e else '데이터없음'}")
