"""type조건 효과 측정 — typed 생성기를 house_type 조건 ON(정답유형) vs OFF(ANY)로
균형 dwelling test에서 평가해 주거형태별·매크로 adj_L1 차이를 본다(낮을수록 좋음).

같은 체크포인트·같은 시드(rng)로 house_type 인자만 바꿔 비교 → 순수 조건 효과.
소비자 이관 검증도 겸함: generators.load(arch dispatch)만으로 typed 모델을 로드.

사용: PYTHONPATH=src python scripts/typed_effect.py [--version v0] [--seeds 42,1,2,3,4]
"""
from __future__ import annotations

import statistics as stt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph import model_baseline as mb  # noqa: E402
from plan2graph import eval_gen  # noqa: E402
from plan2graph import generators as G  # noqa: E402

HOUSES = ("APT", "DEH", "ROW")


def _macro_by_house(gen_factory, test, tsigs):
    """house_type별 그룹 → 각 그룹 adj_L1 + 매크로(APT/DEH/ROW 동등가중)."""
    groups = {}
    for r in test:
        ht = (r.get("meta", {}) or {}).get("house_type") or "?"
        groups.setdefault(ht, []).append(r)
    out = {}
    for ht, sub in groups.items():
        m = eval_gen._metrics(gen_factory(ht), sub, tsigs, False, None)
        out[ht] = m["adj_L1"]
    macro = sum(out.get(h, 0.0) for h in HOUSES) / len(HOUSES)
    return out, macro


def run(version: str = "v0", seeds=(42, 1, 2, 3, 4)):
    train = mb._load_split(version, "train")
    test = mb._load_split(version, "test")
    if not train or not test:
        print(f"  [데이터 없음] {version}"); return
    tsigs = mb.fit(train).get("train_sigs", set())
    cond = {h: [] for h in HOUSES}; unc = {h: [] for h in HOUSES}
    cmac, umac = [], []
    for s in seeds:
        ckpt = config.PROJECT_ROOT / "models" / f"gen_{version}_typed_seed{s}.pt"
        if not ckpt.exists():
            print(f"  [체크포인트 없음] {ckpt}"); continue
        gen = G.load(ckpt)   # arch dispatch (set-transformer-typed)
        # house_type 조건 ON = 정답유형 주입 / OFF = None(ANY, 조건 미지정)
        on_fac = lambda ht, _g=gen: (lambda prog, rng: _g.generate(prog, rng, house_type=ht))
        off_fac = lambda ht, _g=gen: (lambda prog, rng: _g.generate(prog, rng, house_type=None))
        ch, cm = _macro_by_house(on_fac, test, tsigs)
        uh, um = _macro_by_house(off_fac, test, tsigs)
        cmac.append(cm); umac.append(um)
        for h in HOUSES:
            cond[h].append(ch.get(h)); unc[h].append(uh.get(h))
        print(f"seed{s}: 조건ON macro={cm:.3f} {ch} | 조건OFF macro={um:.3f} {uh}")

    def ms(xs):
        xs = [x for x in xs if x is not None]
        return (stt.mean(xs), stt.pstdev(xs)) if xs else (0.0, 0.0)

    print(f"\n=== type조건 효과 ({version}, adj_L1 낮을수록 좋음, Δ>0 = 조건이 도움) ===")
    print(f"{'house':6} {'조건ON':>14} {'조건OFF':>14} {'Δ개선':>9}")
    for h in HOUSES:
        cm, cs = ms(cond[h]); um, us = ms(unc[h])
        print(f"{h:6} {cm:>7.3f}±{cs:<5.3f} {um:>7.3f}±{us:<5.3f} {um - cm:>+9.3f}")
    cm, cs = ms(cmac); um, us = ms(umac)
    print(f"{'MACRO':6} {cm:>7.3f}±{cs:<5.3f} {um:>7.3f}±{us:<5.3f} {um - cm:>+9.3f}")

    # GUI 라이브 산출물(eval_ab.json 패턴) — 대시보드 §2가 읽음. [mean, std].
    import json
    out = config.DATA_DIR / "releases" / "typed_effect.json"
    payload = {"version": version, "arch": "set-transformer-typed", "seeds": list(seeds),
               "houses": list(HOUSES),
               "on": {h: list(ms(cond[h])) for h in HOUSES},
               "off": {h: list(ms(unc[h])) for h in HOUSES},
               "macro_on": list(ms(cmac)), "macro_off": list(ms(umac))}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장(GUI): {out}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v0")
    ap.add_argument("--seeds", default="42,1,2,3,4")
    a = ap.parse_args()
    run(a.version, tuple(int(x) for x in a.seeds.split(",") if x.strip()))
