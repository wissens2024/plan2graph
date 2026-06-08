"""기하 생성기 추론·렌더 — 학습된 train_geom 모델로 방 박스 배치 → 도면 이미지.

load(run_id) → 모델 / generate(net, rooms) → 박스 / render(rooms, boxes) → PNG.
도면생성 메뉴에서 호출(NL→program→rooms→박스→렌더). 이쁜 도면의 첫 실물.
"""
from __future__ import annotations

from pathlib import Path

from .train_geom import MAXR, PAD, ROLE_IX, RUNS, _model


def available_runs() -> list[str]:
    if not RUNS.exists():
        return []
    return sorted(p.name for p in RUNS.iterdir()
                  if p.name.startswith("geom_") and (p / "checkpoint.pt").exists())


def load(run_id: str):
    import torch
    net = _model()
    ckpt = torch.load(RUNS / run_id / "checkpoint.pt", map_location="cpu")
    net.load_state_dict(ckpt["state"])
    net.eval()
    return net


def generate(net, rooms: list[tuple]):
    """rooms: [(role, area_frac 0..1, n_windows)] → 방별 박스 [cx,cy,w,h] (0..1)."""
    import torch
    R = min(len(rooms), MAXR)
    role = torch.full((1, MAXR), PAD, dtype=torch.long)
    scal = torch.zeros(1, MAXR, 2)
    mask = torch.zeros(1, MAXR, dtype=torch.bool)
    for i in range(R):
        rl, ar, nw = rooms[i]
        role[0, i] = ROLE_IX.get(rl, PAD)
        scal[0, i, 0] = float(ar)
        scal[0, i, 1] = float(nw)
        mask[0, i] = True
    with torch.no_grad():
        box = net(role, scal, mask)[0, :R].tolist()
    return box


def render(rooms: list[tuple], boxes: list, size: int = 900) -> bytes:
    """방+박스 → 도면 PNG bytes(역할색 박스+라벨)."""
    import io
    from PIL import Image, ImageDraw
    from .topoedit import ROLE_COLOR, _hex_rgba, _pil_font
    im = Image.new("RGB", (size, size), "white")
    d = ImageDraw.Draw(im, "RGBA")
    M = 30
    for (rl, *_), (cx, cy, w, h) in zip(rooms, boxes):
        x0 = M + (cx - w / 2) * (size - 2 * M)
        y0 = M + (cy - h / 2) * (size - 2 * M)
        x1 = M + (cx + w / 2) * (size - 2 * M)
        y1 = M + (cy + h / 2) * (size - 2 * M)
        col = ROLE_COLOR.get(rl, "#cccccc")
        d.rectangle([x0, y0, x1, y1], fill=_hex_rgba(col, 110),
                    outline=(40, 40, 40, 255), width=3)
        try:
            d.text(((x0 + x1) / 2, (y0 + y1) / 2), rl, fill=(0, 0, 0, 255),
                   font=_pil_font(), anchor="mm")
        except Exception:  # noqa: BLE001
            pass
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


if __name__ == "__main__":   # 서버 테스트: 학습된 g0 모델로 한 장 생성·저장
    import sys
    rid = sys.argv[1] if len(sys.argv) > 1 else "geom_g0"
    # 신혼부부: 거실·주방·현관·침실2·욕실 (+발코니)
    rooms = [("거실", 1.0, 1), ("주방", 0.7, 1), ("현관", 0.2, 0),
             ("안방", 0.6, 1), ("침실", 0.5, 1), ("욕실", 0.2, 0), ("발코니", 0.3, 0)]
    net = load(rid)
    boxes = generate(net, rooms)
    png = render(rooms, boxes)
    out = Path("artifacts") / f"{rid}_sample.png"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(png)
    for (rl, *_), b in zip(rooms, boxes):
        print(f"  {rl:<6} box=[{b[0]:.2f},{b[1]:.2f},{b[2]:.2f},{b[3]:.2f}]")
    print("saved", out, len(png), "B")
