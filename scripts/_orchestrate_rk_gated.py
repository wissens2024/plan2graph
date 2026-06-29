"""논문 정본 RK(FT) 학습: R(roomperm_seed42_ep80) → AI-Hub gated FT → ep별 strict 평가.
K(target-only)와 *동일 config*(grid128·seed42·room_perm·동일 하이퍼) + --resume(R)만 차이.
→ R·K·RK 3모델이 코퍼스/FT만 달라 공정 비교. (R·K·R256은 기존 재사용)
"""
import glob, os, re, subprocess

PY = "/home/ju/.local/share/mamba/envs/p2g/bin/python"
GPU = os.environ.get("RK_GPU", "1")              # 비는 GPU로 (기본 1)
DATA = "data/staging/tokens_korean_gated/train.jsonl"
VOCAB = "data/staging/tokens_korean_gated/vocab.json"
BASE = "ckpts/korplan_ar_r_roomperm_seed42_ep80.pt"   # R 사전학습(seed42·room_perm) = FT base
OUT = "ckpts/korplan_ar_rk_gated_seed42_roomperm.pt"  # 새 RK
EPOCHS = 180   # R가 ep80 → +100 FT → ep180 (K의 100ep AI-Hub와 동일 노출)

env = dict(os.environ, CUDA_VISIBLE_DEVICES=GPU, PYTHONPATH="src:scripts")
print(f"[RK] FT 시작: base={BASE} → AI-Hub gated, ep81~{EPOCHS} (GPU{GPU})", flush=True)
with open("logs_ar_rk_gated.log", "w") as lf:
    p = subprocess.Popen([PY, "scripts/train_wall_cycle.py",
        "--data", DATA, "--vocab", VOCAB,
        "--resume", BASE,                         # ★FT: R에서 이어서
        "--epochs", str(EPOCHS), "--batch", "32", "--lr", "1e-4",
        "--d-model", "512", "--n-layer", "24", "--n-head", "32", "--max-len", "1152",
        "--dim-ff", "1408", "--grad-ckpt", "--amp", "--constrained", "--orthogonal",
        "--country", "0", "--seed", "42", "--room-perm", "--room-perm-prob", "0.5",
        "--diag-every", "10", "--ckpt-every", "10", "--out", OUT],
        stdout=lf, stderr=subprocess.STDOUT, env=env)
    p.wait()
print("[RK] 학습 종료 rc=%s → ep별 strict 평가" % p.returncode, flush=True)


def evalone(ck):
    e = dict(os.environ, CUDA_VISIBLE_DEVICES=GPU, PYTHONPATH="src:scripts")
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
    return dict(drawable=g("clean(drawable)"), loose=g("clean(strict)"), selfint=g("selfint=0"))


cks = sorted(glob.glob("ckpts/korplan_ar_rk_gated_seed42_roomperm_ep*.pt"),
             key=lambda x: int(re.findall(r"_ep(\d+)\.pt", x)[0]))
rows = []
for ck in cks:
    ep = int(re.findall(r"_ep(\d+)\.pt", ck)[0])
    r = evalone(ck); rows.append((ep, r))
    print("[eval] RK ep%d: strict %s loose %s" % (ep, r["drawable"], r["loose"]), flush=True)
    with open("results_rk_gated_seed42_strict.md", "w", encoding="utf-8") as f:
        f.write("# RK(RPLAN→AI-Hub FT) gated seed42·room_perm — strict 곡선\n\n")
        f.write("eval n200 seed42 country0. R(roomperm_seed42_ep80)→AI-Hub FT. "
                "K(target-only, results_korean_gated_targetonly_strict.md)와 동일 config → FT 효과 비교.\n\n")
        f.write("| ep | clean(strict=도면답게) | clean(loose) | selfint=0 |\n|---|---|---|---|\n")
        for e2, r2 in rows:
            f.write("| ep%d | **%s%%** | %s%% | %s%% |\n" % (e2, r2["drawable"], r2["loose"], r2["selfint"]))
print("[RK] -> results_rk_gated_seed42_strict.md 완료", flush=True)
