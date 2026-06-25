"""C 오케스트레이터: A(한국 gated) 종료 대기 -> ep별 평가(천장곡선) -> B(RPLAN grid256-rb) 학습 -> ep별 평가.
GPU1 순차. 수동개입 0. nohup으로 ssh 끊겨도 생존."""
import os, re, subprocess, sys, time

PY = "/home/ju/.local/share/mamba/envs/p2g/bin/python"
A_PID = int(sys.argv[1]) if len(sys.argv) > 1 else 3372549


def alive(pid):
    try:
        os.kill(pid, 0); return True
    except Exception:
        return False


def evalone(ck, vocab, country):
    if not os.path.exists(ck):
        return None
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="1", PYTHONPATH="src:scripts")
    p = subprocess.run([PY, "scripts/eval_ar_geom.py", "--ckpt", ck, "--vocab", vocab,
        "--n", "200", "--render", "0", "--constrained", "--orthogonal",
        "--country", str(country), "--seed", "42"], capture_output=True, text=True, env=env)
    out = p.stdout + p.stderr
    def pct(m):
        for l in out.splitlines():
            if m in l:
                x = re.findall(r"\((\d+)%\)", l)
                if x:
                    return x[-1] + "%"
        return "?"
    rooms = "?"
    for l in out.splitlines():
        if "rooms~" in l:
            rooms = l.split("rooms~")[-1].strip()
    return dict(clean=pct("clean(strict)"), selfint=pct("selfint=0"),
                overlap=pct("overlap<0.25"), single=pct("single(strict)"), rooms=rooms)


# ── STAGE 1: A 대기 + 한국 gated 천장곡선 ──
print("[orch] A(한국 gated) 종료 대기 pid=%d" % A_PID, flush=True)
while alive(A_PID):
    time.sleep(120)
print("[orch] A 종료. 한국 gated ep별 평가", flush=True)
VG = "data/staging/tokens_korean_gated/vocab.json"
rows = []
for ep in [60, 70, 80, 90, 100, 110, 120, 130, 140, 150]:
    ck = "ckpts/korplan_ar_k_gated_ft_ep%d.pt" % ep
    r = evalone(ck, VG, 0)
    if r:
        rows.append((ep, r))
        print("[eval] gated ep%d: clean %s single %s" % (ep, r["clean"], r["single"]), flush=True)
with open("results_korean_gated_curve.md", "w", encoding="utf-8") as f:
    f.write("# 한국 gated FT 천장 곡선 (RPLAN->FT, gated GT 100%% clean)\n\n")
    f.write("eval n200 seed42 country0. 비교: 옛 ungated RPLAN->FT snap clean=20%%.\n\n")
    f.write("| ep(한국=ep-50) | clean | selfint=0 | overlap<.25 | single | rooms |\n|---|---|---|---|---|---|\n")
    for ep, r in rows:
        f.write("| ep%d (%d) | **%s** | %s | %s | %s | %s |\n"
                % (ep, ep - 50, r["clean"], r["selfint"], r["overlap"], r["single"], r["rooms"]))
print("[orch] -> results_korean_gated_curve.md", flush=True)

# ── STAGE 2: B(RPLAN grid256-rb) 학습 + 평가 ──
while not os.path.exists("data/staging/tokens_rplan_rb256/train.jsonl"):
    time.sleep(30)
print("[orch] B 학습 시작: RPLAN grid256-rb (room-perm seed42, ep120)", flush=True)
env = dict(os.environ, CUDA_VISIBLE_DEVICES="1", PYTHONPATH="src:scripts")
with open("logs_ar_r_rb256.log", "w") as lf:
    bp = subprocess.Popen([PY, "scripts/train_wall_cycle.py",
        "--data", "data/staging/tokens_rplan_rb256/train.jsonl",
        "--vocab", "data/staging/tokens_rplan_rb256/vocab.json",
        "--epochs", "120", "--batch", "32", "--lr", "1e-4",
        "--d-model", "512", "--n-layer", "24", "--n-head", "32", "--max-len", "1152",
        "--dim-ff", "1408", "--grad-ckpt", "--amp", "--constrained", "--orthogonal",
        "--country", "1", "--seed", "42", "--room-perm", "--room-perm-prob", "0.5",
        "--diag-every", "10", "--ckpt-every", "10",
        "--out", "ckpts/korplan_ar_r_rb256_roomperm.pt"],
        stdout=lf, stderr=subprocess.STDOUT, env=env)
    bp.wait()
print("[orch] B 학습 종료. grid256 ep별 평가", flush=True)
VR = "data/staging/tokens_rplan_rb256/vocab.json"
rows = []
for ep in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120]:
    ck = "ckpts/korplan_ar_r_rb256_roomperm_ep%d.pt" % ep
    r = evalone(ck, VR, 1)
    if r:
        rows.append((ep, r))
        print("[eval] rb256 ep%d: clean %s" % (ep, r["clean"]), flush=True)
with open("results_rplan_grid256_curve.md", "w", encoding="utf-8") as f:
    f.write("# RPLAN grid256 (rBoundary) 천장 곡선 — room-perm+seed42\n\n")
    f.write("eval n200 seed42 country1. 비교: grid128 tokens_rplan room-perm 피크 46%%(ep80).\n\n")
    f.write("| ep | clean | selfint=0 | overlap<.25 | single | rooms |\n|---|---|---|---|---|---|\n")
    for ep, r in rows:
        f.write("| ep%d | **%s** | %s | %s | %s | %s |\n"
                % (ep, r["clean"], r["selfint"], r["overlap"], r["single"], r["rooms"]))
print("[orch] -> results_rplan_grid256_curve.md  C 완료", flush=True)
