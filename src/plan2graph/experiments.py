"""실험 자료 보존·재현 — 버전별 실험 디렉토리 + append-only 원장(ledger).

목적: 생성기 조건(데이터버전 × 글로벌 사전학습 무/유 × 파인튜닝 × seed)을
각각 runs/<run_id>/ 에 보존하고, runs/index.jsonl 에 한 줄씩 누적해
"글로벌 안 한 것 vs 한 것" 등을 나란히 비교·재현 가능하게 한다.

- 시드 고정 + 프로비넌스(git commit·데이터 manifest 해시·torch/CUDA·호스트) 기록 → 재현.
- 체크포인트(*.pt)는 용량상 git 제외(시드+코드로 재생성), meta/metrics/log·원장(index.jsonl)은 추적.

CLI: python -m plan2graph.experiments table   # 원장 비교표 출력
"""
from __future__ import annotations

import hashlib
import json
import platform
import re
import socket
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402

RUNS_DIR = ROOT / "runs"
INDEX = RUNS_DIR / "index.jsonl"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT),
            stderr=subprocess.DEVNULL).decode().strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def env_provenance() -> dict:
    """재현 환경 핀: python·platform·host·torch/CUDA·GPU."""
    info = {"python": platform.python_version(), "platform": sys.platform,
            "host": socket.gethostname()}
    try:
        import torch
        info["torch"] = torch.__version__
        info["cuda"] = bool(torch.cuda.is_available())
        if torch.cuda.is_available():
            info["gpu"] = torch.cuda.get_device_name(0)
    except Exception:  # noqa: BLE001
        info["torch"] = None
    return info


def data_provenance(version: str) -> dict:
    """데이터 핀: manifest 해시 + split 크기·해시 → '어떤 데이터였나' 고정."""
    rel = config.DATA_DIR / "releases" / version
    prov = {"version": version, "manifest_sha": None}
    man = rel / "manifest.json"
    if man.exists():
        prov["manifest_sha"] = hashlib.sha256(man.read_bytes()).hexdigest()[:16]
    for sp in ("train", "val", "test"):
        f = rel / "splits" / f"{sp}.txt"
        if f.exists():
            ids = f.read_text(encoding="utf-8").split()
            prov[f"n_{sp}"] = len(ids)
            prov[f"sha_{sp}"] = hashlib.sha256(
                "\n".join(sorted(ids)).encode()).hexdigest()[:12]
    return prov


def seed_everything(seed: int):
    """재현용 시드 고정(random/numpy/torch + cudnn determinism)."""
    import random as _r
    _r.seed(seed)
    try:
        import numpy as _np
        _np.random.seed(seed)
    except Exception:  # noqa: BLE001
        pass
    try:
        import torch
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
    except Exception:  # noqa: BLE001
        pass


def make_run_id(generator: str, version: str, pretrain=None, seed: int = 42,
                arch: str = None) -> str:
    """조건을 사람이 읽는 안정 slug로. 예: gen-v0-neural-set-transformer-v1-noPretrain-seed42."""
    parts = ["gen", version, generator]
    if generator == "neural":
        parts.append(arch or "nn")
        parts.append("noPretrain" if not pretrain else "pre_" + str(pretrain))
    parts.append(f"seed{seed}")
    return "-".join(parts)


def start_run(run_id: str) -> Path:
    d = RUNS_DIR / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_meta(run_dir: Path, meta: dict) -> dict:
    full = {"created": _now(), "git_commit": git_commit(),
            "env": env_provenance(), **meta}
    (run_dir / "meta.json").write_text(
        json.dumps(full, ensure_ascii=False, indent=2), encoding="utf-8")
    return full


def write_metrics(run_dir: Path, rows: list):
    (run_dir / "metrics.json").write_text(
        json.dumps({"rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")


def append_index(record: dict):
    """원장에 한 줄 추가(append-only — 과거 결과 절대 안 지움)."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"logged": _now(), **record}
    with INDEX.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load_index() -> list:
    if not INDEX.exists():
        return []
    return [json.loads(ln) for ln in INDEX.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


def print_table():
    """원장의 eval 기록을 비교표로 — '글로벌 무/유 × 버전 × 생성기'를 나란히."""
    evals = [r for r in load_index() if r.get("kind") == "eval"]
    if not evals:
        print("기록된 평가 없음. (train+eval 후 생성)")
        return
    print(f"{'run_id':60} {'loop':5} {'무결성':>7} {'법규':>6} "
          f"{'인접L1':>7} {'다양성':>7} {'신규성':>7} {'git':>9} {'logged':>20}")
    for r in evals:
        print(f"{str(r.get('run_id',''))[:60]:60} {str(r.get('reg_loop','')):5} "
              f"{r.get('integrity',''):>7} {r.get('legal',''):>6} "
              f"{r.get('adj_L1',''):>7} {r.get('diversity',''):>7} "
              f"{r.get('novelty',''):>7} {str(r.get('git_commit',''))[:8]:>9} "
              f"{str(r.get('logged',''))[:19]:>20}")
    # 최고 성능(인접L1 최소, loop off 기준) 강조
    offs = [r for r in evals if r.get("reg_loop") == "off" and isinstance(r.get("adj_L1"), (int, float))]
    if offs:
        best = min(offs, key=lambda r: r["adj_L1"])
        print(f"\n▶ 인접L1 최저(loop off): {best['run_id']}  adj_L1={best['adj_L1']}")


def _config_key(run_id: str) -> str:
    """시드를 떼어 동일 조건끼리 묶는 키. 예: ...-noPretrain-seed3 → ...-noPretrain."""
    return re.sub(r"-seed\d+", "", run_id or "")


def _ms(vals):
    """mean±std 문자열(시드 노이즈 판정용). n<2면 std=0."""
    xs = [v for v in vals if isinstance(v, (int, float))]
    if not xs:
        return "—", 0
    m = statistics.mean(xs)
    s = statistics.stdev(xs) if len(xs) > 1 else 0.0
    return f"{m:.3f}±{s:.3f}", len(xs)


def _mean_std(vals):
    """(mean, std, n). n<2면 std=0. 숫자 아닌 값은 무시."""
    xs = [v for v in vals if isinstance(v, (int, float))]
    if not xs:
        return None, 0.0, 0
    return statistics.mean(xs), (statistics.stdev(xs) if len(xs) > 1 else 0.0), len(xs)


# 미배치(개발 부산물) 학습본 — 결과 채택에서 제외(30ep·1시드 미수렴, 배치본과 비교 불가).
# git 이력/runs 원본엔 보존하되 agg_summary(결과 집계)에서만 거른다. 모델은 set-transformer 하나.
RETIRED_ARCH = "set-transformer-v1"


def parse_config(cfg: str) -> dict:
    """run_id(시드 제거) → A/B 비교 축. 결과 대시보드가 '데이터버전×생성기'로 묶어 한 표에
    보이게 한다(PROJECT_PLAN §4-4). 신경망은 set-transformer 하나(아키텍처 버전꼬리표 미표기). 예:
      gen-v0-baseline                              → v0 · 규칙기반 · 사전학습없음
      gen-v0-neural-set-transformer-v2-noPretrain  → v0 · 신경망(set-transformer) · 없음
      gen-v0-neural-set-transformer-v2-pre_global_cubicasa → v0 · 〃 · CubiCasa
    """
    parts = re.sub(r"^gen-", "", cfg or "").split("-")
    ft_version = parts[0] if parts else "?"   # finetune 데이터셋(현재 전부 v0=AI-Hub)
    rest = parts[1:]
    if rest and rest[0] == "baseline":
        return {"version": ft_version, "generator": "규칙기반", "arch": "",
                "pretrain": "없음", "ds_version": _ds_version(ft_version, "없음")}
    rest = rest[1:] if rest and rest[0] == "neural" else rest
    pretrain, arch = "없음", []
    for t in rest:
        if t == "noPretrain":
            pretrain = "없음"
        elif t.startswith("pre_global_"):
            pretrain = t.replace("pre_global_", "").capitalize()   # cubicasa→Cubicasa
        elif t.startswith("pre_"):
            pretrain = t[4:]
        elif re.fullmatch(r"T[0-9.]+", t):
            continue                                                # temperature 태그 무시
        else:
            arch.append(t)
    arch_disp = re.sub(r"-v\d+$", "", "-".join(arch))   # 아키텍처 버전꼬리표 제거(모델 하나)
    return {"version": ft_version, "generator": "신경망", "arch": arch_disp,
            "pretrain": pretrain, "ds_version": _ds_version(ft_version, pretrain)}


# 데이터셋 버전 = 사전학습 데이터 조합([[dataset-version-scheme]]): v0=AI-Hub, v2=+CubiCasa,
# v3=+RPLAN. 보정(v1)은 finetune 데이터가 달라 ft_version으로 구분.
_PRETRAIN_VER = {"없음": "v0", "Cubicasa": "v2", "Rplan": "v3"}


def _ds_version(ft_version: str, pretrain: str) -> str:
    if ft_version != "v0":          # 보정 등 다른 finetune셋 = 그 버전 그대로(v1…)
        return ft_version
    return _PRETRAIN_VER.get(pretrain, "v0")


def agg_summary() -> dict:
    """시드 집계를 구조화 dict로 반환(GUI·md 공용 단일 소스).
    반환: {"eval":[{config,loop,seeds,adj_L1_mean,adj_L1_std,integrity,legal,diversity,novelty}],
           "generalization":[{config,subset,seeds,adj_L1_mean,adj_L1_std}]}."""
    rows = load_index()
    ev, gn = defaultdict(list), defaultdict(list)
    for r in rows:
        if RETIRED_ARCH in (r.get("run_id") or ""):   # 미배치 개발본 = 결과 미채택
            continue
        if r.get("kind") == "eval":
            ev[(_config_key(r["run_id"]), r.get("reg_loop"))].append(r)
        elif r.get("kind") == "generalization":
            gn[(_config_key(r["run_id"]), r.get("subset"))].append(r)
    out_ev = []
    for (cfg, loop), rs in sorted(ev.items()):
        am, asd, n = _mean_std([r.get("adj_L1") for r in rs])
        out_ev.append({
            "config": cfg, **parse_config(cfg), "loop": loop, "seeds": n,
            "adj_L1_mean": am, "adj_L1_std": asd,
            "integrity": _mean_std([r.get("integrity") for r in rs])[0],
            "legal": _mean_std([r.get("legal") for r in rs])[0],
            "diversity": _mean_std([r.get("diversity") for r in rs])[0],
            "novelty": _mean_std([r.get("novelty") for r in rs])[0]})
    out_gn = []
    for (cfg, sub), rs in sorted(gn.items(), key=lambda x: (x[0][0], x[0][1])):
        am, asd, n = _mean_std([r.get("adj_L1") for r in rs])
        out_gn.append({"config": cfg, **parse_config(cfg), "subset": sub, "seeds": n,
                       "adj_L1_mean": am, "adj_L1_std": asd})
    return {"eval": out_ev, "generalization": out_gn}


def print_agg():
    """시드 집계 — 동일 조건의 여러 시드를 평균±표준편차로. '차이가 노이즈인가' 판정."""
    rows = load_index()
    ev, gn = defaultdict(list), defaultdict(list)
    for r in rows:
        if r.get("kind") == "eval":
            ev[(_config_key(r["run_id"]), r.get("reg_loop"))].append(r)
        elif r.get("kind") == "generalization":
            gn[(_config_key(r["run_id"]), r.get("subset"))].append(r)
    print("=== [eval] 전체 test (시드 집계) ===")
    print(f"{'config':50} {'loop':4} {'seeds':>5} {'adj_L1(mean±std)':>18} {'legal':>6} {'div':>6}")
    for (cfg, loop), rs in sorted(ev.items()):
        a, n = _ms([r.get("adj_L1") for r in rs])
        lg, _ = _ms([r.get("legal") for r in rs])
        dv, _ = _ms([r.get("diversity") for r in rs])
        print(f"{cfg[:50]:50} {str(loop):4} {n:>5} {a:>18} {lg.split('±')[0]:>6} {dv.split('±')[0]:>6}")
    print("\n=== [generalization] seen/unseen program (시드 집계) ===")
    print(f"{'config':50} {'subset':7} {'seeds':>5} {'adj_L1(mean±std)':>18}")
    for (cfg, sub), rs in sorted(gn.items(), key=lambda x: (x[0][0], x[0][1])):
        a, n = _ms([r.get("adj_L1") for r in rs])
        print(f"{cfg[:50]:50} {str(sub):7} {n:>5} {a:>18}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="table", choices=["table", "agg"])
    a = ap.parse_args()
    print_agg() if a.cmd == "agg" else print_table()
