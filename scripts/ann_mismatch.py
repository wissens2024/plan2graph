# -*- coding: utf-8 -*-
"""주석이 자기 도면 것인가 — 주석이 선언한 이미지 크기 vs 실제 PNG 크기.

AI-Hub SPA 주석 JSON의 images[].width/height 가 실제 원천 PNG 크기와 다르면,
그 주석은 **다른 도면의 것**일 가능성이 크다(= 주석↔이미지 오매칭).
읽기 전용.
"""
import json
import os
import struct
import sys
import zipfile

ROOT = "/home/ju/plan2graph"
IDX = json.load(open(os.path.join(ROOT, "data/staging/parsed/_png_index.json"), encoding="utf-8"))
LBL = {}
for part in ("Training", "Validation"):
    for nm in ("TL_SPA.zip", "VL_SPA.zip"):
        p = os.path.join(ROOT, "data/raw/aihub/01-1.정식개방데이터", part, "02.라벨링데이터", nm)
        if os.path.exists(p):
            z = zipfile.ZipFile(p)
            for e in z.namelist():
                if e.lower().endswith(".json"):
                    LBL[e.split("/")[-1]] = (p, e)

_zc = {}


def zf(p):
    if p not in _zc:
        _zc[p] = zipfile.ZipFile(p)
    return _zc[p]


def png_size(data):
    # PNG 헤더에서 폭·높이만 읽는다(디코딩 안 함)
    return struct.unpack(">II", data[16:24])


def check(sheet):
    ent = IDX.get(sheet)
    if not ent:
        return None
    zpath, entry = ent
    base = entry.split("/")[-1]
    jname = base.rsplit(".", 1)[0] + ".json"
    if jname not in LBL:
        return ("라벨없음", None, None)
    lp, le = LBL[jname]
    d = json.loads(zf(lp).read(le))
    im = (d.get("images") or [{}])[0]
    decl = (im.get("width"), im.get("height"))
    with zf(zpath).open(entry) as f:
        head = f.read(24)
    real = png_size(head)
    return ("불일치" if tuple(decl) != real else "일치", decl, real)


for sheet in sys.argv[1:]:
    r = check(sheet)
    print("%-22s %s" % (sheet, r))
