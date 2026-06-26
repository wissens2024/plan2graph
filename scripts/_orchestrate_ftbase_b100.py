"""FT-base ablation (GPU0 병렬분, 200W캡): grid256 RPLAN 베이스 ep100 에서
한국 gated(grid256) FT -> ep별 평가 -> 천장곡선.
GPU0 단독(캡200W, 느리지만 안전). 메인 orch(GPU1=b90,b110)와 병렬. nohup 생존.
"""
import glob, os, re, subprocess

PY = "/home/ju/.local/share/mamba/envs/p2g/bin/python"
DATA = "data/staging/tokens_korean_gated_g256/train.jsonl"
VOCAB = "data/staging/tokens_korean_gated_g256/vocab.json"
KOR_EP = 80
BASE_EP, TAG = 100, "b100"
GPU = "0"


def train():
    base = "ckpts/korplan_ar_r_rb256_roomperm_ep%d.pt" % BASE_EP
    out = "ckpts/korplan_ar_k_g256_ftR_%s.pt" % TAG
    total = BASE_EP + KOR_EP
    env = dict(os.environ, CUDA_VISIBLE_DEVICES=GPU, PYTHONPATH="src:scripts")
    print("[orch-b100] FT %s: resume %s -> epochs %d (korean %d) GPU%s" % (TAG, base, total, KOR_EP, GPU), flush=True)
    with open("logs_ar_k_g256_ftR_%s.log" % TAG, "w") as lf:
        p = subprocess.Popen([PY, "scripts/train_wall_cycle.py",
            "--data", DATA, "--vocab", VOCAB,
            "--resume", base, "--epochs", str(total), "--batch", "32", "--lr", "1e-4",
            "--d-model", "512", "--n-layer", "24", "--n-head", "32", "--max-len", "1152",
            "--dim-ff", "1408", "--grad-ckpt", "--amp", "--constrained", "--orthogonal",
            "--country", "0", "--seed", "42", "--room-perm", "--room-perm-prob", "0.5",
            "--diag-every", "10", "--ckpt-every", "10", "--out", out],
            stdout=lf, stderr=subprocess.STDOUT, env=env)
        p.wait()
    print("[orch-b100] FT %s 종료 rc=%s" % (TAG, p.returncode), flush=True)


def evalone(ck):
    env = dict(os.environ, CUDA_VISIBLE_DEVICES=GPU, PYTHONPATH="src:scripts")
    p = subprocess.run([PY, "scripts/eval_ar_geom.py", "--ckpt", ck, "--vocab", VOCAB,
        "--n", "200", "--render", "0", "--constrained", "--orthogonal",
        "--country", "0", "--seed", "42"], capture_output=True, text=True, env=env)
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


train()
print("[orch-b100] 평가 시작", flush=True)
cks = sorted(glob.glob("ckpts/korplan_ar_k_g256_ftR_%s_ep*.pt" % TAG),
             key=lambda x: int(re.findall(r"_ep(\d+)\.pt", x)[0]))
rows = []
for ck in cks:
    gep = int(re.findall(r"_ep(\d+)\.pt", ck)[0])
    kep = gep - BASE_EP
    r = evalone(ck)
    rows.append((kep, gep, r))
    print("[eval] %s korean ep%d (g%d): clean %s single %s" % (TAG, kep, gep, r["clean"], r["single"]), flush=True)

with open("results_korean_g256_ftbase_b100_curve.md", "w", encoding="utf-8") as f:
    f.write("# 한국 gated grid256 FT — 베이스 ep100 (GPU0 병렬분)\n\n")
    f.write("eval n200 seed42 country0. 데이터=tokens_korean_gated_g256(GT clean 99%%). ")
    f.write("ep90/ep110은 results_korean_g256_ftbase_curve.md.\n\n")
    f.write("## 베이스 grid256 ep100 (RPLAN clean 56%%)\n\n")
    f.write("| korean ep | clean | selfint=0 | overlap<.25 | single | rooms |\n|---|---|---|---|---|---|\n")
    for kep, gep, r in rows:
        f.write("| ep%d | **%s** | %s | %s | %s | %s |\n"
                % (kep, r["clean"], r["selfint"], r["overlap"], r["single"], r["rooms"]))
print("[orch-b100] -> results_korean_g256_ftbase_b100_curve.md  완료", flush=True)
