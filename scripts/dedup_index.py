#!/usr/bin/env python3
"""dedup_index — 중복 도면(동일 도면) 그룹핑 + 보정 불일치 검출.

문제: 같은 도면이 데이터셋에 여러 번 들어 있고, 알바가 그 사본들을 **각각 다르게**
보정했다. 어느 보정이 맞는지 모르니 자동 복사(전파)를 할 수 없다.

이 스크립트가 하는 일:
  1) parsed/graphs 전체를 **도형 서명(shape signature)** 으로 묶는다.
     - 서명 = 방 폴리곤 좌표를 (평행이동·미러) 정규화 후 양자화 → 해시
     - 즉 "같은 도면이면 시트 어디에 놓였든·좌우 뒤집혔든 같은 그룹"
  2) 그룹마다 corrected/edits 에 보정본이 있는 멤버를 찾는다.
  3) 보정본들을 **같은 좌표계(그룹 정규 프레임)로 옮겨** 비교한다.
     - 방 위치를 허용오차 안에서 짝짓고 역할까지 같아야 '같은 보정'
     - 같은 게 2가지 이상이면 불일치(= 사람이 눈으로 봐야 할 대상)
  4) 산출:
     - data/staging/dedup_index.json   (에디터 '중복 검수' 화면이 읽는 인덱스)
     - docs/DEDUP_CONFLICTS.md         (사람이 눈으로 볼 표)

원칙: **읽기 전용**. 이 스크립트는 어떤 보정본도 고치거나 지우지 않는다.

실행:  python scripts/dedup_index.py
       python scripts/dedup_index.py --quant 2   (좌표 오차 관대하게)
"""
import argparse
import collections
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from plan2graph.dedup import corr_cells, same_correction, shape_sig  # noqa: E402

HOME = os.path.expanduser("~")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGING = os.path.join(ROOT, "data", "staging")
PARSED = os.path.join(STAGING, "parsed", "graphs")
EDITS = os.path.join(STAGING, "corrected", "edits")
OUT_JSON = os.path.join(STAGING, "dedup_index.json")
OUT_MD = os.path.join(ROOT, "docs", "DEDUP_CONFLICTS.md")


def roles_str(cnt):
    return " ".join(f"{k}×{v}" if v > 1 else k
                    for k, v in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0])))


def roles_diff(a, b):
    """두 역할카운트의 차이를 사람이 읽는 문자열로. a에만 / b에만."""
    keys = set(a) | set(b)
    only_a, only_b = [], []
    for k in sorted(keys):
        d = a.get(k, 0) - b.get(k, 0)
        if d > 0:
            only_a.append(f"{k}+{d}")
        elif d < 0:
            only_b.append(f"{k}+{-d}")
    return ", ".join(only_a) or "-", ", ".join(only_b) or "-"


# ─────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quant", type=float, default=1.0, help="도형 서명 양자화(px)")
    ap.add_argument("--tol", type=float, default=10.0, help="보정 비교 시 방 위치 허용오차(px)")
    ap.add_argument("--parsed", default=PARSED)
    ap.add_argument("--edits", default=EDITS)
    ap.add_argument("--out", default=OUT_JSON)
    ap.add_argument("--md", default=OUT_MD)
    ap.add_argument("--md-top", type=int, default=200, help="표에 실을 불일치 그룹 수")
    args = ap.parse_args()

    t0 = time.time()
    files = sorted(f for f in os.listdir(args.parsed) if f.endswith(".json"))
    print(f"[1/3] parsed 스캔 {len(files)}건 …", flush=True)

    sig_of, tf_of, verts_of = {}, {}, {}
    groups = collections.defaultdict(list)
    for i, f in enumerate(files, 1):
        gid = f[:-5]
        try:
            with open(os.path.join(args.parsed, f), encoding="utf-8") as fh:
                g = json.load(fh)
        except Exception:  # noqa: BLE001
            continue
        s, t = shape_sig(g, args.quant)
        if not s:
            continue
        sig_of[gid] = s
        tf_of[gid] = t
        groups[s].append(gid)
        if s not in verts_of:   # 서명이 몇 개의 좌표로 만들어졌나 = 서명 신뢰도
            verts_of[s] = sum(len(r["polygon"]) for r in (g.get("rooms") or {}).values()
                              if r.get("polygon"))
        if i % 5000 == 0:
            print(f"      {i}/{len(files)}", flush=True)

    print(f"[2/3] 그룹 {len(groups)}개 · 보정본 비교 …", flush=True)
    done = set()
    if os.path.isdir(args.edits):
        done = {f[:-5] for f in os.listdir(args.edits) if f.endswith(".json")}

    out_groups = {}
    n_conflict = n_agree = n_single = 0
    for s, members in groups.items():
        members.sort()
        corr = [m for m in members if m in done]
        rec = {"members": members, "n": len(members), "corrected": corr,
               "verts": verts_of.get(s, 0)}
        if not corr:
            rec["status"] = "none"
            rec["pending"] = len(members)      # 아무도 안 건드린 그룹 = 전원이 미보정
            out_groups[s] = rec
            continue
        # 탐욕 클러스터링: 각 보정본을 기존 변종 대표와 허용오차 비교 → 같으면 합류, 아니면 새 변종
        variants = []
        for m in corr:
            try:
                with open(os.path.join(args.edits, m + ".json"), encoding="utf-8") as fh:
                    e = json.load(fh)
            except Exception:  # noqa: BLE001
                continue
            cells, roles = corr_cells(e, tf_of[m])
            if not cells:
                continue
            held = ((e.get("meta") or {}).get("review_status") == "모호")
            hit = None
            for v in variants:
                if same_correction(cells, v["_cells"], args.tol):
                    hit = v
                    break
            if hit is None:
                hit = {"members": [], "roles": dict(roles), "n_rooms": len(cells),
                       "held": 0, "_cells": cells}
                variants.append(hit)
            hit["members"].append(m)
            if held:
                hit["held"] += 1
        for v in variants:
            v["count"] = len(v["members"])
            v.pop("_cells", None)
        variants.sort(key=lambda v: -v["count"])
        rec["variants"] = variants
        rec["n_entrance"] = max((v["roles"].get("현관", 0) for v in variants), default=0)
        if len(rec["variants"]) >= 2:
            rec["status"] = "conflict"
            n_conflict += 1
        elif len(corr) >= 2:
            rec["status"] = "agree"
            n_agree += 1
        else:
            rec["status"] = "single"
            n_single += 1
        rec["pending"] = len(members) - len(corr)     # 이 그룹에서 아직 미보정인 형제 수
        out_groups[s] = rec

    idx = {
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "quant": args.quant, "tol": args.tol,
        "n_plans": len(sig_of), "n_groups": len(groups), "n_corrected": len(done),
        "n_conflict": n_conflict, "n_agree": n_agree, "n_single": n_single,
        "groups": out_groups,
        "sig_of": sig_of,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(idx, fh, ensure_ascii=False)
    print(f"      → {args.out}", flush=True)

    # ── 표(마크다운) ────────────────────────────────────────────────────────
    print("[3/3] 표 작성 …", flush=True)
    conf = [(s, r) for s, r in out_groups.items() if r["status"] == "conflict"]
    # 영향 큰 순: 보정본 수 × 변종 수, 그다음 미보정 형제 수(전파 이득)
    conf.sort(key=lambda sr: (-len(sr[1]["corrected"]), -len(sr[1]["variants"]),
                              -sr[1]["pending"]))
    n_conf_files = sum(len(r["corrected"]) for _s, r in conf)
    n_conf_sib = sum(r["pending"] for _s, r in conf)

    L = []
    L.append("# 중복 도면 — 보정 불일치 표 (DEDUP_CONFLICTS)")
    L.append("")
    L.append(f"> 생성 {idx['built_at']} · `scripts/dedup_index.py` 자동 생성 (읽기 전용 분석)")
    L.append("> 같은 도면(위치·미러 무관)인데 **보정 결과가 서로 다른** 그룹만 모았다.")
    L.append("> 도면번호(plan_id)로 에디터에서 검색하면 바로 열린다.")
    L.append("")
    L.append("## 요약")
    L.append("")
    L.append("| 항목 | 값 |")
    L.append("|---|--:|")
    L.append(f"| 전체 도면 | {idx['n_plans']:,} |")
    L.append(f"| 고유 도면(그룹) | {idx['n_groups']:,} |")
    L.append(f"| 보정 완료 | {idx['n_corrected']:,} |")
    L.append(f"| 보정 2건 이상이고 **서로 일치** | {n_agree:,} 그룹 |")
    L.append(f"| 보정 2건 이상인데 **불일치** ⚠ | {n_conflict:,} 그룹 |")
    L.append(f"| └ 불일치에 묶인 보정 파일 | {n_conf_files:,} 건 |")
    L.append(f"| └ 불일치 그룹의 미보정 형제(전파 보류분) | {n_conf_sib:,} 건 |")
    L.append(f"| 보정 1건뿐(비교 불가·바로 전파 가능) | {n_single:,} 그룹 |")
    L.append("")
    L.append("## 불일치 그룹 (영향 큰 순)")
    L.append("")
    L.append("`변종` = 서로 다른 보정 결과의 가짓수. 각 변종의 대표 도면번호를 열어 비교하면 된다.")
    L.append("")
    L.append("`현관` 열이 2 이상이면 세대분리 실패(한 장에 여러 세대)라 보정 자체가 무의미할 수 있다.")
    L.append("")
    L.append("| # | 그룹 | 사본 | 보정 | 변종 | 미보정 | 현관 | 변종별 대표 도면번호 → 역할구성 |")
    L.append("|--:|---|--:|--:|--:|--:|--:|---|")
    for i, (s, r) in enumerate(conf[:args.md_top], 1):
        cells = []
        for v in r["variants"][:6]:
            rep = v["members"][0]
            more = f" (+{v['count']-1})" if v["count"] > 1 else ""
            cells.append(f"`{rep}`{more} → {roles_str(collections.Counter(v['roles']))}")
        if len(r["variants"]) > 6:
            cells.append(f"…외 {len(r['variants'])-6}개 변종")
        L.append(f"| {i} | `{s}` | {r['n']} | {len(r['corrected'])} | "
                 f"{len(r['variants'])} | {r['pending']} | {r.get('n_entrance', 0)} | "
                 + "<br>".join(cells) + " |")
    if len(conf) > args.md_top:
        L.append("")
        L.append(f"> …외 {len(conf)-args.md_top:,}개 그룹. 전체는 에디터 **🔁 중복 검수** 화면에서.")

    L.append("")
    L.append("## 변종 차이 상세 (상위 40그룹)")
    L.append("")
    L.append("| 그룹 | A(다수안) | B | A에만 | B에만 |")
    L.append("|---|---|---|---|---|")
    for s, r in conf[:40]:
        a = r["variants"][0]
        for b in r["variants"][1:3]:
            da, db = roles_diff(collections.Counter(a["roles"]), collections.Counter(b["roles"]))
            L.append(f"| `{s}` | `{a['members'][0]}` ({a['count']}건) | "
                     f"`{b['members'][0]}` ({b['count']}건) | {da} | {db} |")

    os.makedirs(os.path.dirname(args.md), exist_ok=True)
    with open(args.md, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print(f"      → {args.md}", flush=True)
    print(f"완료 {time.time()-t0:.0f}s · 불일치 {n_conflict} / 일치 {n_agree} / 단일 {n_single}")


if __name__ == "__main__":
    sys.exit(main())
