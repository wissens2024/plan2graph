#!/usr/bin/env python3
"""fix_role_ocr — **도면에 인쇄된 이름으로 역할을 고친다.** 추측이 아니다.

사용자(2026-11-13): *"그럼 대피공간 역할 추가하고 95%는 대피공간으로 바꾸면 안 되나?"*

역할 감사에서 나온 것: 역할 `기타` 는 OCR 일치율 **0%** 이고, 글자가 찍힌 `기타` 방
3,642개가 **전부 이름이 있는 방**이며 **그중 95%가 `대피공간`** 이다.
원인은 간단했다 — `topoedit.ROLES`(에디터 역할 선택 목록)에 **대피공간이 없어서**
고를 수가 없었다. 코덱 어휘에는 2026-06-16부터 있었다.

이 스크립트는 **인쇄된 글자가 방 안에 찍힌 경우에만** 역할을 바꾼다.
  · 조인: plan_id = {house}_FP_{sig}_u{n} → inspect_excluded.build_index(split) 로
    sig → OCR 9자리 key → 라벨 zip `/{house}_FP_OCR_{key}.json`
    (⚠ build_index 의 OCR 엔트리는 **원천 PNG** 다. 글자는 라벨 zip 에 있다.)
  · 한 시트의 모든 유닛 방을 한 풀로 합쳐 판정한다(글자는 시트 전체에 찍힌다).
  · 글자 상자 중심이 방 안에 들어간 것만. 겹치면 **가장 작은 방**을 택한다.
  · 바꾸기 전 역할은 `role_parsed` 에, 출처는 `role_source="ocr"` 에 남긴다.

기본은 `--only 대피공간` — 사용자가 확인한 것만 건드린다. 다른 역할로 넓히려면
`--only` 를 바꾸거나 `--all` 을 준다(그때는 바꾼 조합을 전부 출력한다).

읽기 전용(기본). --apply 를 줘야 쓴다.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import re
import shutil
import sys
import zipfile

ROOT = "/home/ju/plan2graph"
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)
from shapely.geometry import Point, Polygon       # noqa: E402
from plan2graph import inspect_excluded as ix     # noqa: E402

EDITS = os.path.join(ROOT, "data", "staging", "corrected", "edits")
BK = os.path.join(ROOT, "data", "staging", "corrected", "_manual_backup")
RAW = os.path.join(ROOT, "data", "raw", "aihub", "01-1.정식개방데이터")
OCR_ZIPS = [os.path.join(RAW, p, "02.라벨링데이터", z)
            for p, z in (("Training", "TL_OCR.zip"), ("Validation", "VL_OCR.zip"))]

# 인쇄 표기 → 우리 역할. 여기 없는 글자는 건드리지 않는다.
# 사용자 확인(2026-11-13): 대피공간만 넣는다. 테라스는 발코니와 같아서 별칭으로 뺐다.
NORM = {"대피공간": "대피공간"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=["대피공간"])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--from-role", nargs="*", default=["기타"],
                    help="이 역할인 방만 바꾼다(기본: 기타)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    want = None if a.all else set(a.only)
    from_role = set(a.from_role)

    idx = {}
    for split in ("Training", "Validation"):
        idx.update(ix.build_index(split))
    label_at = {}
    for p in OCR_ZIPS:
        if not os.path.exists(p):
            continue
        for n in zipfile.ZipFile(p).namelist():
            if "_FP_OCR_" in n:
                label_at[n] = p
    zc = {}

    def zf(p):
        if p not in zc:
            zc[p] = zipfile.ZipFile(p)
        return zc[p]

    pat = re.compile(r"^([A-Z]+)_FP_([0-9a-f]{8}_\d+)(?:_u(\d+))?\.json$")
    sheets = collections.defaultdict(list)
    for f in os.listdir(EDITS):
        m = pat.match(f)
        if m:
            sheets[m.group(2)].append(f)

    joinable = []
    for sig, fs in sheets.items():
        d = idx.get(sig)
        if not d or "OCR" not in d:
            continue
        ent = "/%s_FP_OCR_%s.json" % (d["OCR"]["house"], d["OCR"]["key"])
        if ent in label_at:
            joinable.append((sig, sorted(fs), ent))
    joinable.sort()
    if a.limit:
        joinable = joinable[: a.limit]
    print("시트 %d · OCR 조인 가능 %d (%.1f%%)"
          % (len(sheets), len(joinable), 100.0 * len(joinable) / max(len(sheets), 1)),
          file=sys.stderr)

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bk = os.path.join(BK, stamp + "_roleocr")
    changed = collections.Counter()
    n_sheet = n_file = 0
    for sig, fs, ent in joinable:
        try:
            coco = json.loads(zf(label_at[ent]).read(ent.lstrip("/")))
        except Exception:
            try:
                coco = json.loads(zf(label_at[ent]).read(ent))
            except Exception:
                continue
        texts = []
        for an in coco.get("annotations", []):
            at = an.get("attributes") or {}
            t = ""
            for v in at.values():
                if isinstance(v, str) and v.strip():
                    t = re.sub(r"\s+", "", v.strip())
                    break
            role = NORM.get(t)
            if not role or (want and role not in want):
                continue
            b = an.get("bbox")
            if b:
                texts.append((Point(b[0] + b[2] / 2.0, b[1] + b[3] / 2.0), role))
        if not texts:
            continue
        # 시트의 모든 유닛 방을 한 풀로
        pool = []
        graphs = {}
        for f in fs:
            p = os.path.join(EDITS, f)
            try:
                g = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            graphs[f] = g
            for rid, r in (g.get("rooms") or {}).items():
                poly = r.get("polygon") or []
                if len(poly) < 3:
                    continue
                q = Polygon([(x, y) for x, y in poly]).buffer(0)
                if not q.is_empty and q.geom_type == "Polygon":
                    pool.append((q.area, f, str(rid), q))
        if not pool:
            continue
        touched = set()
        for pt, role in texts:
            cand = [(ar, f, rid) for ar, f, rid, q in pool if q.contains(pt)]
            if not cand:
                continue
            ar, f, rid = min(cand)        # 겹치면 가장 작은 방
            r = graphs[f]["rooms"][rid]
            cur = r.get("role")
            if cur == role or (from_role and cur not in from_role):
                continue
            if "role_parsed" not in r:
                r["role_parsed"] = cur
            r["role"] = role
            r["role_source"] = "ocr"
            changed[(cur, role)] += 1
            touched.add(f)
        if touched:
            n_sheet += 1
            n_file += len(touched)
            if a.apply:
                os.makedirs(bk, exist_ok=True)
                for f in touched:
                    p = os.path.join(EDITS, f)
                    shutil.copy(p, os.path.join(bk, f))
                    tmp = p + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as fh:
                        json.dump(graphs[f], fh, ensure_ascii=False)
                    os.replace(tmp, p)

    print("\n바꾼 시트 %d · 도면 %d · 방 %d" % (n_sheet, n_file, sum(changed.values())))
    for (a0, b0), c in changed.most_common(20):
        print("   %-8s → %-8s %6d" % (a0, b0, c))
    print("백업 → " + bk if a.apply else "[드라이런] --apply 로 적용")


if __name__ == "__main__":
    main()
