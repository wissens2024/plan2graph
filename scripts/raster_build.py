"""semantic raster 렌더러 (소실 복구, 2026-06-24) — g-0.4 그래프 → 역할-색 raster.

방 폴리곤을 role 색으로 채움(외곽선 없음 = 순수 의미 세그먼트), 큰 방 먼저(작은 방이 위).
FID용: 실제·생성을 *동일* 렌더로 그려 분포 비교. 색은 결정론(role→고정팔레트)이라 내부 일관.
raster_build_mc.py가 role_color·_area 재사용. render(g,size)는 fid_rplan이 사용.
⚠️ 원본 raster_build.py(미커밋 소실)의 재구성 — 팔레트는 원본과 다를 수 있으나 gen/real 동일 적용이라 FID 내부 일관성은 유지.
"""
from __future__ import annotations

import colorsys
import sys

sys.path.insert(0, "src")
from PIL import Image, ImageDraw  # noqa: E402

from plan2graph.wallcycle_codec import ROLES  # noqa: E402


def _palette(n):
    """결정론 팔레트 — 황금비 hue 간격으로 n개 구분색."""
    cols = []
    for i in range(n):
        h = (i * 0.6180339887) % 1.0
        r, g, b = colorsys.hsv_to_rgb(h, 0.62, 0.92)
        cols.append((int(r * 255), int(g * 255), int(b * 255)))
    return cols


_PAL = _palette(len(ROLES))
_ROLE2I = {r: i for i, r in enumerate(ROLES)}


def role_color(room):
    """방 dict({role|base}) → RGB. 미상 role = '기타' 색."""
    role = (room.get("role") or room.get("base") or "").strip()
    idx = _ROLE2I.get(role, _ROLE2I.get("기타", len(ROLES) - 1))
    return _PAL[idx]


def _area(poly):
    """폴리곤 면적(shoelace). 큰 방 먼저 그리기 정렬용."""
    n = len(poly)
    a = 0.0
    for i in range(n):
        x1, y1 = poly[i][0], poly[i][1]
        x2, y2 = poly[(i + 1) % n][0], poly[(i + 1) % n][1]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def render(g, size=256, margin=4):
    """g-0.4 그래프 → role-색 semantic raster (PIL RGB, 흰=외부)."""
    rooms = [r for r in (g.get("rooms") or {}).values()
             if r.get("polygon") and len(r["polygon"]) >= 3]
    img = Image.new("RGB", (size, size), (255, 255, 255))
    if not rooms:
        return img
    xs = [p[0] for r in rooms for p in r["polygon"]]
    ys = [p[1] for r in rooms for p in r["polygon"]]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    w = max(maxx - minx, 1e-6)
    h = max(maxy - miny, 1e-6)
    s = (size - 2 * margin) / max(w, h)               # 비율 유지 스케일
    drw = ImageDraw.Draw(img)
    for r in sorted(rooms, key=lambda rm: -_area(rm["polygon"])):   # 큰 방 먼저
        pts = [(margin + (x - minx) * s, margin + (y - miny) * s) for x, y in r["polygon"]]
        drw.polygon(pts, fill=role_color(r))
    return img
