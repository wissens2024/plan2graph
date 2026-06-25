"""room-perm+seed RPLAN 학습(pid) 종료 대기 → ep10~80 평가 + no-perm(v2) 대조 → results_roomperm_rplan.md.

room-permutation 효능(증강) + seed 재현성 확인용. eval: n200·seed42·표준overlap·country1(CN).
"""
import os
import re
import subprocess
import sys
import time

PY = "/home/ju/.local/share/mamba/envs/p2g/bin/python"
TRAIN_PID = 2529560
VOCAB = "data/staging/tokens_rplan/vocab.json"


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def evalone(ck):
    if not os.path.exists(ck):
        return None
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="0", PYTHONPATH="src:scripts")
    p = subprocess.run(
        [PY, "scripts/eval_ar_geom.py", "--ckpt", ck, "--vocab", VOCAB,
         "--n", "200", "--render", "0", "--constrained", "--orthogonal",
         "--country", "1", "--seed", "42"],
        capture_output=True, text=True, env=env)
    out = p.stdout + p.stderr

    def pct(marker):
        for line in out.splitlines():
            if marker in line:
                m = re.findall(r"\((\d+)%\)", line)
                if m:
                    return m[-1] + "%"
        return "?"
    return dict(decoded=pct("decoded"), clean=pct("clean(strict)"),
                selfint=pct("selfint=0"), overlap=pct("overlap<0.25"),
                single=pct("single(strict)"))


def main():
    print(f"[roomperm] 학습 종료 대기 (pid={TRAIN_PID})", flush=True)
    while alive(TRAIN_PID):
        time.sleep(120)
    print("[roomperm] 학습 종료. 평가 시작", flush=True)

    rows = []
    for ep in (10, 20, 30, 40, 50, 60, 70, 80):
        ck = f"ckpts/korplan_ar_r_roomperm_seed42_ep{ep}.pt"
        r = evalone(ck)
        if r:
            rows.append((f"room-perm+seed ep{ep}", r))
            print(f"[eval] room-perm ep{ep}: clean {r['clean']}", flush=True)
    # no-perm(v2) 대조 — 수렴 에폭만
    for ep in (70, 80):
        ck = f"ckpts/korplan_ar_r_fmlm80m_pretrain_v2_ep{ep}.pt"
        r = evalone(ck)
        if r:
            rows.append((f"[대조] no-perm v2 ep{ep}", r))

    with open("results_roomperm_rplan.md", "w", encoding="utf-8") as f:
        f.write("# RPLAN room-permutation+seed 결과 (재현가능)\n\n")
        f.write("eval: n=200, **seed=42**, 표준 overlap(RLVR), country=CN. 학습=`--room-perm --seed 42`.\n\n")
        f.write("| 모델 | decoded | **clean** | selfint=0 | overlap<.25 | single |\n")
        f.write("|---|---|---|---|---|---|\n")
        for label, r in rows:
            f.write(f"| {label} | {r['decoded']} | **{r['clean']}** | {r['selfint']} | "
                    f"{r['overlap']} | {r['single']} |\n")
        f.write("\n## 보는 법\n")
        f.write("- **room-perm 효능**: room-perm ep70/80 clean ↔ no-perm v2 ep70/80 clean 비교\n")
        f.write("- **수렴**: ep10~80 곡선이 no-perm보다 빠르거나 높은가\n")
        f.write("- **재현성**: seed=42 고정 → 다른 연구자가 같은 코드+시드로 동일 수치 재현\n")
    print("[roomperm] 완료 → results_roomperm_rplan.md", flush=True)


if __name__ == "__main__":
    main()
