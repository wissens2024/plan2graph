"""정확도 게이트 — 문-방 위상 추론을 사람이 검증해 precision/recall 측정.

도면 읽는 소양이면 채점 가능(건축사 불필요). 추론된 문/통로 엣지를 하나씩
✓(맞음)/✗(틀림)로 표시하고, 누락된 연결을 추가 → 정확도 산출.
이 엣지 편집은 v1 수동 보정과 동일 기능(겸용).

산출: gate/<gid>.png(검증 오버레이), gate/score_template.csv(채점표),
      gate.score(marks) → precision·recall.
"""
from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402

GATE_DIR = ROOT / "artifacts" / "gate"
CONNECT = ("door", "open", "balcony")


def select_drawings(n: int = 20, version: str = "v0") -> list[dict]:
    """깨끗한 단일세대(현관1, scale 우선) 그래프 N개 — 결정적 선택."""
    rel = config.release_dir(version) / "graphs"
    files = sorted(rel.glob("*.json"))
    picked = []
    for f in files:
        r = json.loads(f.read_text(encoding="utf-8"))
        if r["constraints"]["program"].get("현관", 0) != 1:
            continue
        if 7 <= r["meta"]["n_rooms"] <= 14:        # 검증 적당한 크기
            picked.append(r)
        if len(picked) >= n:
            break
    return picked


def edges_of(rec: dict) -> list[dict]:
    nt = {nd["id"]: nd["type"] for nd in rec["layout"]["nodes"]}
    out = []
    for i, e in enumerate(rec["layout"]["edges"]):
        if e["via"] in CONNECT and isinstance(e["source"], int) \
                and isinstance(e["target"], int):
            out.append({"eid": i, "a": nt.get(e["source"]), "b": nt.get(e["target"]),
                        "via": e["via"], "src": e["source"], "tgt": e["target"]})
    return out


def render_gate(rec: dict, sheet, out_path: Path) -> None:
    """검증 오버레이: 도면(회색) + 방 옅은채움·라벨 + 추론 엣지 굵은선(번호)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon as MplPoly
    from PIL import Image
    from plan2graph.review import KFONT, _VIA_COLOR

    dr = sheet.dr
    focus = {nd["id"] for nd in rec["layout"]["nodes"] if isinstance(nd["id"], int)}
    fig, ax = plt.subplots(figsize=(12, 12), dpi=130)
    fig.patch.set_facecolor("white")
    bxs, bys = [], []
    for i in focus:
        if i < len(dr.rooms) and dr.rooms[i].polygon is not None:
            x0, y0, x1, y1 = dr.rooms[i].polygon.bounds
            bxs += [x0, x1]; bys += [y0, y1]
    region = (min(bxs) - 90, min(bys) - 90, max(bxs) + 90, max(bys) + 90) if bxs else None
    if sheet.png_bytes and region:
        img = Image.open(io.BytesIO(sheet.png_bytes)).convert("L")
        ow, oh = img.size
        rx0, ry0, rx1, ry1 = (max(0, int(region[0])), max(0, int(region[1])),
                              min(ow, int(region[2])), min(oh, int(region[3])))
        ax.imshow(img.crop((rx0, ry0, rx1, ry1)), cmap="gray", vmin=0, vmax=255,
                  extent=(rx0, rx1, ry1, ry0))
    cen = {}
    for i in focus:
        r = dr.rooms[i] if i < len(dr.rooms) else None
        if r is None or r.polygon is None:
            continue
        xs, ys = r.polygon.exterior.xy
        ax.add_patch(MplPoly(list(zip(xs, ys)), closed=True, facecolor="#4C9BE8",
                             edgecolor="#1B4F8A", alpha=0.10, lw=0.8))  # 옅게(도면 보이게)
        if r.centroid:
            cen[i] = r.centroid
            ax.text(r.centroid[0], r.centroid[1], r.class_name.replace("공간_", ""),
                    fontsize=8, ha="center", va="center", fontfamily=KFONT,
                    bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="#1B4F8A", alpha=0.85))
    for ed in edges_of(rec):
        u, v = ed["src"], ed["tgt"]
        if u in cen and v in cen:
            c = _VIA_COLOR.get(ed["via"], "#333")
            mx, my = (cen[u][0] + cen[v][0]) / 2, (cen[u][1] + cen[v][1]) / 2
            ax.plot([cen[u][0], cen[v][0]], [cen[u][1], cen[v][1]], color=c, lw=2.4, alpha=0.9)
            ax.text(mx, my, str(ed["eid"]), fontsize=7, color="white", ha="center",
                    va="center", bbox=dict(boxstyle="circle,pad=0.1", fc=c, ec="none"))
    ax.set_title(f"{rec['graph_id']}  방{rec['meta']['n_rooms']}  엣지{len(edges_of(rec))} "
                 f"(🔴문 🟢발코니 🟠개방)", fontsize=10, fontfamily=KFONT)
    ax.axis("off")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)


def build_gate(n: int = 20, version: str = "v0") -> Path:
    """게이트 도면 N개 추출·렌더 + 채점 템플릿 생성."""
    from plan2graph import review
    idx = review.build_indices(("Training", "Validation"))
    recs = select_drawings(n, version)
    GATE_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for rec in recs:
        sid = rec["graph_id"].rsplit("_u", 1)[0]
        sh = review.load_sheet(sid, idx)
        if sh is None:
            continue
        render_gate(rec, sh, GATE_DIR / f"{rec['graph_id']}.png")
        for ed in edges_of(rec):
            rows.append({"graph_id": rec["graph_id"], "eid": ed["eid"],
                         "a": ed["a"], "b": ed["b"], "via": ed["via"],
                         "verdict": ""})  # ✓/✗ 채움
    tpl = GATE_DIR / "score_template.csv"
    with open(tpl, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["graph_id", "eid", "a", "b", "via", "verdict"])
        w.writeheader()
        w.writerows(rows)
    print(f"게이트 {len(recs)}장 · 추론엣지 {len(rows)}개 → {GATE_DIR}")
    print(f"채점표: {tpl} (verdict에 o/x 입력, 누락엣지는 행 추가)")
    return tpl


def score(marks_csv: Path = None, missing_csv: Path = None) -> dict:
    """채점표(verdict o/x) → precision. 누락엣지 파일 있으면 recall."""
    p = marks_csv or (GATE_DIR / "score_template.csv")
    rows = list(csv.DictReader(open(p, encoding="utf-8")))
    judged = [r for r in rows if r["verdict"].strip().lower() in ("o", "x", "ok", "wrong")]
    correct = sum(1 for r in judged if r["verdict"].strip().lower() in ("o", "ok"))
    n_infer = len(judged)
    n_missing = 0
    if missing_csv and Path(missing_csv).exists():
        n_missing = sum(1 for _ in csv.DictReader(open(missing_csv, encoding="utf-8")))
    prec = correct / n_infer if n_infer else None
    rec = correct / (correct + n_missing) if (correct + n_missing) else None
    return {"n_inferred_judged": n_infer, "correct": correct,
            "precision": round(prec, 3) if prec is not None else None,
            "missing_added": n_missing,
            "recall": round(rec, 3) if rec is not None else None}


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "score"], default="build", nargs="?")
    ap.add_argument("--n", type=int, default=20)
    a = ap.parse_args()
    if a.cmd == "build":
        build_gate(a.n)
    else:
        print(json.dumps(score(), ensure_ascii=False, indent=2))
