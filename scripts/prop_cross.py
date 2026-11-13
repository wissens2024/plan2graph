#!/usr/bin/env python3
"""그룹이 달라도 **같은 그림이면** 확정본을 전파한다.

사용자(2026-11-14): "이거랑 좀전에 한거랑 동일 도면 아닌가?"

맞았다. dedup 그룹은 **파싱된 방 분할의 서명**으로 묶이기 때문에, 같은 세대를
다르게 분할해 놓으면 같은 그림인데도 다른 그룹에 떨어진다. 실측:
  583b053a_u0 (그룹 d88a4f8f) 와 44549e8c_u0 (그룹 1c5e9ff9)
  시트 전체로는 8.36% 픽셀이 다르지만, **세대 영역 안에서는 다른 픽셀이 0개**다.
  (시트에 실린 다른 세대가 달라서 전체 해시가 갈린 것이다.)

그래서 그룹을 넘나드는 전파가 필요하다. 안전장치는 그대로 쓴다:
  · 세대 영역 픽셀이 완전히 같아야 한다(--max-diff 픽셀 이하)
  · dedup.alignment_error 가 _ALIGN_TOL(2px) 이내여야 한다
  · dedup.transform_graph 로 좌표를 옮긴다(직접 베끼지 않는다)
  · 덮어쓰기 전 백업
읽기 전용(기본). --apply 를 줘야 쓴다.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import time

import numpy as np
from PIL import Image

ROOT = "/home/ju/plan2graph"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
from plan2graph import dedup                       # noqa: E402
from plan2graph.dedup_review import _ALIGN_TOL     # noqa: E402
import scripts.edit_server as es                   # noqa: E402

ST = os.path.join(ROOT, "data", "staging")
GRAPHS = os.path.join(ST, "parsed", "graphs")
EDITS = os.path.join(ST, "corrected", "edits")
BK = os.path.join(ST, "corrected", "_dedup_backup")


def load(d, p):
    f = os.path.join(d, p + ".json")
    return json.load(open(f, encoding="utf-8")) if os.path.exists(f) else None


def unit_box(g, pad=8):
    xs, ys = [], []
    for r in (g.get("rooms") or {}).values():
        for x, y in (r.get("polygon") or []):
            xs.append(x); ys.append(y)
    return (int(min(xs)) - pad, int(min(ys)) - pad,
            int(max(xs)) + pad, int(max(ys)) + pad)


def gray(pid, box):
    b = es._png_bytes(pid)
    if b is None:
        return None
    im = Image.open(io.BytesIO(b)).convert("L")
    return np.asarray(im.crop(box)).astype(np.int16)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("targets", nargs="+")
    ap.add_argument("--max-diff", type=int, default=0,
                    help="세대 영역에서 허용하는 '다른 픽셀' 수 (기본 0 = 완전 동일)")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    es._build_png_index()
    src_c = load(EDITS, a.source)
    src_p = load(GRAPHS, a.source)
    if src_c is None or src_p is None:
        print("원본 없음"); sys.exit(1)
    _s, t_src = dedup.shape_sig(src_p)
    if not t_src:
        print("원본 도형 서명 실패"); sys.exit(1)
    box = unit_box(src_c)
    ga = gray(a.source, box)
    print("원본 %s · 세대 영역 %s" % (a.source, list(box)))

    stamp = time.strftime("%Y%m%d_%H%M%S")
    bk = os.path.join(BK, stamp + "_cross")
    ok = 0
    for t in a.targets:
        tgt_p = load(GRAPHS, t)
        if tgt_p is None:
            print("  X %-34s 파싱본 없음" % t); continue
        gb = gray(t, box)
        nz = int((np.abs(ga - gb) > 8).sum()) if (ga is not None and gb is not None) else -1
        _s2, t_tgt = dedup.shape_sig(tgt_p)
        err = dedup.alignment_error(src_p, tgt_p, t_src, t_tgt)
        verdict = []
        if nz < 0:
            verdict.append("PNG 없음")
        elif nz > a.max_diff:
            verdict.append("세대 픽셀 %d개 다름" % nz)
        if not (err <= _ALIGN_TOL):
            verdict.append("정렬오차 %.2fpx" % err)
        print("  %-34s 세대 다른 픽셀 %-7s 정렬오차 %-7s %s"
              % (t, nz, "%.2f" % err if err == err else "nan",
                 "거부: " + " · ".join(verdict) if verdict else "통과"))
        if verdict or not a.apply:
            continue
        g = dedup.transform_graph(src_c, t_src, t_tgt, t)
        meta = g.setdefault("meta", {})
        meta["propagated"] = True
        meta["correction_source"] = "propagated"
        meta["propagated_from"] = a.source
        meta["propagated_at"] = stamp
        meta["propagated_cross_group"] = True
        g["corrected"] = (meta.get("review_status") != "모호")
        dst = os.path.join(EDITS, t + ".json")
        if os.path.exists(dst):
            os.makedirs(bk, exist_ok=True)
            shutil.copy2(dst, os.path.join(bk, t + ".json"))
        tmp = dst + ".%d.tmp" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(g, f, ensure_ascii=False)
        os.replace(tmp, dst)
        ok += 1
    print("\n%s %d장" % ("적용" if a.apply else "[드라이런] 통과", ok if a.apply else 0))
    if a.apply and ok:
        print("백업 -> " + bk)
        out = os.path.join(ROOT, "data", "propagated_plans.json")
        log = json.load(open(out, encoding="utf-8")) if os.path.exists(out) else []
        log.append({"sig": "cross", "source": a.source, "targets": a.targets,
                    "written": ok, "overwritten": ok, "backup": bk})
        json.dump(log, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
