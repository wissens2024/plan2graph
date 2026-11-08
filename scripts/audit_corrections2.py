"""알바 보정 최종 회계 v2 — 방을 ID가 아니라 기하(IoU)로 짝지어 '확인'과 '수정'을 분리.

에디터는 합치기/나누기/삭제 후 방 번호를 재부여한다. 그래서 ID 매칭은
같은 방을 '삭제+추가'로 이중계상한다. IoU 그리디 매칭으로 동일 방을 추적한다.

분류(에디터가 노출한 축에 한해):
  역할 확인 = 짝이 있고 역할 동일   → 사람이 보고 맞다고 보증
  역할 수정 = 짝이 있고 역할 다름
  기하 확인 = IoU >= 0.98
  기하 수정 = 0.5 <= IoU < 0.98
  흡수/삭제 = parsed에만 있음 (짝 없음)
  신설      = corrected에만 있음 (짝 없음)
"""
from __future__ import annotations
import json, os, sys
from shapely.geometry import Polygon
from shapely.ops import unary_union

PARSED = "data/staging/parsed/graphs"
EDITS = "data/staging/corrected/edits"
GATED = "data/staging/korean_gated_ids.json"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 0
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/audit_corr2.json"
IOU_SAME, IOU_IDENT = 0.5, 0.98


def poly(p):
    if not p or len(p) < 3:
        return None
    try:
        g = Polygon(p)
        if not g.is_valid:
            g = g.buffer(0)
        return g if (g.area > 1e-6 and not g.is_empty) else None
    except Exception:
        return None


def role_of(r):
    return r.get("role") or r.get("base")


def load_ids(path):
    if not os.path.exists(path):
        return set()
    d = json.load(open(path, encoding="utf-8"))
    if isinstance(d, dict):
        for k in ("ids", "plan_ids", "kept", "items"):
            if isinstance(d.get(k), list):
                return set(str(x) for x in d[k])
        return set(str(x) for x in d)
    return set(str(x) for x in d)


gated = load_ids(GATED)
pid_p = set(e.name[:-5] for e in os.scandir(PARSED) if e.name.endswith(".json"))
pid_e = set(e.name[:-5] for e in os.scandir(EDITS) if e.name.endswith(".json"))
paired = sorted(pid_p & pid_e)
if N:
    paired = paired[:N]
print("[pool] paired=%d (gated 중 보정 %d)" % (len(paired), len(gated & pid_e)), flush=True)

K = ("plans", "rooms_p", "rooms_c", "matched", "role_ok", "role_chg",
     "geom_ident", "geom_chg", "absorbed", "created", "role_chg_on_ident")
ALL = {k: 0 for k in K}
GAT = {k: 0 for k in K}
# ⚠ edits 30,542건 중 18,018건은 dedup 전파 복사본(meta.propagated)이다. 사본끼리 같은
# 보정이 반복되므로 ALL/GAT는 사본 가중 평균이 된다. 전파본을 뺀 '사람 직접' 축을 따로 센다.
DIR = {k: 0 for k in K}
GDR = {k: 0 for k in K}
pairs = {}
plan_kind = {}
plan_kind_direct = {}

for n, pid in enumerate(paired, 1):
    try:
        gp = json.load(open("%s/%s.json" % (PARSED, pid), encoding="utf-8"))
        ge = json.load(open("%s/%s.json" % (EDITS, pid), encoding="utf-8"))
    except Exception:
        continue
    RP = [(k, v, poly(v.get("polygon"))) for k, v in (gp.get("rooms") or {}).items()]
    RC = [(k, v, poly(v.get("polygon"))) for k, v in (ge.get("rooms") or {}).items()]
    RP = [x for x in RP if x[2] is not None]
    RC = [x for x in RC if x[2] is not None]

    cand = []
    for i, (_, _, ga) in enumerate(RP):
        ba = ga.bounds
        for j, (_, _, gb) in enumerate(RC):
            bb = gb.bounds
            if ba[2] < bb[0] or bb[2] < ba[0] or ba[3] < bb[1] or bb[3] < ba[1]:
                continue
            try:
                inter = ga.intersection(gb).area
            except Exception:
                continue
            if inter <= 0:
                continue
            u = ga.area + gb.area - inter
            iou = inter / u if u > 0 else 0.0
            if iou >= IOU_SAME:
                cand.append((iou, i, j))
    cand.sort(reverse=True)
    usedP, usedC, mt = set(), set(), []
    for iou, i, j in cand:
        if i in usedP or j in usedC:
            continue
        usedP.add(i); usedC.add(j); mt.append((iou, i, j))

    rok = rchg = gid = gchg = rchg_ident = 0
    for iou, i, j in mt:
        a, b = role_of(RP[i][1]), role_of(RC[j][1])
        if a == b:
            rok += 1
        else:
            rchg += 1
            pairs[(a, b)] = pairs.get((a, b), 0) + 1
            if iou >= IOU_IDENT:
                rchg_ident += 1
        if iou >= IOU_IDENT:
            gid += 1
        else:
            gchg += 1

    rec = {"plans": 1, "rooms_p": len(RP), "rooms_c": len(RC), "matched": len(mt),
           "role_ok": rok, "role_chg": rchg, "geom_ident": gid, "geom_chg": gchg,
           "absorbed": len(RP) - len(mt), "created": len(RC) - len(mt),
           "role_chg_on_ident": rchg_ident}
    for k in K:
        ALL[k] += rec[k]
    if pid in gated:
        for k in K:
            GAT[k] += rec[k]
    is_direct = not bool((ge.get("meta") or {}).get("propagated"))
    if is_direct:
        for k in K:
            DIR[k] += rec[k]
        if pid in gated:
            for k in K:
                GDR[k] += rec[k]

    if rec["absorbed"] or rec["created"]:
        kind = "구조변경"
    elif gchg:
        kind = "기하수정"
    elif rchg:
        kind = "라벨수정"
    else:
        kind = "전면확인"
    plan_kind[kind] = plan_kind.get(kind, 0) + 1
    if is_direct:
        plan_kind_direct[kind] = plan_kind_direct.get(kind, 0) + 1

    if n % 2000 == 0:
        print("  ...%d/%d  확인%d 수정%d 흡수%d 신설%d"
              % (n, len(paired), ALL["role_ok"], ALL["role_chg"],
                 ALL["absorbed"], ALL["created"]), flush=True)

top = sorted(pairs.items(), key=lambda kv: -kv[1])[:30]
out = {"all": ALL, "gated": GAT, "direct": DIR, "gated_direct": GDR,
       "plan_kind": plan_kind, "plan_kind_direct": plan_kind_direct,
       "iou_same": IOU_SAME, "iou_identical": IOU_IDENT,
       "top_role_changes": [{"from": a, "to": b, "n": c} for (a, b), c in top]}
json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def show(tag, A):
    R = A["rooms_p"] or 1
    print("-" * 74, flush=True)
    print("%s: 도면 %d · parsed 방 %d → corrected 방 %d (순증 %+d, %+.1f%%)"
          % (tag, A["plans"], A["rooms_p"], A["rooms_c"], A["rooms_c"] - A["rooms_p"],
             100.0 * (A["rooms_c"] - A["rooms_p"]) / R), flush=True)
    print("  짝지어진 방 %d (%.1f%%)  |  흡수·삭제 %d (%.1f%%)  신설 %d"
          % (A["matched"], 100.*A["matched"]/R, A["absorbed"], 100.*A["absorbed"]/R,
             A["created"]), flush=True)
    M = A["matched"] or 1
    print("  역할  ✅확인 %d (%.1f%% of 짝)   ✏수정 %d (%.1f%%)"
          % (A["role_ok"], 100.*A["role_ok"]/M, A["role_chg"], 100.*A["role_chg"]/M), flush=True)
    print("  기하  ✅동일(IoU≥.98) %d (%.1f%%)   ✏수정 %d (%.1f%%)"
          % (A["geom_ident"], 100.*A["geom_ident"]/M, A["geom_chg"],
             100.*A["geom_chg"]/M), flush=True)
    print("  기하 동일한데 라벨만 바뀜: %d" % A["role_chg_on_ident"], flush=True)


print("=" * 74, flush=True)
show("전체(전파본 포함·사본 가중)", ALL)
show("사람 직접 보정만(전파본 제외)", DIR)
show("학습코퍼스(gated)", GAT)
show("학습코퍼스 ∩ 사람 직접", GDR)
print("-" * 74, flush=True)
print("도면 분류(전체):", json.dumps(plan_kind, ensure_ascii=False), flush=True)
print("도면 분류(사람 직접):", json.dumps(plan_kind_direct, ensure_ascii=False), flush=True)
print("상위 역할 교정:", json.dumps(out["top_role_changes"][:12], ensure_ascii=False), flush=True)
print("[json]", OUT, flush=True)
