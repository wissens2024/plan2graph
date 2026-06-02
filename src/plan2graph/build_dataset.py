"""Task 2-1 — 전체 일괄 변환 + 품질 필터.

linked_spa_str_{split}.json(원천 PNG 지문으로 묶인 SPA+STR 쌍)을 입력으로,
각 도면의 라벨 JSON을 라벨 ZIP에서 직접 읽어(압축 전체 해제 없이) 파이프라인
(coco→geometry→topology→rules→schema)을 적용해 표준 그래프 레코드를 만든다.

설계:
- 라벨 ZIP의 중앙 디렉터리로 (label, key)→(zip, entry) 인덱스를 1회 구축.
- 각 쌍마다 필요한 SPA/STR(+OBJ/OCR) 엔트리만 zf.read로 추출 → 메모리에서 파싱.
- 실패/이상치는 사유별로 분류·격리(quality_report.csv). 임계 이하 품질은 제외 표시.
- joblib 병렬(--jobs). 워커는 (zip,entry) 경로만 받아 zip을 자체로 열어 읽음.

CLI:
  python src/plan2graph/build_dataset.py --split Training --limit 100   # 파일럿
  python src/plan2graph/build_dataset.py --split Training --jobs 8       # 전체
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph.unpack import discover_zips, parse_name  # noqa: E402
from plan2graph.coco import load_coco_bytes  # noqa: E402
from plan2graph.geometry import assemble_drawing  # noqa: E402
from plan2graph.topology import build_graph, iter_units  # noqa: E402
from plan2graph.rules import validate  # noqa: E402
from plan2graph.schema import serialize  # noqa: E402

# 품질 임계 (튜닝 — NOTES.md 기록)
MIN_ROOMS = 2                 # 방 2개 미만은 그래프 의미 없음
MAX_UNRESOLVED_RATIO = 0.20   # 미해소 문 비율 20% 초과 = 추론 신뢰도 미달


def classify_unit(record: dict) -> tuple[str, str]:
    """세대 레코드 → (status, reason).
    status: 'complete'(채택) / 'quarantine'(제외하고 목록화).
    완벽 기준: 필수 5요소(현관·거실·침실·주방·화장실) 모두 보유 + 방수 범위 + 무결성.
    """
    program = record["constraints"]["program"]
    n_rooms = record["meta"]["n_rooms"]
    missing = [c.replace("공간_", "") for c in config.ESSENTIAL_ROOM_CLASSES
               if program.get(c.replace("공간_", ""), 0) < 1]
    if missing:
        return "quarantine", "missing_essential:" + ",".join(missing)
    if n_rooms < config.ACCEPT_MIN_ROOMS:
        return "quarantine", f"too_few_rooms:{n_rooms}"
    if n_rooms > config.ACCEPT_MAX_ROOMS:
        return "quarantine", f"too_many_rooms:{n_rooms}"
    if not record["validation"].get("passed"):
        return "quarantine", "integrity_failed"
    return "complete", ""


def build_label_index(split: str) -> dict[tuple[str, str], tuple[str, str]]:
    """라벨 ZIP에서 (label, key) → (zip_path, entry_name) 인덱스."""
    zips = [z for z in discover_zips()
            if z["content"] == "라벨" and z["split"] == split]
    index: dict[tuple[str, str], tuple[str, str]] = {}
    for z in zips:
        try:
            with zipfile.ZipFile(z["path"]) as zf:
                names = zf.namelist()
        except zipfile.BadZipFile:
            continue
        for name in names:
            if name.endswith("/"):
                continue
            meta = parse_name(Path(name).stem)
            if meta is None or meta["drawing"] != config.TARGET_DRAWING_TYPE:
                continue
            index[(meta["label"], meta["key"])] = (str(z["path"]), name)
    return index


# ── 워커: zip 캐시(프로세스별) ────────────────────────────────────────────────
_ZIP_CACHE: dict[str, zipfile.ZipFile] = {}


def _read_entry(zip_path: str, entry: str) -> bytes:
    zf = _ZIP_CACHE.get(zip_path)
    if zf is None:
        zf = zipfile.ZipFile(zip_path)
        _ZIP_CACHE[zip_path] = zf
    return zf.read(entry)


def process_pair(sheet_id: str, house: str,
                 entries: dict[str, tuple[str, str]]) -> dict:
    """한 시트(라벨 엔트리 묶음) → 세대별 표준 레코드 리스트 + 상태.
    ※ AI-Hub FP 시트엔 여러 세대가 타일돼 있다 → iter_units로 분해, 세대당 1레코드.
    entries: {'SPA': (zip,entry), 'STR': (zip,entry), ...}
    반환: {status, reason, sheet_id, records[], ...}
    """
    try:
        docs = []
        for label in ("SPA", "STR", "OBJ", "OCR"):
            if label in entries:
                zip_path, entry = entries[label]
                data = _read_entry(zip_path, entry)
                docs.append(load_coco_bytes(data, source=entry))
        dr = assemble_drawing(docs)
        if len(dr.rooms) < MIN_ROOMS:
            return {"status": "rejected", "reason": "too_few_rooms",
                    "sheet_id": sheet_id, "n_rooms": len(dr.rooms), "records": []}
        G = build_graph(dr, graph_id=sheet_id)
        G.graph["house_type"] = house
        units, noise = iter_units(G, min_rooms=MIN_ROOMS)
        if not units:
            return {"status": "rejected", "reason": "no_unit_with_entrance",
                    "sheet_id": sheet_id, "n_rooms": len(dr.rooms),
                    "n_noise": len(noise), "records": []}

        records = []
        for i, U in enumerate(units):
            uid = f"{sheet_id}_u{i}"
            U.graph["graph_id"] = uid
            n_doors = U.graph.get("n_doors", 0)
            val = validate(U)
            rec = serialize(U, graph_id=uid, house_type=house,
                            width=dr.width, height=dr.height, validation=val)
            records.append({
                "graph_id": uid, "record": rec,
                "integrity_passed": val["passed"],
                "n_rooms": U.graph.get("n_rooms"), "n_doors": n_doors,
            })
        return {
            "status": "ok", "reason": "", "sheet_id": sheet_id,
            "records": records, "n_units": len(units),
            "n_noise_frags": len(noise), "broken_polys": dr.broken_count,
        }
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "reason": f"{type(e).__name__}: {e}",
                "sheet_id": sheet_id, "records": []}


def build(splits, limit: int | None, jobs: int, out_dir: Path) -> None:
    if isinstance(splits, str):
        splits = [splits]
    # 여러 AI-Hub split(Training+Validation)을 '하나의 풀'로 통합. 지문(gid)으로 중복 제거.
    tasks = []
    seen_gid = set()
    skipped_missing = 0
    for split in splits:
        link_path = config.INTERIM_DIR / f"linked_spa_str_{split.lower()}.json"
        if not link_path.exists():
            print(f"  [건너뜀] 연결 인덱스 없음: {split}")
            continue
        pairs = json.loads(link_path.read_text(encoding="utf-8"))["pairs"]
        label_index = build_label_index(split)
        print(f"{split}: 쌍 {len(pairs):,} · 라벨엔트리 {len(label_index):,}")
        for fp, labels in pairs.items():
            entries: dict[str, tuple[str, str]] = {}
            house = None
            for label, key in labels.items():
                ie = label_index.get((label, key))
                if ie is None:
                    continue
                entries[label] = ie
                if house is None:
                    m = parse_name(Path(ie[1]).stem)
                    house = m["house"] if m else None
            if "SPA" not in entries or "STR" not in entries:
                skipped_missing += 1
                continue
            gid = f"{house}_FP_{fp}"
            if gid in seen_gid:        # 두 split에 같은 도면(지문) → 중복 제거
                continue
            seen_gid.add(gid)
            tasks.append((gid, house or "UNK", entries))
            if limit and len(tasks) >= limit:
                break
    print(f"  통합 처리 대상 {len(tasks):,}개 (split={'+'.join(splits)}, "
          f"엔트리 결손 스킵 {skipped_missing:,})")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "graphs").mkdir(exist_ok=True)

    # 실행
    if jobs > 1:
        from joblib import Parallel, delayed
        results = Parallel(n_jobs=jobs, prefer="processes")(
            delayed(process_pair)(gid, house, entries)
            for gid, house, entries in tasks)
    else:
        results = [process_pair(gid, house, entries)
                   for gid, house, entries in tasks]

    # 집계: 완벽한 세대만 채택, 나머지는 격리 목록(quarantine)에 기록
    n_sheet_ok = n_sheet_err = 0
    n_units = n_accept = 0
    q_reasons = defaultdict(int)
    accepted_rows = []
    quarantine_rows = []

    def _prog_str(rec):
        return ";".join(f"{k}:{v}" for k, v in rec["constraints"]["program"].items())

    for r in results:
        if r["status"] == "ok":
            n_sheet_ok += 1
            for u in r["records"]:
                n_units += 1
                status, reason = classify_unit(u["record"])
                if status == "complete":
                    n_accept += 1
                    gp = out_dir / "graphs" / f"{u['graph_id']}.json"
                    gp.write_text(json.dumps(u["record"], ensure_ascii=False),
                                  encoding="utf-8")
                    accepted_rows.append({
                        "graph_id": u["graph_id"], "sheet_id": r["sheet_id"],
                        "n_rooms": u.get("n_rooms", ""),
                        "n_doors": u.get("n_doors", ""),
                        "program": _prog_str(u["record"])})
                else:
                    q_reasons[reason.split(":")[0]] += 1
                    quarantine_rows.append({
                        "graph_id": u["graph_id"], "sheet_id": r["sheet_id"],
                        "reason": reason, "n_rooms": u.get("n_rooms", ""),
                        "program": _prog_str(u["record"])})
        elif r["status"] == "rejected":
            q_reasons[r["reason"]] += 1
            quarantine_rows.append({
                "graph_id": "", "sheet_id": r["sheet_id"],
                "reason": r["reason"], "n_rooms": r.get("n_rooms", ""),
                "program": ""})
        else:
            n_sheet_err += 1
            q_reasons["error"] += 1
            quarantine_rows.append({
                "graph_id": "", "sheet_id": r["sheet_id"],
                "reason": "error:" + r["reason"][:50], "n_rooms": "",
                "program": ""})

    # 채택 목록 + 격리 목록 저장
    acc_path = out_dir / "accepted.csv"
    with open(acc_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "graph_id", "sheet_id", "n_rooms", "n_doors", "program"])
        w.writeheader()
        w.writerows(accepted_rows)
    q_path = out_dir / "quarantine.csv"
    with open(q_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "graph_id", "sheet_id", "reason", "n_rooms", "program"])
        w.writeheader()
        w.writerows(quarantine_rows)

    print(f"\n=== 변환 결과 ({split}) ===")
    print(f"  시트 성공 {n_sheet_ok:,}  오류 {n_sheet_err:,}")
    print(f"  세대 추출 {n_units:,}  →  ★채택(완벽) {n_accept:,}  "
          f"격리 {len(quarantine_rows):,}")
    print(f"  채택률(세대 대비): {n_accept / max(n_units, 1) * 100:.1f}%")
    print("  격리 사유별:")
    for k in sorted(q_reasons, key=lambda x: -q_reasons[x]):
        print(f"    {k:28} {q_reasons[k]:>6,}")
    print(f"  채택 그래프: {out_dir / 'graphs'}  ({n_accept:,}개)")
    print(f"  채택 목록: {acc_path}")
    print(f"  격리 목록(후일 검토): {q_path}")


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Task 2-1 전체 변환·품질필터")
    ap.add_argument("--split", default="all",
                    help="Training | Validation | all(통합, 기본)")
    ap.add_argument("--limit", type=int, default=None, help="파일럿: 앞 N개만")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--out", default=str(config.PROCESSED_DIR))
    args = ap.parse_args(argv)
    splits = ["Training", "Validation"] if args.split == "all" else [args.split]
    build(splits, args.limit, args.jobs, Path(args.out))


if __name__ == "__main__":
    main()
