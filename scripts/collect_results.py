"""결과 자동 수집기 — 매트릭스(GPU1)+ep80(GPU0) 학습 종료 대기 → 전 모델 평가 → 기록지(results_report.md).

학습 끝나면 GPU 비므로 GPU1에서 평가. seed=42 + 표준 overlap(RLVR). 수동 개입 0.
"""
import os
import re
import subprocess
import sys
import time

PY = "/home/ju/.local/share/mamba/envs/p2g/bin/python"
MATRIX_PID = 1989350
EP80_PID = 2218353
RPLAN_VOCAB = "data/staging/tokens_rplan/vocab.json"
VN = "data/staging/tokens_korean_clean_nosnap/vocab.json"
VS = "data/staging/tokens_korean_clean_snap/vocab.json"


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def evalone(ck, vocab, country):
    if not os.path.exists(ck):
        return None
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="1", PYTHONPATH="src:scripts")
    p = subprocess.run(
        [PY, "scripts/eval_ar_geom.py", "--ckpt", ck, "--vocab", vocab,
         "--n", "200", "--render", "0", "--constrained", "--orthogonal",
         "--country", str(country), "--seed", "42"],
        capture_output=True, text=True, env=env)
    out = p.stdout + p.stderr

    def pct(marker):
        for line in out.splitlines():
            if marker in line:
                m = re.findall(r"\((\d+)%\)", line)
                if m:
                    return m[-1] + "%"
        return "?"

    rooms = "?"
    for line in out.splitlines():
        if "rooms~" in line:
            rooms = line.split("rooms~")[-1].strip()
    return dict(decoded=pct("decoded"), clean=pct("clean(strict)"),
                selfint=pct("selfint=0"), overlap=pct("overlap<0.25"),
                single=pct("single(strict)"), rooms=rooms)


def main():
    print(f"[collect] 학습 종료 대기 (matrix={MATRIX_PID}, ep80={EP80_PID})", flush=True)
    while alive(MATRIX_PID) or alive(EP80_PID):
        time.sleep(120)
    print("[collect] 모든 학습 종료. 평가 시작", flush=True)

    rows = []
    # RPLAN per-epoch (country=CN=1)
    for ep in (10, 20, 30, 40, 50, 60, 70, 80):
        ck = f"ckpts/korplan_ar_r_fmlm80m_pretrain_v2_ep{ep}.pt"
        r = evalone(ck, RPLAN_VOCAB, 1)
        if r:
            rows.append((f"RPLAN ep{ep}", r))
            print(f"[eval] RPLAN ep{ep}: clean {r['clean']}", flush=True)

    # 한국 매트릭스 (country=KR=0). target-only=ep50, FT=ep100
    kor = [
        ("한국 target-only nosnap", "ckpts/korplan_ar_k_nosnap_ep50.pt", VN),
        ("한국 target-only snap", "ckpts/korplan_ar_k_snap_ep50.pt", VS),
        ("한국 RPLAN→FT nosnap", "ckpts/korplan_ar_rk_nosnap_ep100.pt", VN),
        ("한국 RPLAN→FT snap", "ckpts/korplan_ar_rk_snap_ep100.pt", VS),
    ]
    for label, ck, voc in kor:
        r = evalone(ck, voc, 0)
        if r:
            rows.append((label, r))
            print(f"[eval] {label}: clean {r['clean']}", flush=True)

    with open("results_report.md", "w", encoding="utf-8") as f:
        f.write("# KorPlan-AR 결과 종합 기록지\n\n")
        f.write("평가: `eval_ar_geom` n=200, **seed=42**, constrained+orthogonal, "
                "overlap=RLVR 표준정의(arXiv:2605.14117), selfint=OGC.\n")
        f.write("⚠️ 탐색 단계 수치(no-room-permutation, dim_ff=1408). 최종은 튜닝버전+seed 재학습.\n\n")
        f.write("| 모델 | decoded | **clean** | selfint=0 | overlap<.25 | single | rooms |\n")
        f.write("|---|---|---|---|---|---|---|\n")
        for label, r in rows:
            f.write(f"| {label} | {r['decoded']} | **{r['clean']}** | {r['selfint']} | "
                    f"{r['overlap']} | {r['single']} | {r['rooms']} |\n")
        f.write("\n## 핵심 비교\n")
        f.write("- **snap 효과**: target-only/FT 각각 nosnap↔snap clean 비교\n")
        f.write("- **pretrain 효과**: target-only↔RPLAN→FT clean 비교\n")
        f.write("- **RPLAN 수렴**: ep10~80 clean 곡선 (평탄 여부)\n")
    print("[collect] 완료 → results_report.md", flush=True)


if __name__ == "__main__":
    main()
