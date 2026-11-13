#!/usr/bin/env python3
"""ann_mismatch — 주석이 자기 도면 것인가: 선언 크기 vs 실제 PNG 크기 전수 검사.

AI-Hub 주석 JSON의 `images[].width/height` 가 실제 원천 PNG 크기와 다르면,
그 주석은 **다른 도면의 것**일 가능성이 크다(실측 확증: 크기 불일치 시트의 평면
93개 중 88개가 다른 도면과 기하 완전 일치 — DATA.md §8.14).

⚠ 이 검사의 한계: **두 도면의 크기가 같으면 못 잡는다.** 사본이 있는 도면은
`dedup_verify_pixels.py`(픽셀 대조)가 보완하지만, **사본이 1장뿐인 평면 12,589장
(30.5%)은 어느 쪽도 못 본다** → 벽선 정합 검사가 필요하다.

주석 zip은 **4종 × 2분할 = 8개**(SPA 공간 · STR 구조 · OBJ 객체 · OCR 문자)다.
도면 PNG가 어느 종에서 왔든 같은 이름의 JSON이 같은 종의 라벨 zip에 있다.
**전부 인덱싱해야 전수가 된다.**

산출: data/staging/ann_image_mismatch.json  [[sheet, [선언w,선언h], [실제w,실제h]], ...]
원칙: 읽기 전용.
"""
from __future__ import annotations

import collections
import json
import os
import struct
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ST = os.path.join(ROOT, "data", "staging")
RAW = os.path.join(ROOT, "data", "raw", "aihub", "01-1.정식개방데이터")
OUT = os.path.join(ST, "ann_image_mismatch.json")


def build_label_index():
    """주석 zip 8개 전부 → {파일명: (zip경로, 엔트리)}"""
    lbl = {}
    for part, pre in (("Training", "TL"), ("Validation", "VL")):
        for cat in ("SPA", "STR", "OBJ", "OCR"):
            p = os.path.join(RAW, part, "02.라벨링데이터", "%s_%s.zip" % (pre, cat))
            if not os.path.exists(p):
                print("   (없음) %s" % os.path.basename(p), file=sys.stderr)
                continue
            z = zipfile.ZipFile(p)
            n = 0
            for e in z.namelist():
                if e.lower().endswith(".json"):
                    lbl[e.split("/")[-1]] = (p, e)
                    n += 1
            print("   %s_%s.zip  %6d" % (pre, cat, n), file=sys.stderr)
    return lbl


_zc = {}


def zf(p):
    if p not in _zc:
        _zc[p] = zipfile.ZipFile(p)
    return _zc[p]


def png_size(head):
    """PNG 헤더 24바이트에서 폭·높이만 읽는다(이미지 디코딩 안 함)."""
    return struct.unpack(">II", head[16:24])


def main():
    idx = json.load(open(os.path.join(ST, "parsed", "_png_index.json"), encoding="utf-8"))
    print("[1/2] 주석 zip 인덱싱", file=sys.stderr)
    lbl = build_label_index()
    print("   합계 %d개 주석 파일" % len(lbl), file=sys.stderr)

    print("[2/2] 시트 %d개 대조" % len(idx), file=sys.stderr)
    c = collections.Counter()
    bad = []
    for i, (sheet, (zpath, entry)) in enumerate(sorted(idx.items()), 1):
        base = entry.split("/")[-1]
        jname = base.rsplit(".", 1)[0] + ".json"
        ent = lbl.get(jname)
        if ent is None:
            c["라벨없음"] += 1
            continue
        try:
            d = json.loads(zf(ent[0]).read(ent[1]))
            im = (d.get("images") or [{}])[0]
            decl = (im.get("width"), im.get("height"))
            with zf(zpath).open(entry) as f:
                real = png_size(f.read(24))
        except Exception:  # noqa: BLE001
            c["읽기실패"] += 1
            continue
        if None in decl:
            c["크기미기재"] += 1
            continue
        if tuple(decl) != real:
            c["불일치"] += 1
            bad.append([sheet, list(decl), list(real)])
        else:
            c["일치"] += 1
        if i % 4000 == 0:
            print("   %d/%d" % (i, len(idx)), file=sys.stderr)

    json.dump(bad, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print("\n전수:", dict(c))
    print("불일치 %d시트 → %s" % (len(bad), os.path.relpath(OUT, ROOT)))


if __name__ == "__main__":
    main()
