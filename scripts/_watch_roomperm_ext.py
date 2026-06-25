"""Phase2 ext 학습 종료 대기 -> ep90~120 clean 평가 -> results_roomperm_ext.md (천장 증명)."""
import os, re, subprocess, sys, time
PY = "/home/ju/.local/share/mamba/envs/p2g/bin/python"
TRAIN_PID = int(sys.argv[1])
VOCAB = "data/staging/tokens_rplan/vocab.json"
EPS = [90, 100, 110, 120]

def alive(pid):
    try:
        os.kill(pid, 0); return True
    except Exception:
        return False

def evalone(ck):
    if not os.path.exists(ck):
        return None
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="1", PYTHONPATH="src:scripts")
    p = subprocess.run([PY, "scripts/eval_ar_geom.py", "--ckpt", ck, "--vocab", VOCAB,
        "--n", "200", "--render", "0", "--constrained", "--orthogonal",
        "--country", "1", "--seed", "42"], capture_output=True, text=True, env=env)
    out = p.stdout + p.stderr
    def pct(marker):
        for line in out.splitlines():
            if marker in line:
                m = re.findall(r"\((\d+)%\)", line)
                if m:
                    return m[-1] + "%"
        return "?"
    return dict(clean=pct("clean(strict)"), selfint=pct("selfint=0"),
                overlap=pct("overlap<0.25"), single=pct("single(strict)"))

print("[watch] 학습 종료 대기 pid=%d" % TRAIN_PID, flush=True)
while alive(TRAIN_PID):
    time.sleep(120)
print("[watch] 학습 종료. ep90~120 평가 시작", flush=True)
rows = []
for ep in EPS:
    ck = "ckpts/korplan_ar_r_roomperm_seed42_ep%d.pt" % ep
    r = evalone(ck)
    if r:
        rows.append((ep, r))
        print("[eval] ep%d: clean %s single %s" % (ep, r["clean"], r["single"]), flush=True)
with open("results_roomperm_ext.md", "w", encoding="utf-8") as f:
    f.write("# Phase2 천장 증명 — room-perm+seed42 RPLAN ep90~120 연장\n\n")
    f.write("eval n=200·seed42·표준overlap·country=CN. ep10~80은 results_roomperm_rplan.md.\n\n")
    f.write("| ep | clean | selfint=0 | overlap<.25 | single |\n|---|---|---|---|---|\n")
    f.write("| [기존] ep70 | 38% | 42% | 65% | 73% |\n")
    f.write("| [기존] ep80 | 46% | 47% | 72% | 80% |\n")
    for ep, r in rows:
        f.write("| ep%d | **%s** | %s | %s | %s |\n" % (ep, r["clean"], r["selfint"], r["overlap"], r["single"]))
print("[watch] 완료 -> results_roomperm_ext.md", flush=True)
