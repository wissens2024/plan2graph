#!/usr/bin/env python3
"""dedup_verify_pixels — 중복 그룹이 '진짜 같은 도면'인지 원본 PNG 픽셀로 전수 검증.

왜 필요한가: 그룹은 파싱된 방 폴리곤 좌표(도형 서명)로 묶었다. 그걸 다시 좌표로 확인하면
자기순환이라 아무것도 증명하지 못한다. 그래서 **파싱과 무관한 원본 이미지 픽셀**을 본다.

방법
  1) 사본 2장 이상인 그룹의 멤버 전원에 대해, 원본 시트 PNG에서 세대 bbox를 잘라낸다.
     · 시트를 한 번만 디코딩하도록 '시트 단위'로 처리(멤버 단위로 하면 같은 시트를 수십 번 연다)
     · 미러 그룹을 위해 shape_sig 의 정규 방향(fx,fy)으로 뒤집어 맞춘 뒤 비교
  2) 각 크롭에서 세 가지를 뽑는다
     · h_exact : 원본 픽셀 해시            → 완전 동일 판정
     · h_ink   : 이진화(<200) 후 해시       → 안티에일리어싱 잡음 무시
     · grid    : 32x32 잉크 밀도 격자        → 주석 차이의 '양'을 재는 용도
  3) 그룹별로 기준(첫 멤버) 대비 분류
     ① 완전 동일  ② 렌더 잡음만  ③ 주석/라벨만 차이(격자차 ≤2%)  ④ 그 외

산출: data/staging/dedup_pixel_verify.json  (그룹별 판정 + ④ 목록)
실행: PYTHONPATH=src python scripts/dedup_verify_pixels.py [--workers 6]
      읽기 전용 — 보정본을 고치지 않는다. 시트 PNG는 zip에서 메모리로만 읽고 캐시에 쓰지 않는다.
"""
import argparse
import collections
import hashlib
import io
import json
import os
import re
import sys
import time
import zipfile
from multiprocessing import Pool

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from plan2graph.dedup import shape_sig  # noqa: E402

Image.MAX_IMAGE_PIXELS = None            # 대형 도면 시트 = 정상 입력

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGING = os.path.join(ROOT, "data", "staging")
PARSED = os.path.join(STAGING, "parsed", "graphs")
PNG_CACHE = os.path.join(STAGING, "parsed", "png")
PNG_INDEX = os.path.join(STAGING, "parsed", "_png_index.json")
INDEX = os.path.join(STAGING, "dedup_index.json")
OUT = os.path.join(STAGING, "dedup_pixel_verify.json")

INK_T = 200          # 이 값보다 어두우면 잉크. 관측된 렌더 잡음(최대차 ~20)은 흰색(255) 근처라 안 걸린다
GRID = 32
_PIDX = None


def _sheet_of(gid):
    m = re.search(r"_FP_(.+?)_u\d+$", gid)
    return m.group(1) if m else None


def _load_sheet(sig):
    """시트 PNG → PIL(L). 캐시에 있으면 캐시, 없으면 zip에서 메모리로만(디스크 안 늘림)."""
    p = os.path.join(PNG_CACHE, sig + ".png")
    if os.path.exists(p):
        return Image.open(p).convert("L")
    e = (_PIDX or {}).get(sig)
    if not e:
        return None
    with zipfile.ZipFile(e[0]) as zf:
        data = zf.read(e[1])
    return Image.open(io.BytesIO(data)).convert("L")


def _init(pidx):
    global _PIDX
    _PIDX = pidx


def _do_sheet(job):
    """시트 1장 → 그 위 세대들의 (해시·격자). 시트는 딱 한 번만 디코딩한다."""
    sig, gids = job
    out = {}
    try:
        im = _load_sheet(sig)
        if im is None:
            return {g: None for g in gids}
        arr = np.asarray(im)
        im.close()
    except Exception:  # noqa: BLE001
        return {g: None for g in gids}
    for gid in gids:
        try:
            with open(os.path.join(PARSED, gid + ".json"), encoding="utf-8") as fh:
                g = json.load(fh)
            b = g.get("bbox_px")
            if not b or len(b) < 4:
                out[gid] = None
                continue
            _s, t = shape_sig(g)
            x0, y0 = int(round(b[0])), int(round(b[1]))
            x1, y1 = x0 + int(round(b[2])), y0 + int(round(b[3]))
            h, w = arr.shape
            sub = arr[max(0, y0):min(h, y1), max(0, x0):min(w, x1)]
            if sub.size == 0:
                out[gid] = None
                continue
            if t:                                  # 미러 그룹: 정규 방향으로 맞춘 뒤 비교
                if t[0] == -1:
                    sub = sub[:, ::-1]
                if t[1] == -1:
                    sub = sub[::-1, :]
            sub = np.ascontiguousarray(sub)
            ink = sub < INK_T
            gimg = Image.fromarray((ink * 255).astype(np.uint8)).resize(
                (GRID, GRID), Image.BOX)
            out[gid] = {
                "e": hashlib.sha1(sub.tobytes()).hexdigest()[:12],
                "i": hashlib.sha1(np.packbits(ink).tobytes()).hexdigest()[:12],
                "n": int(ink.sum()),
                "s": [int(sub.shape[0]), int(sub.shape[1])],
                "g": np.asarray(gimg, dtype=np.uint8).ravel().tolist(),
            }
        except Exception:  # noqa: BLE001
            out[gid] = None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--tol", type=float, default=0.02, help="주석차로 볼 격자 상대차 상한")
    ap.add_argument("--outlier", type=float, default=0.2,
                    help="이 값을 넘으면 '주석이 딴 도면에 얹힌' 이탈 멤버로 분리")
    a = ap.parse_args()

    idx = json.load(open(INDEX, encoding="utf-8"))
    pidx = json.load(open(PNG_INDEX, encoding="utf-8"))
    groups = {s: r for s, r in idx["groups"].items() if r["n"] >= 2}
    need = {m for r in groups.values() for m in r["members"]}
    sheets = {}
    for gid in need:
        sheets.setdefault(_sheet_of(gid), []).append(gid)
    jobs = [(s, v) for s, v in sheets.items() if s]
    print(f"[전수] 그룹 {len(groups):,} · 도면 {len(need):,} · 시트 {len(jobs):,} "
          f"· 워커 {a.workers}", flush=True)

    feats, t0, done = {}, time.time(), 0
    with Pool(a.workers, initializer=_init, initargs=(pidx,)) as pool:
        for res in pool.imap_unordered(_do_sheet, jobs, chunksize=8):
            feats.update(res)
            done += 1
            if done % 500 == 0:
                el = time.time() - t0
                print(f"      시트 {done:,}/{len(jobs):,}  {el:.0f}s  "
                      f"(남은 ~{el/done*(len(jobs)-done):.0f}s)", flush=True)

    print("[분류] 그룹별 판정 …", flush=True)
    # 그룹 안에 '주석이 딴 도면에 얹힌' 소수 멤버가 섞여 있을 수 있다(원본 AI-Hub 오류).
    # 그런 한 장 때문에 그룹 전체를 불량으로 몰면 안 되므로, **중앙값 격자**를 기준으로
    # 이탈 멤버를 따로 골라내고 나머지로 그룹을 판정한다(중앙값은 소수 이상치에 강함).
    cls = {"identical": 0, "noise": 0, "annot": 0, "other": 0, "nodata": 0}
    detail, others, bad_members = {}, [], []
    for s, r in groups.items():
        fs = [(m, feats.get(m)) for m in r["members"]]
        ok = [(m, f) for m, f in fs if f]
        if len(ok) < 2:
            cls["nodata"] += 1
            detail[s] = "nodata"
            continue
        shp = collections.Counter(tuple(f["s"]) for _m, f in ok).most_common(1)[0][0]
        main = [(m, f) for m, f in ok if tuple(f["s"]) == shp]
        odd = [m for m, f in ok if tuple(f["s"]) != shp]        # 크기부터 다른 멤버
        if len(main) < 2:
            cls["other"] += 1
            detail[s] = "other"
            others.append([s, r["n"], "크기제각각"])
            continue
        gs = np.stack([np.asarray(f["g"], dtype=np.float32) for _m, f in main])
        med = np.median(gs, axis=0)
        base = max(1.0, float(med.sum()))
        dev = np.abs(gs - med).sum(axis=1) / base
        keep = [main[i] for i in range(len(main)) if dev[i] <= a.outlier]
        odd += [main[i][0] for i in range(len(main)) if dev[i] > a.outlier]
        for m in odd:
            bad_members.append(m)
        if len(keep) < 2:
            cls["other"] += 1
            detail[s] = "other"
            others.append([s, r["n"], "합의 없음"])
            continue
        if len({f["e"] for _m, f in keep}) == 1:
            cls["identical"] += 1
            detail[s] = "identical"
        elif len({f["i"] for _m, f in keep}) == 1:
            cls["noise"] += 1
            detail[s] = "noise"
        else:
            kg = np.stack([np.asarray(f["g"], dtype=np.float32) for _m, f in keep])
            worst = float(np.abs(kg - med).sum(axis=1).max()) / base
            if worst <= a.tol:
                cls["annot"] += 1
                detail[s] = "annot"
            else:
                cls["other"] += 1
                detail[s] = "other"
                others.append([s, r["n"], round(worst * 100, 2)])

    tot = sum(cls.values()) - cls["nodata"]
    same = cls["identical"] + cls["noise"] + cls["annot"]
    print()
    print(f"판정 가능한 그룹 {tot:,}개 (사본 2장 이상)")
    print(f"  ① 픽셀 완전 동일        : {cls['identical']:,}")
    print(f"  ② 렌더 잡음만           : {cls['noise']:,}")
    print(f"  ③ 주석/라벨만 차이(≤{a.tol*100:.0f}%) : {cls['annot']:,}")
    print(f"  ④ 그 외(사람이 봐야 함)  : {cls['other']:,}")
    print(f"  · 원본 PNG 없어 판정불가 : {cls['nodata']:,}")
    print(f"  → 같은 도면 비율 : {100*same/max(1,tot):.2f}%")
    print(f"  ⚠ 주석이 딴 도면에 얹힌 것으로 보이는 개별 도면 : {len(bad_members):,}장"
          f"  (원본 AI-Hub 오류 후보 — 보정·전파에서 빼야 함)")
    others.sort(key=lambda x: -(x[1] if isinstance(x[1], int) else 0))
    json.dump({"built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "ink_threshold": INK_T, "grid": GRID, "tol": a.tol,
               "counts": cls, "same_ratio": round(100*same/max(1, tot), 2),
               "outlier_tol": a.outlier, "bad_members": sorted(bad_members),
               "verdict": detail, "others": others[:500]},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"  → {OUT}  ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
