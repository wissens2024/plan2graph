"""AI-Hub 클린 통합 — v0∪v2 그래프 병합 + provenance 스탬프 + 원본 회계 manifest.

재변환·V2V 재실행 없이, 이미 만들어진 그래프(releases/v0 ∪ releases/v2)를 합쳐
staging/aihub/ 한 곳으로 통일한다. 원본(zip)·V2V 산출물·옛 폴더는 건드리지 않는다.

산출:
  staging/aihub/graphs/<graph_id>.json   v0∪v2 병합(graph_id 중복은 첫 채택), meta에
                                         disposition·provenance 스탬프
  staging/aihub/manifest.jsonl           받은 원천 도면 1장=1줄(처분·사유·became_graph·
                                         corrected). 줄 수 = 다운로드, 버킷 합 = 줄 수.

처분(상호배타, 도면1장=대표사유1칸):
  dual + 그래프      → use  · dual(직접변환)
  dual · 그래프없음   → fix  · convert_failed (둘 다 있는데 변환 실패)
  방만 + 그래프      → use  · v2v_str_recovered   (corrected: from spa_only, method v2v_str)
  방만 · 그래프없음   → fix  · spa_only_pending    (V2V 미적용/실패)
  구조만 + 그래프    → use  · v2v_spa_recovered   (corrected: from str_only, method v2v_spa)
  구조만 · 그래프없음 → fix  · str_only_pending
  비-FP             → excl · nonfp
  OBJ/OCR만         → excl · objocr
  중복 사본          → excl · duplicate (대표 외 추가 키)

CLI: python src/plan2graph/build_aihub.py --at "<ISO시각>"   (시각은 외부 주입; 미지정 시 빈값)
"""
from __future__ import annotations

import argparse
import json
import glob
import os
import sys
import shutil
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph.unpack import discover_zips, iter_zipinfos, parse_name  # noqa: E402

FP = config.TARGET_DRAWING_TYPE
SRC = "aihub"
OUT = config.DATA_DIR / "staging" / SRC
GRAPHS_OUT = OUT / "graphs"
V0 = config.release_dir("v0") / "graphs"
V2 = config.release_dir("v2") / "graphs"


def scan_sources():
    """원천 zip 전체 1패스 → (sig_info, entries).
    sig_info: 지문 -> {types,labels,house} (분류용, 고유 도면).
    entries : **받은 raw 원천 PNG를 있는 그대로** 한 건씩 [(sig,label,key,house)].
              len(entries) = 다운로드 원천 PNG 수(=전체). byte-identical 사본도 전부 포함."""
    sig: dict = defaultdict(lambda: {"types": set(), "labels": set(), "house": None})
    entries: list = []
    zips = [z for z in discover_zips() if z["content"] == "원천"]
    for z, info in iter_zipinfos(zips):
        if not info.filename.lower().endswith(".png"):
            continue
        m = parse_name(Path(info.filename).stem)
        s = "%08x_%d" % (info.CRC, info.file_size)
        if m:
            d = sig[s]
            d["types"].add(m["drawing"]); d["labels"].add(m["label"])
            if d["house"] is None:
                d["house"] = m.get("house")
            entries.append((s, m["label"], m["key"], m.get("house")))
        else:   # 파싱 실패도 raw 1건으로 회계(누락 없음)
            entries.append((s, None, Path(info.filename).stem, None))
    return sig, entries


def category_of(d: dict) -> str:
    if FP not in d["types"]:
        return "nonfp"
    L = d["labels"]
    if "SPA" in L and "STR" in L:
        return "dual"
    if "SPA" in L:
        return "spa_only"
    if "STR" in L:
        return "str_only"
    if "OBJ" in L or "OCR" in L:
        return "objocr"
    return "other"


def graph_index() -> tuple[dict, dict]:
    """변환된 그래프 → (fp -> [graph_id...]), (graph_id -> 경로). graph_id 중복은 첫 채택.
    통합본(staging/aihub/graphs)이 있으면 그걸, 없으면 옛 v0∪v2를 읽는다."""
    idx: dict = defaultdict(list)
    path: dict = {}
    bases = [GRAPHS_OUT] if GRAPHS_OUT.is_dir() else [V0, V2]
    for base in bases:
        for f in sorted(glob.glob(str(base) + "/*.json")):
            gid = os.path.basename(f)[:-5]
            if gid in path:
                continue
            path[gid] = f
            p = gid.split("_")
            if len(p) >= 3:
                idx[p[2]].append(gid)
    return idx, path


def merged_crc8s(gpath: dict) -> set:
    """중복라벨 병합으로 복구된 그래프의 CRC8 집합(provenance.origin=dedup_label_merge).
    같은 지문의 SPA/STR(다른 키)을 합쳐 만든 dual — manifest에 별도 사유로 추적."""
    out = set()
    for gid, f in gpath.items():
        try:
            meta = json.loads(Path(f).read_text(encoding="utf-8")).get("meta", {})
        except Exception:  # noqa: BLE001
            continue
        if meta.get("provenance", {}).get("origin") == "dedup_label_merge":
            p = gid.split("_")
            if len(p) >= 3:
                out.add(p[2])
    return out


def _disposition(cat: str, became: bool, at: str, merged: bool = False):
    """(cat, 그래프존재) → (disposition, reason, corrected). 상호배타 단일배정."""
    if cat == "nonfp":
        return "excl", "nonfp", None
    if cat == "objocr":
        return "excl", "objocr", None
    if cat == "dual":
        if became and merged:
            return ("use", "dual_dedup_merge",
                    {"from": "convert_failed", "method": "fingerprint_label_union(SPA+STR)",
                     "at": at, "by": "recover_dedup_merge"})
        return ("use", "dual", None) if became else ("fix", "convert_failed", None)
    if cat == "spa_only":
        return (("use", "v2v_str_recovered",
                 {"from": "spa_only", "method": "v2v_str", "at": at, "by": "v2v"})
                if became else ("fix", "spa_only_pending", None))
    if cat == "str_only":
        return (("use", "v2v_spa_recovered",
                 {"from": "str_only", "method": "v2v_spa", "at": at, "by": "v2v"})
                if became else ("fix", "str_only_pending", None))
    return "fix", "other", None


def _origin_for_graph(cat: str, at: str) -> dict:
    if cat == "spa_only":
        return {"origin": "v2v", "corrected": {"from": "spa_only", "method": "v2v_str", "at": at, "by": "v2v"}}
    if cat == "str_only":
        return {"origin": "v2v", "corrected": {"from": "str_only", "method": "v2v_spa", "at": at, "by": "v2v"}}
    return {"origin": "direct"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", default="", help="보정 기록 타임스탬프(ISO). 미지정 시 빈값.")
    ap.add_argument("--write-graphs", action="store_true",
                    help="v0∪v2 그래프를 staging/aihub/graphs로 복사+스탬프(대량 쓰기).")
    a = ap.parse_args()
    at = a.at

    print("1) 원천 zip 스캔(raw 그대로)...", flush=True)
    sig, entries = scan_sources()
    print("   raw 원천 PNG(=전체):", len(entries), "· 고유 도면(지문):", len(sig))
    print("2) v0∪v2 그래프 인덱스...", flush=True)
    gidx, gpath = graph_index()
    merged = merged_crc8s(gpath)
    print("   그래프 graph_id:", len(gpath), "· 그래프 보유 fp:", len(gidx),
          "· 중복라벨복구 fp:", len(merged))

    # ── 그래프 병합(provenance 스탬프) ──
    if a.write_graphs:
        GRAPHS_OUT.mkdir(parents=True, exist_ok=True)
        cr_cat = {fp.split("_")[0]: category_of(d) for fp, d in sig.items()}  # CRC8 -> cat
        n = 0
        for gid, src_path in gpath.items():
            cr = gid.split("_")[2] if len(gid.split("_")) >= 3 else None
            cat = cr_cat.get(cr, "dual")
            try:
                rec = json.loads(Path(src_path).read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            rec.setdefault("meta", {})
            rec["meta"]["disposition"] = "use"
            rec["meta"]["provenance"] = _origin_for_graph(cat, at)
            rec["meta"]["source"] = "aihub"
            (GRAPHS_OUT / f"{gid}.json").write_text(
                json.dumps(rec, ensure_ascii=False), encoding="utf-8")
            n += 1
        print(f"   그래프 병합 완료: {n} → {GRAPHS_OUT}")

    # ── manifest.jsonl : raw 원천 PNG 1건 = 1줄 (전체=raw, 사본도 전부 기록) ──
    OUT.mkdir(parents=True, exist_ok=True)
    mpath = OUT / "manifest.jsonl"
    bucket = defaultdict(int)
    lines = 0
    repped = set()   # 지문별 첫 1건만 대표(분류), 나머지는 중복 제외
    with mpath.open("w", encoding="utf-8") as fo:
        for s, label, key, house in entries:
            if s not in repped and s in sig:
                repped.add(s)
                cat = category_of(sig[s])
                crc8 = s.split("_")[0]
                gids = gidx.get(crc8, [])                 # 그래프 지문 = CRC8
                disp, reason, corrected = _disposition(cat, len(gids) > 0, at, crc8 in merged)
                row = {"drawing_id": key, "fingerprint": s, "source": "aihub", "house": house,
                       "disposition": disp, "reason": reason, "became_graph": len(gids) > 0,
                       "graph_ids": gids, "corrected": corrected, "dup_of": None}
            else:   # byte-identical 사본(라벨/키 중복) 또는 파싱실패 → 제외, raw에 그대로 남김
                rs = "duplicate" if s in sig else "parse_fail"
                row = {"drawing_id": f"{key}@{label}" if label else str(key),
                       "fingerprint": s, "source": "aihub", "house": house,
                       "disposition": "excl", "reason": rs, "became_graph": False,
                       "graph_ids": [], "corrected": None, "dup_of": (s if rs == "duplicate" else None)}
            fo.write(json.dumps(row, ensure_ascii=False) + "\n")
            bucket[(row["disposition"], row["reason"])] += 1
            lines += 1
    print(f"3) manifest 작성: {mpath} · {lines:,}줄 (=raw 원천 PNG)")

    # ── 검증 ──
    print("4) 검증 — 버킷별(처분·사유):")
    tot = 0
    for (disp, reason), n in sorted(bucket.items(), key=lambda x: (-x[1])):
        print("   %-5s %-20s %7d" % (disp, reason, n))
        tot += n
    use = sum(n for (dp, _), n in bucket.items() if dp == "use")
    fix = sum(n for (dp, _), n in bucket.items() if dp == "fix")
    exc = sum(n for (dp, _), n in bucket.items() if dp == "excl")
    print(f"   ── 합 {tot:,} (use {use:,} + fix {fix:,} + excl {exc:,}) · manifest 줄수 {lines:,} · 일치={tot==lines}")


if __name__ == "__main__":
    main()
