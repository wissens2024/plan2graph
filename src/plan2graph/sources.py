"""출처(Source) 레지스트리 — 데이터셋 개념설계 P1 (DATASET_DESIGN.md §2①).

모든 데이터 출처를 한 곳에서 정의한다. 레코드는 meta.source(태그)만 들고 있고,
역할(role)·진입단(tier)·양식(modality)은 여기서 resolve()로 끌어온다.

  role : benchmark(test 동결 보유) | pretrain(train/val만)  ← §6
  tier : 1(벡터/인덱스 파싱) | 2(비전 세그멘테이션)            ← §3
  modality : label_coco | svg_vector | index_map | render_raster

※ meta.role 과 meta.provenance(aihub_label/v2_pred/global_pretrain)는 **별개 축**:
  role=평가에서의 위치, provenance=라벨이 만들어진 출처·품질.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402


@dataclass(frozen=True)
class Source:
    id: str
    modality: str          # label_coco | svg_vector | index_map | render_raster
    tier: int              # 1 | 2
    role: str              # benchmark | pretrain

    @property
    def staging_dir(self) -> Path:
        return config.DATA_DIR / "staging" / self.id


REGISTRY: dict[str, Source] = {
    "aihub":      Source("aihub",      "label_coco",    1, "benchmark"),
    "cubicasa5k": Source("cubicasa5k", "svg_vector",    1, "pretrain"),
    "rplan":      Source("rplan",      "index_map",     1, "pretrain"),
    # 미래(설계 P5): rplan_render(render_raster, tier2), 원본벡터 rplan_vector(index_map)
}

# 레코드 meta.source 태그 → registry id (별칭/접두 매칭).
_ALIASES = {"aihub-71465": "aihub"}


def resolve(source_tag: str | None) -> Source | None:
    """meta.source 태그 → Source. 별칭·접두 매칭. 미상이면 None."""
    if not source_tag:
        return None
    if source_tag in REGISTRY:
        return REGISTRY[source_tag]
    if source_tag in _ALIASES:
        return REGISTRY[_ALIASES[source_tag]]
    for key, src in REGISTRY.items():        # 접두 폴백(예: 'rplan_render' → rplan)
        if source_tag.startswith(key):
            return src
    return None


def role_of(source_tag: str | None, default: str = "pretrain") -> str:
    s = resolve(source_tag)
    return s.role if s else default


def tier_of(source_tag: str | None, default: int = 1) -> int:
    s = resolve(source_tag)
    return s.tier if s else default


# ── 경로 해석 (expand 패턴: staging/ 있으면 그쪽, 없으면 레거시) ──────────────
# 전환기엔 코드가 양쪽을 다 인식해 데이터 이동 전에도 안 깨진다(DATASET_DESIGN §9).
_LEGACY_GRAPHS = {
    "aihub":      config.PROCESSED_DIR / "graphs",
    "cubicasa5k": config.release_dir("global_cubicasa") / "graphs",
    "rplan":      config.release_dir("global_rplan") / "graphs",
}
# AI-Hub 큐/원장은 현재 processed/ 직하. 글로벌은 staging 전까지 큐 없음.
_LEGACY_QUEUE_ROOT = {"aihub": config.PROCESSED_DIR}


def staging_root(source_id: str) -> Path:
    return config.DATA_DIR / "staging" / source_id


def graphs_dir(source_id: str) -> Path:
    """그래프 디렉터리. aihub=parsed/graphs(g-0.4 정본), 그 외 staging/<id>/graphs 우선."""
    if source_id == "aihub":
        return config.DATA_DIR / "staging" / "parsed" / "graphs"
    s = staging_root(source_id) / "graphs"
    if s.is_dir():
        return s
    return _LEGACY_GRAPHS.get(source_id, s)


def queue_path(source_id: str, which: str) -> Path:
    """검수 큐 CSV(accepted|quarantine). staging 우선, 없으면 레거시."""
    fn = "quarantine.csv" if which == "quarantine" else "accepted.csv"
    sroot = staging_root(source_id)
    if sroot.is_dir():
        return sroot / fn
    return _LEGACY_QUEUE_ROOT.get(source_id, sroot) / fn


def ledger_path(source_id: str) -> Path:
    """결정 원장 CSV. staging 우선, 없으면 레거시(aihub=processed/ledger.csv)."""
    sroot = staging_root(source_id)
    if sroot.is_dir():
        return sroot / "ledger.csv"
    return _LEGACY_QUEUE_ROOT.get(source_id, sroot) / "ledger.csv"


def manifest_path(source_id: str) -> Path:
    """원천 처분 manifest(JSONL). staging/<id>/manifest.jsonl."""
    return staging_root(source_id) / "manifest.jsonl"


def provenance_map(source_id: str) -> dict[str, str]:
    """graph_id → 라벨 출처·품질(manifest.reason). 버전 레시피의 provenance 필터용.

    DATASET_DESIGN §5: recipe의 status/role만으론 v0(dual)·v2(+V2V)를 구분 못 한다
    (staging success가 V2V 복구분을 포함하므로). manifest.reason을 조인해 선언적 구분.
    manifest 없는 출처(cubicasa/rplan)는 빈 dict → provenance 필터는 no-op.
    """
    import json
    p = manifest_path(source_id)
    out: dict[str, str] = {}
    if p.is_file():
        with p.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                r = d.get("reason")
                for g in (d.get("graph_ids") or []):
                    out[g] = r
    return out


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    for tag in ("aihub-71465", "cubicasa5k", "rplan", "rplan_render", "unknown"):
        s = resolve(tag)
        print(f"{tag:14s} → {s.id+'/'+s.role+'/tier'+str(s.tier) if s else 'None(pretrain/tier1 기본)'}")
