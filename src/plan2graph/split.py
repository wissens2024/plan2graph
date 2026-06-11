"""Task 2-4 — train/val/test 분할 + 데이터셋 카드.

누수 방지 핵심: 한 시트(한 PNG)에서 나온 여러 세대(u0/u1…)는 같은 평면 유형의
변형일 수 있다 → **시트 단위로 분할**해 같은 시트의 세대가 train/test에 갈리지 않게 한다.
분할은 시트키 해시로 결정(재현 가능, SPLIT_SEED).

출력: data/processed/splits/{train,val,test}.txt (graph_id 목록),
      data/processed/dataset_card.md (통계·분포·한계).
"""
from __future__ import annotations

import collections
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402


def _sheet_key(graph_id: str) -> str:
    """'APT_FP_<fp>_u3' → 'APT_FP_<fp>' (세대 접미사 제거)."""
    return graph_id.rsplit("_u", 1)[0]


def _bucket(sheet_key: str) -> str:
    """시트키 해시 → train/val/test (SPLIT_RATIOS 비율, 결정적)."""
    h = hashlib.md5(f"{config.SPLIT_SEED}:{sheet_key}".encode()).hexdigest()
    x = int(h[:8], 16) / 0xFFFFFFFF  # [0,1)
    r = config.SPLIT_RATIOS
    if x < r["train"]:
        return "train"
    if x < r["train"] + r["val"]:
        return "val"
    return "test"


def split_and_card(graphs_dir: Path, out_dir: Path) -> None:
    files = sorted(graphs_dir.glob("*.json"))
    if not files:
        raise SystemExit(f"채택 그래프 없음: {graphs_dir}")
    records = [json.loads(p.read_text(encoding="utf-8")) for p in files]
    print(f"채택 그래프 {len(records):,}개 로드")

    # 시트 단위 분할
    splits: dict[str, list[str]] = {"train": [], "val": [], "test": []}
    sheet_split: dict[str, str] = {}
    for r in records:
        sk = _sheet_key(r["graph_id"])
        s = sheet_split.setdefault(sk, _bucket(sk))
        splits[s].append(r["graph_id"])

    split_dir = out_dir / "splits"
    split_dir.mkdir(parents=True, exist_ok=True)
    for s, ids in splits.items():
        (split_dir / f"{s}.txt").write_text(
            "\n".join(sorted(ids)) + "\n", encoding="utf-8")
    n_sheets = len(sheet_split)
    print(f"  시트 {n_sheets:,}개 → train {len(splits['train']):,} / "
          f"val {len(splits['val']):,} / test {len(splits['test']):,} (세대 기준)")

    _write_card(records, splits, n_sheets, out_dir)


def _write_card(records, splits, n_sheets, out_dir: Path) -> None:
    n = len(records)
    house = collections.Counter(r["meta"]["house_type"] for r in records)
    rooms = sorted(r["meta"]["n_rooms"] for r in records)
    room_class = collections.Counter()
    edge_via = collections.Counter()
    door_types = collections.Counter()
    n_with_balcony = 0
    n_scaled = 0
    floor_areas = []
    for r in records:
        if r["meta"].get("scale_confidence") == "ok":
            n_scaled += 1
            if r["meta"].get("floor_area_m2"):
                floor_areas.append(r["meta"]["floor_area_m2"])
        for nd in r["layout"]["nodes"]:
            if nd.get("type") and nd["type"] != "exterior":
                room_class[nd["type"]] += 1
        has_bal = False
        for e in r["layout"]["edges"]:
            edge_via[e["via"]] += 1
            if e["via"] == "door" and e.get("door_type"):
                door_types[e["door_type"]] += 1
            if e["via"] == "balcony":
                has_bal = True
        n_with_balcony += int(has_bal)

    def pct(x):
        return f"{x / max(n, 1) * 100:.1f}%"

    med = rooms[n // 2]
    lines = []
    lines.append("# Plan2Graph 데이터셋 카드 (v0.1)\n")
    lines.append(f"- 출처: AI-Hub 건축 도면 데이터(dataSetSn=71465), `{config.__dict__.get('PROJECT_ROOT')}`")
    lines.append("- 단위: **세대(주거) 단위 방-문-방 위상 그래프**. 각 그래프 = 배치(layout) + 파생 제약(constraints) 한 쌍.")
    lines.append("- 좌표: 픽셀. 면적: scale(㎡) 확보분은 ㎡, 미확보분은 픽셀²(치수선 OCR 역산·신뢰게이트).\n")

    lines.append("## 규모")
    lines.append(f"- 채택(완벽한 세대) 그래프: **{n:,}개** (시트 {n_sheets:,}개에서 추출)")
    lines.append(f"- 분할(시트 단위, 누수 방지): train {len(splits['train']):,} / "
                 f"val {len(splits['val']):,} / test {len(splits['test']):,}")
    lines.append(f"- 주택유형: " + ", ".join(f"{k} {v:,}" for k, v in house.most_common()))
    lines.append(f"- 세대당 방수: 최소 {rooms[0]}, 중앙값 {med}, 최대 {rooms[-1]}")
    lines.append(f"- 발코니 통로(미닫이창) 보유 세대: {n_with_balcony:,} ({pct(n_with_balcony)})")
    _fa = sorted(floor_areas)
    _famed = f"{_fa[len(_fa)//2]:.0f}" if _fa else "-"
    lines.append(f"- **scale 확보(㎡ 변환) 세대: {n_scaled:,} ({pct(n_scaled)})** "
                 f"— 치수선 OCR 역산, 침실면적 신뢰게이트 통과분. 바닥면적 중앙값 {_famed}㎡. "
                 f"미확보분은 scale=None(scale_quarantine.csv)\n")

    lines.append("## 채택 기준 (완벽한 주거)")
    lines.append("- 필수 5요소(현관·거실·침실·주방·화장실) 모두 보유 + 단일 연결 + 무결성 통과")
    lines.append(f"- 방수 {config.ACCEPT_MIN_ROOMS}~{config.ACCEPT_MAX_ROOMS} 범위")
    lines.append("- 미달 세대는 `quarantine.csv`에 사유와 함께 격리(후일 검토)\n")

    lines.append("## 공간(방) 클래스 분포 (노드 수)")
    for k, v in room_class.most_common():
        lines.append(f"- {k.replace('공간_', '')}: {v:,}")
    lines.append("")
    lines.append("## 엣지(연결) 분포")
    for k, v in edge_via.most_common():
        lines.append(f"- {k}: {v:,}")
    if door_types:
        lines.append("- 문 세부유형: " + ", ".join(f"{k} {v:,}" for k, v in door_types.most_common()))
    lines.append("")

    lines.append("## 엣지 종류 설명")
    lines.append("- **door**: 출입문(구조_출입문) 연결. **balcony**: 발코니 슬라이딩(미닫이창). "
                 "**open**: 문·창 없이 벽이 끊긴 개방통로(개방형 LDK 등, 표준 엣지). "
                 "**entrance/exterior_door**: 현관↔외부.")
    lines.append("")
    lines.append("## 알려진 한계")
    lines.append(f"- **scale 부분 확보**: {n_scaled:,}세대 ㎡ 변환(치수선 OCR). 나머지는 픽셀² → "
                 "면적 의존 법규는 ㎡ 확보분에만 적용, 나머지는 scale_quarantine.csv.")
    lines.append("- **`공간_기타` 노이즈**: 기타가 최다 노드. 벽두께·자투리 단편이 개방통로로 세대에 편입돼 노드 수를 부풀림. 면적 임계 필터 검토 중(ROADMAP §8).")
    lines.append("- **개방통로(open) 임계 민감도**: gap≤60px·미피복비율≥0.30 기준. 드물게 과연결(>60방)은 방수 상한으로 격리. 손검증 20장 정량 측정은 후속.")
    lines.append("- **문-방 추론 정확도**: 합성 단위테스트 통과·시각 게이트 확인 단계.")
    lines.append("- **다세대 시트 분해**: 한 PNG의 여러 세대를 연결요소로 분해. 드물게 인접 세대가 오결합될 수 있음.")
    lines.append("")

    card = out_dir / "dataset_card.md"
    card.write_text("\n".join(lines), encoding="utf-8")
    print(f"  데이터셋 카드: {card}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    out = config.PROCESSED_DIR
    split_and_card(out / "graphs", out)
