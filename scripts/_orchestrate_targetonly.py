"""논문 FT ablation 정본: gated 데이터로 target-only(미사전학습) 학습 → ep별 strict 평가.
production(gated RPLAN→FT)과 동일 데이터·레시피, 차이는 --resume 없음(fresh)뿐.
→ gated 데이터에서 '사전학습 효과'를 깨끗이 분리(데이터품질 교란 제거).
"""
import glob, os, re, subprocess

PY = "/home/ju/.local/share/mamba/envs/p2g/bin/python"
DATA = "data/staging/tokens_korean_gated/train.jsonl"
VOCAB = "data/staging/tokens_korean_gated/vocab.json"
OUT = "ckpts/korplan_ar_k_gated_targetonly.pt"

env = dict(os.environ, CUDA_VISIBLE_DEVICES="1", PYTHONPATH="src:scripts")
print("[TO] gated target-only 학습 시작 (fresh, no resume, ep100)", flush=True)
with open("logs_ar_k_gated_targetonly.log", "w") as lf:
    p = subprocess.Popen([PY, "scripts/train_wall_cycle.py",
        "--data", DATA, "--vocab", VOCAB,
        "--epochs", "100", "--batch", "32", "--lr", "1e-4",
        "--d-model", "512", "--n-layer", "24", "--n-head", "32", "--max-len", "1152",
        "--dim-ff", "1408", "--grad-ckpt", "--amp", "--constrained", "--orthogonal",
        "--country", "0", "--seed", "42", "--room-perm", "--room-perm-prob", "0.5",
        "--diag-every", "10", "--ckpt-every", "10", "--out", OUT],
        stdout=lf, stderr=subprocess.STDOUT, env=env)
    p.wait()
print("[TO] 학습 종료 rc=%s. ep별 strict 평가" % p.returncode, flush=True)


def evalone(ck):
    e = dict(os.environ, CUDA_VISIBLE_DEVICES="1", PYTHONPATH="src:scripts")
    r = subprocess.run([PY, "scripts/eval_ar_geom.py", "--ckpt", ck, "--vocab", VOCAB,
        "--n", "200", "--render", "0", "--constrained", "--orthogonal",
        "--country", "0", "--seed", "42"], capture_output=True, text=True, env=e)
    out = r.stdout + r.stderr

    def g(metric):
        for l in out.splitlines():
            if metric in l:
                x = re.findall(r"\((\d+)%\)", l)
                if x:
                    return int(x[-1])
        return None
    rooms = "?"
    for l in out.splitlines():
        if "rooms~" in l:
            rooms = l.split("rooms~")[-1].strip()
    return dict(drawable=g("clean(drawable)"), loose=g("clean(strict)"),
                single=g("single(strict)"), selfint=g("selfint=0"), rooms=rooms)


cks = sorted(glob.glob("ckpts/korplan_ar_k_gated_targetonly_ep*.pt"),
             key=lambda x: int(re.findall(r"_ep(\d+)\.pt", x)[0]))
rows = []
for ck in cks:
    ep = int(re.findall(r"_ep(\d+)\.pt", ck)[0])
    r = evalone(ck)
    rows.append((ep, r))
    print("[eval] target-only ep%d: strict %s loose %s selfint %s" % (ep, r["drawable"], r["loose"], r["selfint"]), flush=True)

with open("results_korean_gated_targetonly_strict.md", "w", encoding="utf-8") as f:
    f.write("# 한국 gated target-only (미사전학습) — strict 곡선\n\n")
    f.write("eval n200 seed42 country0. production(gated RPLAN→FT)과 동일 데이터/레시피, --resume만 없음.\n")
    f.write("→ FT(사전학습) 효과 분리: 이 곡선 vs results_korean_gated_strict.md(=RPLAN→FT) 비교.\n\n")
    f.write("| ep | clean(strict) | clean(loose) | single | selfint=0 | rooms |\n|---|---|---|---|---|---|\n")
    for ep, r in rows:
        f.write("| ep%d | **%s%%** | %s%% | %s%% | %s%% | %s |\n"
                % (ep, r["drawable"], r["loose"], r["single"], r["selfint"], r["rooms"]))
print("[TO] -> results_korean_gated_targetonly_strict.md 완료", flush=True)
