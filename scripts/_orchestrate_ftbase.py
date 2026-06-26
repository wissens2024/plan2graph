"""FT-base ablation 오케스트레이터: grid256 RPLAN 베이스 ep90 vs ep110 에서
한국 gated(grid256) FT -> ep별 평가 -> 천장곡선 비교.
GPU1 순차. nohup 생존. 수동개입 0.
사용자 질문: '피크(ep110)가 항상 전이에 좋은가?' -> 추측말고 비교.
"""
import glob, os, re, subprocess, sys

PY = "/home/ju/.local/share/mamba/envs/p2g/bin/python"
DATA = "data/staging/tokens_korean_gated_g256/train.jsonl"
VOCAB = "data/staging/tokens_korean_gated_g256/vocab.json"
KOR_EP = 80  # 한국 FT 에폭 수 (run A 피크 korean ep80 포착)

# (base_epoch, out_tag)
RUNS = [(90, "b90"), (110, "b110")]


def train(base_ep, tag):
    base = "ckpts/korplan_ar_r_rb256_roomperm_ep%d.pt" % base_ep
    out = "ckpts/korplan_ar_k_g256_ftR_%s.pt" % tag
    total = base_ep + KOR_EP
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="1", PYTHONPATH="src:scripts")
    print("[orch] FT %s: resume %s -> epochs %d (korean %d)" % (tag, base, total, KOR_EP), flush=True)
    with open("logs_ar_k_g256_ftR_%s.log" % tag, "w") as lf:
        p = subprocess.Popen([PY, "scripts/train_wall_cycle.py",
            "--data", DATA, "--vocab", VOCAB,
            "--resume", base, "--epochs", str(total), "--batch", "32", "--lr", "1e-4",
            "--d-model", "512", "--n-layer", "24", "--n-head", "32", "--max-len", "1152",
            "--dim-ff", "1408", "--grad-ckpt", "--amp", "--constrained", "--orthogonal",
            "--country", "0", "--seed", "42", "--room-perm", "--room-perm-prob", "0.5",
            "--diag-every", "10", "--ckpt-every", "10", "--out", out],
            stdout=lf, stderr=subprocess.STDOUT, env=env)
        p.wait()
    print("[orch] FT %s 종료 rc=%s" % (tag, p.returncode), flush=True)


def evalone(ck):
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="1", PYTHONPATH="src:scripts")
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


# 학습 (순차)
for base_ep, tag in RUNS:
    train(base_ep, tag)

# 평가 + 곡선
print("[orch] 평가 시작", flush=True)
results = {}
for base_ep, tag in RUNS:
    cks = sorted(glob.glob("ckpts/korplan_ar_k_g256_ftR_%s_ep*.pt" % tag),
                 key=lambda x: int(re.findall(r"_ep(\d+)\.pt", x)[0]))
    rows = []
    for ck in cks:
        gep = int(re.findall(r"_ep(\d+)\.pt", ck)[0])
        kep = gep - base_ep
        r = evalone(ck)
        rows.append((kep, gep, r))
        print("[eval] %s korean ep%d (g%d): clean %s single %s"
              % (tag, kep, gep, r["clean"], r["single"]), flush=True)
    results[tag] = rows

with open("results_korean_g256_ftbase_curve.md", "w", encoding="utf-8") as f:
    f.write("# 한국 gated grid256 FT — 베이스 ep90 vs ep110 ablation\n\n")
    f.write("eval n200 seed42 country0. 데이터=tokens_korean_gated_g256(GT clean 99%%). ")
    f.write("비교기준: grid128 gated FT 피크 65%%.\n\n")
    for base_ep, tag in RUNS:
        f.write("## 베이스 grid256 ep%d (RPLAN clean %s)\n\n"
                % (base_ep, "66%" if base_ep == 110 else "56%"))
        f.write("| korean ep | clean | selfint=0 | overlap<.25 | single | rooms |\n|---|---|---|---|---|---|\n")
        for kep, gep, r in results[tag]:
            f.write("| ep%d | **%s** | %s | %s | %s | %s |\n"
                    % (kep, r["clean"], r["selfint"], r["overlap"], r["single"], r["rooms"]))
        f.write("\n")
print("[orch] -> results_korean_g256_ftbase_curve.md  완료", flush=True)
