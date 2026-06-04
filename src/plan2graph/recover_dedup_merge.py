"""dedup이 버린 상보 라벨을 병합해 dual 그래프를 복구한다.

AI-Hub는 **동일한 PNG 한 장을 라벨 종류(OBJ/OCR/SPA/STR)마다 다른 키로 중복 배포**한다.
옛 빌드는 SPA·STR을 *키*로 짝지었기에, 키가 다른 이 경우엔 짝을 못 찾아 변환이 안 됐고
(대표로 남은 OBJ 사본은 방·구조 라벨이 없어) convert_failed로 박혔다. 실제론 같은 그림의
SPA(방)·STR(벽) 라벨이 둘 다 존재하므로, **지문(byte-동일)으로 재그룹해 라벨을 합치면**
정상 dual 변환이 된다. 이 스크립트는 그렇게 누락된 그래프만 staging/aihub/graphs에 추가한다.

원본 zip·V2V 산출물·기존 그래프는 건드리지 않는다(파생물 추가 생성만).
그래프명 = {HOUSE}_FP_{CRC8}_{size}_u{i} (3번째 _파트=지문 CRC8) → build_aihub가
재실행 시 became_graph=True 로 인식해 자동으로 use·dual 로 회계.

CLI: python -m plan2graph.recover_dedup_merge [--limit N] [--at "<ISO>"] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from plan2graph import build_dataset as bd      # noqa: E402
from plan2graph import build_aihub as ba        # noqa: E402


def combined_label_index() -> dict:
    """(label, key) -> (zip_path, entry), Training∪Validation 합본."""
    idx: dict = {}
    for sp in ("Training", "Validation"):
        for k, v in bd.build_label_index(sp).items():
            idx.setdefault(k, v)
    return idx


def find_targets():
    """복구 대상: category=dual, 그래프 없음(CRC8 미존재), SPA+STR 라벨 키 둘 다 확보.
    반환: [(fingerprint, house, {label: (zip,entry)})...] + 진단 카운트."""
    sig, entries = ba.scan_sources()
    gidx, _ = ba.graph_index()
    lblidx = combined_label_index()

    fp_labels: dict = defaultdict(dict)   # fp -> {label: key}  (그룹 내 각 라벨 대표 키)
    fp_house: dict = {}
    for s, label, key, house in entries:
        if label:
            fp_labels[s].setdefault(label, key)
        if house and s not in fp_house:
            fp_house[s] = house

    targets = []
    diag = defaultdict(int)
    for s, d in sig.items():
        if ba.category_of(d) != "dual":
            continue
        diag["dual_total"] += 1
        if gidx.get(s.split("_")[0]):
            diag["already_has_graph"] += 1
            continue
        labs = fp_labels.get(s, {})
        if "SPA" not in labs or "STR" not in labs:
            diag["missing_spa_or_str_key"] += 1
            continue
        ent = {}
        for L in ("SPA", "STR"):              # dual 표준 = 방(SPA)+벽(STR)
            k = labs.get(L)
            if k is not None and (L, k) in lblidx:
                ent[L] = lblidx[(L, k)]
        if "SPA" not in ent or "STR" not in ent:
            diag["label_json_missing"] += 1
            continue
        targets.append((s, fp_house.get(s) or d.get("house") or "APT", ent))
    return targets, dict(diag)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="표본 시험용 상한")
    ap.add_argument("--at", default="", help="보정 기록 타임스탬프(ISO)")
    ap.add_argument("--dry-run", action="store_true", help="쓰지 않고 결과만 집계")
    a = ap.parse_args()

    print("1) 복구 대상 탐색...", flush=True)
    targets, diag = find_targets()
    print("   진단:", diag)
    print("   병합변환 대상 지문(dual·그래프없음·SPA+STR보유):", len(targets))
    if a.limit:
        targets = targets[:a.limit]
        print("   (표본 시험: %d개로 제한)" % len(targets))

    ba.GRAPHS_OUT.mkdir(parents=True, exist_ok=True)
    ok_fp = 0
    units = 0
    fail = defaultdict(int)
    samples = []
    for s, house, ent in targets:
        sheet_id = f"{house}_{ba.FP}_{s}"     # uid = {house}_FP_{crc8}_{size}_u{i}
        res = bd.process_pair(sheet_id, house, ent, provenance="aihub_label_merged")
        if res["status"] != "ok" or not res.get("records"):
            fail["%s:%s" % (res["status"], (res.get("reason") or "")[:30])] += 1
            continue
        wrote = 0
        for r in res["records"]:
            cl = bd.classify_unit(r["record"])
            if cl[0] != "complete":           # 기존 staging과 동일 품질바(완벽)만 채택
                fail["gate:" + cl[1].split(":")[0]] += 1
                continue
            rec = r["record"]
            rec.setdefault("meta", {})
            rec["meta"]["disposition"] = "use"
            rec["meta"]["source"] = "aihub"
            rec["meta"]["provenance"] = {
                "origin": "dedup_label_merge",
                "corrected": {"from": "convert_failed",
                              "method": "fingerprint_label_union(SPA+STR)",
                              "at": a.at, "by": "recover_dedup_merge"}}
            gid = r["graph_id"]
            if not a.dry_run:
                (ba.GRAPHS_OUT / f"{gid}.json").write_text(
                    json.dumps(rec, ensure_ascii=False), encoding="utf-8")
            wrote += 1
            units += 1
            if len(samples) < 8:
                samples.append((gid, r["n_rooms"], r["n_doors"]))
        if wrote:
            ok_fp += 1

    print("2) 결과%s:" % (" (DRY-RUN)" if a.dry_run else ""))
    print("   복구 성공 지문: %d / %d" % (ok_fp, len(targets)))
    print("   생성 그래프(세대): %d" % units)
    print("   실패/탈락 사유:", dict(fail))
    if samples:
        print("   샘플(gid, 방수, 문수):")
        for g, nr, nd in samples:
            print("     %s  rooms=%s doors=%s" % (g, nr, nd))


if __name__ == "__main__":
    main()
