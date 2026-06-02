"""Task 1-1 — 압축 해제 & 인벤토리.

설계 핵심:
- 학습 원천 ZIP은 각 4.5GB(총 ~38GB)다. 전체를 풀지 않는다.
  ZIP '중앙 디렉터리'만 읽으면(zipfile.namelist) 압축 해제 없이 전체 인벤토리를 만들 수 있다.
- 게이트(1장→20장→100장)에 필요한 도면만 `extract`로 선택 추출한다.

CLI:
  python src/plan2graph/unpack.py inventory
      → 모든 ZIP을 namelist로 스캔해 data/interim/inventory.csv + 정합 리포트 생성
  python src/plan2graph/unpack.py extract --split Validation --key 350195713
      → 해당 도면키의 라벨(JSON)·원천(PNG)을 data/raw로 추출
  python src/plan2graph/unpack.py sample --n 1 --split Validation --house APT
      → 평면도 중 SPA+STR 라벨이 모두 있는 완전한 도면 N개를 추출
"""
from __future__ import annotations

import argparse
import csv
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

# ── config 부트스트랩 (config.py는 프로젝트 루트에 있음) ──────────────────────
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import config  # noqa: E402


SPLITS = ("Training", "Validation")
SPLIT_PREFIX = {"Training": ("TS", "TL"), "Validation": ("VS", "VL")}  # 원천/라벨
CONTENT_DIR = {"원천": "01.원천데이터", "라벨": "02.라벨링데이터"}


def parse_name(stem: str) -> dict | None:
    """'APT_FP_SPA_350195713' → {house, drawing, label, key}. 형식 불일치 시 None."""
    parts = stem.split("_")
    if len(parts) != 4:
        return None
    house, drawing, label, key = parts
    if house not in config.HOUSE_TYPES or drawing not in config.DRAWING_TYPES:
        return None
    if label not in config.LABEL_TYPES or not (key.isdigit() and len(key) == 9):
        return None
    return {"house": house, "drawing": drawing, "label": label, "key": key}


def discover_zips(root: Path = config.RAW_SOURCE_ROOT) -> list[dict]:
    """원본 트리에서 ZIP을 찾아 (split, content_kind, path) 메타로 반환."""
    found = []
    for split in SPLITS:
        for kind, subdir in CONTENT_DIR.items():
            d = root / split / subdir
            if not d.is_dir():
                continue
            for zp in sorted(d.glob("*.zip")):
                found.append({"split": split, "content": kind, "path": zp})
    return found


def iter_entries(zips: list[dict]):
    """각 ZIP의 namelist를 순회하며 (메타, entry_name)을 yield. 압축 해제 안 함."""
    for z in zips:
        try:
            with zipfile.ZipFile(z["path"]) as zf:
                names = zf.namelist()
        except zipfile.BadZipFile:
            print(f"  [경고] 손상 ZIP 건너뜀: {z['path'].name}", file=sys.stderr)
            continue
        for name in names:
            if name.endswith("/"):
                continue  # 디렉터리 엔트리
            yield z, name


def iter_zipinfos(zips: list[dict]):
    """각 ZIP의 infolist를 순회하며 (메타, ZipInfo)를 yield.
    ZipInfo.CRC·file_size는 중앙 디렉터리에 저장돼 있어 압축 해제 없이 읽힌다."""
    for z in zips:
        try:
            with zipfile.ZipFile(z["path"]) as zf:
                infos = zf.infolist()
        except zipfile.BadZipFile:
            print(f"  [경고] 손상 ZIP 건너뜀: {z['path'].name}", file=sys.stderr)
            continue
        for info in infos:
            if info.is_dir():
                continue
            yield z, info


def build_inventory(root: Path = config.RAW_SOURCE_ROOT) -> Path:
    """모든 ZIP을 스캔해 inventory.csv + 정합 리포트를 만든다."""
    zips = discover_zips(root)
    if not zips:
        raise SystemExit(f"ZIP을 찾지 못했습니다: {root}")
    print(f"ZIP {len(zips)}개 스캔 중...")

    rows: list[dict] = []
    bad_names: list[tuple[str, str]] = []  # (zip, entry) 형식 불일치
    for z, name in iter_entries(zips):
        stem = Path(name).stem  # 선행 '/'·확장자 제거
        ext = Path(name).suffix.lower().lstrip(".")
        meta = parse_name(stem)
        if meta is None:
            bad_names.append((z["path"].name, name))
            continue
        rows.append({
            "key": meta["key"],
            "house": meta["house"],
            "drawing": meta["drawing"],
            "label": meta["label"],
            "ext": ext,
            "content": z["content"],          # 원천 / 라벨
            "split": z["split"],              # Training / Validation
            "zip": z["path"].name,
            "entry": name,
        })

    config.INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.INVENTORY_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "key", "house", "drawing", "label", "ext", "content", "split", "zip", "entry"])
        w.writeheader()
        w.writerows(rows)
    print(f"인벤토리 작성: {config.INVENTORY_CSV}  (엔트리 {len(rows):,})")

    _report(rows, bad_names)
    return config.INVENTORY_CSV


def _report(rows: list[dict], bad_names: list[tuple[str, str]]) -> None:
    """도면종류 분포 + 평면도 라벨 정합(키 결손) 리포트."""
    # 도면종류 × split 분포 (라벨 SPA 기준 = 도면 1장당 1개)
    by_drawing = defaultdict(int)
    for r in rows:
        if r["content"] == "라벨" and r["label"] == "SPA":
            by_drawing[(r["split"], r["house"], r["drawing"])] += 1

    print("\n=== 도면종류 분포 (라벨 SPA 기준, =도면 장수) ===")
    for k in sorted(by_drawing):
        print(f"  {k[0]:11} {k[1]} {k[2]} : {by_drawing[k]:>6,}")

    # 평면도 한정: 도면키별 라벨 커버리지
    fp = config.TARGET_DRAWING_TYPE
    label_cov: dict[str, set] = defaultdict(set)   # key -> {label종류}
    has_image: set[str] = set()
    for r in rows:
        if r["drawing"] != fp:
            continue
        if r["content"] == "라벨":
            label_cov[r["key"]].add(r["label"])
        elif r["content"] == "원천":
            has_image.add(r["key"])

    keys = set(label_cov) | has_image
    full = sum(1 for k in keys if {"SPA", "STR"} <= label_cov.get(k, set()))
    no_spa = sum(1 for k in keys if "SPA" not in label_cov.get(k, set()))
    no_str = sum(1 for k in keys if "STR" not in label_cov.get(k, set()))
    no_img = sum(1 for k in keys if k not in has_image)

    print(f"\n=== 평면도(FP) 라벨 정합 ===")
    print(f"  도면키 총 {len(keys):,}개")
    print(f"  SPA+STR 모두 보유(그래프화 가능): {full:,}")
    print(f"  SPA 결손: {no_spa:,}   STR 결손: {no_str:,}   PNG 결손: {no_img:,}")
    if bad_names:
        print(f"\n  [형식 불일치 엔트리] {len(bad_names)}건 (예: {bad_names[0][1]})")

    # 결손 도면 목록 저장
    miss_path = config.INTERIM_DIR / "fp_label_coverage.csv"
    with open(miss_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["key", "labels", "has_image", "graphable"])
        for k in sorted(keys):
            labs = label_cov.get(k, set())
            w.writerow([k, "|".join(sorted(labs)), k in has_image,
                        {"SPA", "STR"} <= labs])
    print(f"  커버리지 상세: {miss_path}")


# ─────────────────────────────────────────────────────────────────────────────
# 라벨종류 연결 (PNG 내용 지문)
# ─────────────────────────────────────────────────────────────────────────────
def fingerprint_label_map(root: Path = config.RAW_SOURCE_ROOT,
                          split: str = "Training") -> dict:
    """원천 PNG 지문(CRC,size) → {라벨종류: 9자리키} (FP만). 모든 라벨조합 포함.
    v2v_infer가 '단일 라벨 도면'(SPA만/STR만)을 찾는 데 사용."""
    zips = [z for z in discover_zips(root)
            if z["content"] == "원천" and z["split"] == split]
    fp = config.TARGET_DRAWING_TYPE
    groups: dict[tuple[int, int], dict[str, str]] = defaultdict(dict)
    for z, info in iter_zipinfos(zips):
        meta = parse_name(Path(info.filename).stem)
        if meta is None or meta["drawing"] != fp:
            continue
        groups[(info.CRC, info.file_size)][meta["label"]] = meta["key"]
    return {f"{crc:08x}_{size}": d for (crc, size), d in groups.items()}


def build_linkage(root: Path = config.RAW_SOURCE_ROOT,
                  split: str = "Training") -> Path:
    """원천 PNG를 CRC32+크기 지문으로 그룹핑해 '같은 도면'을 묶고,
    각 도면 그룹이 어떤 라벨종류(SPA/STR/OBJ/OCR)를 함께 갖는지 집계한다.

    핵심: 9자리 ID는 라벨종류 간 공유 키가 아니다. 같은 도면의 PNG가
    라벨종류별로 byte-identical하게 들어있으므로 CRC32+file_size가 곧 연결고리다.
    (CRC는 32bit라 충돌 가능 → file_size까지 동일해야 같은 그룹. 표본 SHA로 별도 검증 가능.)
    """
    import json
    zips = [z for z in discover_zips(root)
            if z["content"] == "원천" and z["split"] == split]
    if not zips:
        raise SystemExit(f"원천 ZIP을 찾지 못했습니다: {root}/{split}")
    print(f"{split} 원천 ZIP {len(zips)}개에서 PNG 지문 수집 중...")

    fp = config.TARGET_DRAWING_TYPE
    # 지문 (crc, size) -> {label: key}  (FP만)
    groups: dict[tuple[int, int], dict[str, str]] = defaultdict(dict)
    n_png = 0
    for z, info in iter_zipinfos(zips):
        stem = Path(info.filename).stem
        meta = parse_name(stem)
        if meta is None or meta["drawing"] != fp:
            continue
        n_png += 1
        sig = (info.CRC, info.file_size)
        groups[sig][meta["label"]] = meta["key"]

    # 집계
    n_groups = len(groups)
    both = {sig: d for sig, d in groups.items() if "SPA" in d and "STR" in d}
    only_spa = sum(1 for d in groups.values() if "SPA" in d and "STR" not in d)
    only_str = sum(1 for d in groups.values() if "STR" in d and "SPA" not in d)
    print(f"\n=== {split} FP PNG 지문 연결 결과 ===")
    print(f"  PNG 엔트리 {n_png:,}개 → 고유 도면(지문) {n_groups:,}개")
    print(f"  SPA+STR 동시 보유(그래프화 가능): {len(both):,}  "
          f"({len(both) / max(n_groups, 1) * 100:.1f}%)")
    print(f"  SPA만: {only_spa:,}   STR만: {only_str:,}")

    # 라벨조합 분포
    combo = defaultdict(int)
    for d in groups.values():
        combo["+".join(sorted(d))] += 1
    print("  라벨조합 분포:")
    for k in sorted(combo, key=lambda x: -combo[x]):
        print(f"    {k:20} {combo[k]:>7,}")

    out = config.INTERIM_DIR / f"linked_spa_str_{split.lower()}.json"
    config.INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "split": split, "drawing": fp,
        "n_png": n_png, "n_drawings": n_groups,
        "n_graphable": len(both),
        "graphable_ratio": round(len(both) / max(n_groups, 1), 4),
        "combo_distribution": dict(combo),
        # 그래프화 가능 쌍: 지문 문자열 -> {SPA: key, STR: key, ...}
        "pairs": {f"{crc:08x}_{size}": d for (crc, size), d in both.items()},
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n연결 인덱스 저장: {out}")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 선택 추출
# ─────────────────────────────────────────────────────────────────────────────
def _extract_matching(predicate, dest: Path) -> list[Path]:
    """predicate(meta, content, split, ext) → True인 엔트리를 dest로 추출."""
    dest.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for z, name in iter_entries(discover_zips()):
        stem, ext = Path(name).stem, Path(name).suffix.lower().lstrip(".")
        meta = parse_name(stem)
        if meta is None or not predicate(meta, z["content"], z["split"], ext):
            continue
        with zipfile.ZipFile(z["path"]) as zf:
            data = zf.read(name)
        target = dest / (Path(name).name)  # 평탄화
        target.write_bytes(data)
        out.append(target)
    return out


def extract_key(key: str, split: str | None, dest: Path) -> list[Path]:
    pred = lambda m, c, s, e: m["key"] == key and (split is None or s == split)
    files = _extract_matching(pred, dest)
    print(f"키 {key}: {len(files)}개 추출 → {dest}")
    for p in files:
        print(f"  {p.name}")
    return files


def sample_complete_fp(n: int, split: str, house: str | None, dest: Path) -> list[str]:
    """SPA+STR 라벨과 PNG가 모두 있는 평면도 도면키 N개를 골라 추출."""
    if not config.INVENTORY_CSV.exists():
        build_inventory()
    # 커버리지 CSV에서 graphable 키 선택
    cov = config.INTERIM_DIR / "fp_label_coverage.csv"
    graphable = []
    with open(cov, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["graphable"] == "True" and r["has_image"] == "True":
                graphable.append(r["key"])
    # split/house 필터는 inventory에서 키 집합으로 교차
    keyset = set()
    with open(config.INVENTORY_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["drawing"] != config.TARGET_DRAWING_TYPE:
                continue
            if r["split"] != split:
                continue
            if house and r["house"] != house:
                continue
            keyset.add(r["key"])
    chosen = [k for k in graphable if k in keyset][:n]
    print(f"완전한 FP 도면 {len(chosen)}개 선택: {chosen}")
    for k in chosen:
        extract_key(k, split, dest)
    return chosen


def main(argv=None):
    ap = argparse.ArgumentParser(description="Task 1-1 압축해제·인벤토리")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inventory", help="전체 ZIP 스캔 → inventory.csv")
    pl = sub.add_parser("link", help="원천 PNG 지문으로 라벨종류 연결 + 중복률 측정")
    pl.add_argument("--split", choices=SPLITS, default="Training")
    pe = sub.add_parser("extract", help="도면키 단위 추출")
    pe.add_argument("--key", required=True)
    pe.add_argument("--split", choices=SPLITS, default=None)
    pe.add_argument("--dest", default=str(config.RAW_DIR / "sample"))
    ps = sub.add_parser("sample", help="완전한 FP 도면 N개 추출")
    ps.add_argument("--n", type=int, default=1)
    ps.add_argument("--split", choices=SPLITS, default="Validation")
    ps.add_argument("--house", choices=config.HOUSE_TYPES, default=None)
    ps.add_argument("--dest", default=str(config.RAW_DIR / "sample"))
    args = ap.parse_args(argv)

    if args.cmd == "inventory":
        build_inventory()
    elif args.cmd == "link":
        build_linkage(split=args.split)
    elif args.cmd == "extract":
        extract_key(args.key, args.split, Path(args.dest))
    elif args.cmd == "sample":
        sample_complete_fp(args.n, args.split, args.house, Path(args.dest))


if __name__ == "__main__":
    main()
