"""dedup — 중복 도면(동일 도면) 서명·보정 비교·보정 전파.

배경: 같은 평면도가 데이터셋에 수십~수백 번 들어 있고, 알바가 그 사본들을 각각 따로
보정했다. 사본끼리 보정 결과가 다르면 어느 쪽이 맞는지 알 수 없어 자동 복사를 못 한다.

이 모듈이 제공하는 3가지:
  1) shape_sig(g)      — 평행이동·미러 무관 도형 서명. 같은 도면 = 같은 서명.
  2) same_correction() — 두 보정이 '같은 보정'인지 허용오차 비교(1px 흔들림에 안 속게).
  3) transform_graph() — 보정본을 형제 도면 좌표계로 옮겨 심기(전파). 정렬검증 동반.

좌표 정규화 규약
    변환 t = (fx, fy, ox, oy),  정규좌표  X = fx*(x-ox),  Y = fy*(y-oy)
    fx/fy ∈ {+1,-1} (미러), ox/oy = 평행이동 기준점.
    역변환은 fx∈{1,-1} 이므로 x = fx*X + ox (1/fx == fx).
"""
import hashlib
import math

__all__ = ["shape_sig", "norm_pt", "corr_cells", "same_correction",
           "transform_graph", "alignment_error"]


# ─────────────────────────────────────────────────────────────────────────────
# 1) 도형 서명
# ─────────────────────────────────────────────────────────────────────────────
def _room_polys(g):
    return [r["polygon"] for r in (g.get("rooms") or {}).values() if r.get("polygon")]


def shape_sig(g, q=1.0):
    """(서명, 변환t) — 방 폴리곤 집합을 평행이동·미러 정규화 후 q px 양자화해 해시.

    미러 4후보 중 사전순 최소를 정규형으로 택하므로, 같은 도면은 시트 어디에 놓였든·
    좌우 뒤집혔든 같은 서명이 나온다. 축척이 다르면 다른 서명(=다른 도면 취급).
    """
    polys = _room_polys(g)
    if not polys:
        return None, None
    xs = [x for p in polys for x, _y in p]
    ys = [y for p in polys for _x, y in p]
    mnx, mny, mxx, mxy = min(xs), min(ys), max(xs), max(ys)
    best = best_t = None
    for fx in (1, -1):
        for fy in (1, -1):
            ox = mnx if fx == 1 else mxx
            oy = mny if fy == 1 else mxy
            form = tuple(sorted(
                tuple(sorted((round(fx * (x - ox) / q), round(fy * (y - oy) / q))
                             for x, y in p))
                for p in polys))
            if best is None or form < best:
                best, best_t = form, (fx, fy, ox, oy)
    return hashlib.sha1(repr(best).encode()).hexdigest()[:16], best_t


def norm_pt(x, y, t, q=1.0):
    fx, fy, ox, oy = t
    return (round(fx * (x - ox) / q), round(fy * (y - oy) / q))


# ─────────────────────────────────────────────────────────────────────────────
# 2) 보정 비교
# ─────────────────────────────────────────────────────────────────────────────
def corr_cells(g, t):
    """보정본 → ([(정규x, 정규y, role)...], 역할카운트). 좌표는 **양자화 안 함**.

    격자로 반올림하면 경계에 걸친 1px 흔들림이 '다른 보정'으로 둔갑한다(실측 확인:
    같은 방이 (180,12) vs (181,13)로 갈렸음). 비교는 same_correction()의 허용오차로.
    """
    import collections
    items, roles = [], collections.Counter()
    fx, fy, ox, oy = t
    for r in (g.get("rooms") or {}).values():
        role = r.get("role") or r.get("base") or "?"
        c = r.get("centroid")
        if not c:
            p = r.get("polygon")
            if not p:
                continue
            c = [sum(x for x, _ in p) / len(p), sum(y for _, y in p) / len(p)]
        items.append((fx * (c[0] - ox), fy * (c[1] - oy), role))
        roles[role] += 1
    return items, roles


def same_correction(a, b, tol=10.0):
    """두 보정이 같은가 — 방 수가 같고, 각 방이 tol px 안에서 짝지어지며 역할도 같은가.

    tol 은 사본 간 좌표 정렬오차(≈1px)보다 넉넉하고 방 간격(≈50px+)보다는 훨씬 작게.
    위치가 같은데 역할이 다르면 → 진짜 불일치(사람이 봐야 함).
    """
    if len(a) != len(b):
        return False
    used = set()
    for x, y, role in a:
        best, bd = None, tol
        for j, (x2, y2, _r) in enumerate(b):
            if j in used:
                continue
            d = max(abs(x - x2), abs(y - y2))
            if d <= bd:
                best, bd = j, d
        if best is None or b[best][2] != role:
            return False
        used.add(best)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 3) 전파 — 보정본을 형제 도면 좌표계로
# ─────────────────────────────────────────────────────────────────────────────
def _mapper(t_src, t_tgt):
    """src 좌표 → (정규좌표) → tgt 좌표 점 사상. 순수 평행이동+미러(축척 변화 없음)."""
    fxs, fys, oxs, oys = t_src
    fxt, fyt, oxt, oyt = t_tgt

    def mp(x, y):
        return (fxt * (fxs * (x - oxs)) + oxt,
                fyt * (fys * (y - oys)) + oyt)
    return mp, fxs * fxt, fys * fyt          # (사상함수, x 순뒤집힘, y 순뒤집힘)


def alignment_error(src_parsed, tgt_parsed, t_src, t_tgt):
    """전파 안전성 검사: src 원본을 tgt 좌표계로 옮겼을 때 tgt 원본과 얼마나 어긋나는가(px).

    같은 서명이면 이론상 0에 가까워야 한다. 값이 크면 서명이 잘못 묶은 것 → 전파 중단.
    """
    mp, _sx, _sy = _mapper(t_src, t_tgt)
    a = [mp(*r["centroid"]) for r in (src_parsed.get("rooms") or {}).values()
         if r.get("centroid")]
    b = [tuple(r["centroid"]) for r in (tgt_parsed.get("rooms") or {}).values()
         if r.get("centroid")]
    if not a or not b or len(a) != len(b):
        return float("inf")
    worst, used = 0.0, set()
    for x, y in a:
        best, bd = None, float("inf")
        for j, (x2, y2) in enumerate(b):
            if j in used:
                continue
            d = max(abs(x - x2), abs(y - y2))
            if d < bd:
                best, bd = j, d
        if best is None:
            return float("inf")
        used.add(best)
        worst = max(worst, bd)
    return worst


def _bbox_map(b, mp):
    """[x,y,w,h] → 두 모서리를 사상 후 재구성(미러면 좌우가 뒤집히므로 min/max 재계산)."""
    if not b or len(b) < 4:
        return b
    x0, y0 = mp(b[0], b[1])
    x1, y1 = mp(b[0] + b[2], b[1] + b[3])
    return [round(min(x0, x1), 1), round(min(y0, y1), 1),
            round(abs(x1 - x0), 1), round(abs(y1 - y0), 1)]


def _angle_map(deg, sx, sy):
    """방향각(도)을 순뒤집힘에 맞춰 회전. 뒤집힘 없으면 그대로(평행이동은 각도 불변)."""
    if deg is None or (sx == 1 and sy == 1):
        return deg
    try:
        r = math.radians(float(deg))
    except (TypeError, ValueError):
        return deg
    return round(math.degrees(math.atan2(sy * math.sin(r), sx * math.cos(r))) % 180.0, 1)


def transform_graph(g_src_corrected, t_src, t_tgt, target_id):
    """보정본을 형제 도면 좌표계로 옮긴 새 그래프 반환. 원본 딕셔너리는 건드리지 않는다.

    사상 대상: 최상위 bbox_px · rooms(centroid/bbox/polygon/centroid_norm) ·
              walls.segment · doors(position/polygon/bbox) · windows(position/bbox) ·
              fixtures(position/polygon/bbox) · 방향각(미러일 때만).
    plan_id/unit_id/meta.source_sheet_id 는 대상 도면 것으로 교체한다.
    """
    import copy
    import re
    g = copy.deepcopy(g_src_corrected)
    mp, sx, sy = _mapper(t_src, t_tgt)

    def pts(seq):
        return [[round(v, 1) for v in mp(p[0], p[1])] for p in seq]

    g["bbox_px"] = _bbox_map(g.get("bbox_px"), mp)
    for r in (g.get("rooms") or {}).values():
        if r.get("centroid"):
            r["centroid"] = [round(v, 1) for v in mp(*r["centroid"])]
        if r.get("polygon"):
            r["polygon"] = pts(r["polygon"])
        if r.get("bbox_px"):
            r["bbox_px"] = _bbox_map(r["bbox_px"], mp)
        cn = r.get("centroid_norm")
        if cn and len(cn) == 2:      # 0..1 정규값 — 뒤집힌 축만 1-v
            r["centroid_norm"] = [round(1 - cn[0], 4) if sx == -1 else cn[0],
                                  round(1 - cn[1], 4) if sy == -1 else cn[1]]
    for w in (g.get("walls") or []):
        if w.get("segment"):
            w["segment"] = pts(w["segment"])
    for d in (g.get("doors") or []):
        if d.get("position"):
            d["position"] = [round(v, 1) for v in mp(*d["position"])]
        if d.get("polygon"):
            d["polygon"] = pts(d["polygon"])
        if d.get("bbox_px"):
            d["bbox_px"] = _bbox_map(d["bbox_px"], mp)
        if d.get("orientation_deg") is not None:
            d["orientation_deg"] = _angle_map(d["orientation_deg"], sx, sy)
    for w in (g.get("windows") or []):
        if w.get("position"):
            w["position"] = [round(v, 1) for v in mp(*w["position"])]
        if w.get("bbox_px"):
            w["bbox_px"] = _bbox_map(w["bbox_px"], mp)
        if w.get("orientation_deg") is not None:
            w["orientation_deg"] = _angle_map(w["orientation_deg"], sx, sy)
    for f in (g.get("fixtures") or []):
        if f.get("position"):
            f["position"] = [round(v, 1) for v in mp(*f["position"])]
        if f.get("polygon"):
            f["polygon"] = pts(f["polygon"])
        if f.get("bbox_px"):
            f["bbox_px"] = _bbox_map(f["bbox_px"], mp)
    for e in (g.get("edges") or []):
        if e.get("door_pos"):
            e["door_pos"] = [round(v, 1) for v in mp(*e["door_pos"])]
        if e.get("door_swing_deg") is not None:
            e["door_swing_deg"] = _angle_map(e["door_swing_deg"], sx, sy)

    g["plan_id"] = target_id
    g["unit_id"] = target_id
    meta = g.setdefault("meta", {})
    m = re.search(r"_FP_(.+?)_u(\d+)$", target_id)
    if m:
        meta["source_sheet_id"] = target_id[:target_id.rindex("_u")]
        meta["unit_index"] = int(m.group(2))
    return g
