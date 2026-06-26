"""모든 곡선 strict 기준 재평가 (clean(drawable)=+대각선0+꼭짓점>=4+외곽품질).
GPU0(200W캡)+GPU1 2개 동시. eval_ar_geom의 clean(drawable) 파싱 → *_strict.md 작성.
"""
import os, re, subprocess, tempfile

PY = "/home/ju/.local/share/mamba/envs/p2g/bin/python"
VK = "data/staging/tokens_korean_gated/vocab.json"
VK256 = "data/staging/tokens_korean_gated_g256/vocab.json"
VR256 = "data/staging/tokens_rplan_rb256/vocab.json"
# RPLAN grid128 vocab(v2/roomperm) — tokens_rplan
VR128 = "data/staging/tokens_rplan/vocab.json"

# (group, label_ep, ckpt, vocab, country)
TASKS = []
def add(group, ckpt, ep, vocab, country, lab=None):
    TASKS.append(dict(group=group, ckpt=ckpt, ep=ep, lab=lab if lab is not None else ep, vocab=vocab, country=country))

# 1) 한국 gated grid128 (production)  korean ep = ep-50
for ep in [60,70,80,90,100,110,120,130,140,150]:
    add("kgat", f"ckpts/korplan_ar_k_gated_ft_ep{ep}.pt", ep, VK, 0, lab=ep-50)
# 2) 한국 grid256 b90 / b110  korean ep = ep-base
for ep in range(100,171,10):
    add("kg256_b90", f"ckpts/korplan_ar_k_g256_ftR_b90_ep{ep}.pt", ep, VK256, 0, lab=ep-90)
for ep in range(120,191,10):
    add("kg256_b110", f"ckpts/korplan_ar_k_g256_ftR_b110_ep{ep}.pt", ep, VK256, 0, lab=ep-110)
# 3) 한국 grid256 b100
for ep in range(110,181,10):
    add("kg256_b100", f"ckpts/korplan_ar_k_g256_ftR_b100_ep{ep}.pt", ep, VK256, 0, lab=ep-100)
# 4) RPLAN grid256
for ep in range(10,121,10):
    add("rg256", f"ckpts/korplan_ar_r_rb256_roomperm_ep{ep}.pt", ep, VR256, 1)
# 5) RPLAN grid128 (v2 no-perm + roomperm_seed42)
for ep in range(10,81,10):
    add("r128_noperm", f"ckpts/korplan_ar_r_fmlm80m_pretrain_v2_ep{ep}.pt", ep, VR128, 1)
for ep in range(10,81,10):
    add("r128_roomperm", f"ckpts/korplan_ar_r_roomperm_seed42_ep{ep}.pt", ep, VR128, 1)

TASKS = [t for t in TASKS if os.path.exists(t["ckpt"])]
print(f"[strict] {len(TASKS)} ckpts 재평가 (n200·seed42·2GPU)", flush=True)


def parse(out):
    def g(metric):
        for l in out.splitlines():
            if metric in l:
                x = re.findall(r"\((\d+)%\)", l)
                if x: return int(x[-1])
        return None
    rooms = "?"
    for l in out.splitlines():
        if "rooms~" in l: rooms = l.split("rooms~")[-1].strip()
    return dict(drawable=g("clean(drawable)"), loose=g("clean(strict)"),
                single=g("single(strict)"), rooms=rooms)


def launch(t, gpu, tmp):
    env = dict(os.environ, CUDA_VISIBLE_DEVICES=str(gpu), PYTHONPATH="src:scripts")
    f = open(tmp, "w")
    p = subprocess.Popen([PY, "scripts/eval_ar_geom.py", "--ckpt", t["ckpt"], "--vocab", t["vocab"],
        "--n", "200", "--render", "0", "--constrained", "--orthogonal",
        "--country", str(t["country"]), "--seed", "42"], stdout=f, stderr=subprocess.STDOUT, env=env)
    return p, f

res = {}
i = 0
while i < len(TASKS):
    batch = TASKS[i:i+2]
    procs = []
    for j, t in enumerate(batch):
        tmp = tempfile.mktemp(suffix=f"_strict_{i+j}.log")
        p, f = launch(t, j % 2, tmp)
        procs.append((t, p, f, tmp))
    for t, p, f, tmp in procs:
        p.wait(); f.close()
        out = open(tmp, encoding="utf-8", errors="ignore").read()
        r = parse(out)
        res.setdefault(t["group"], []).append((t["lab"], t["ep"], r))
        print(f"[eval] {t['group']} ep{t['ep']}(lab{t['lab']}): drawable {r['drawable']}% loose {r['loose']}%", flush=True)
        try: os.remove(tmp)
        except Exception: pass
    i += 2


def table(rows, lab_name="ep"):
    s = f"| {lab_name} | clean(strict/도면답게) | clean(loose/옛) | single | rooms |\n|---|---|---|---|---|\n"
    for lab, ep, r in sorted(rows, key=lambda x: x[0]):
        s += f"| ep{lab} | **{r['drawable']}%** | {r['loose']}% | {r['single']}% | {r['rooms']} |\n"
    return s

def w(path, title, body):
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\neval n200 seed42. clean(strict)=도면답게(+대각선0+꼭짓점>=4+외곽 1덩어리·채움·볼록). loose=옛 정의(selfint·overlap·span만).\n\n")
        f.write(body)
    print(f"-> {path}", flush=True)

if res.get("kgat"):
    w("results_korean_gated_strict.md", "한국 gated FT (grid128, production) — strict 곡선", table(res["kgat"], "korean ep"))
if res.get("kg256_b90") or res.get("kg256_b110"):
    body = ""
    if res.get("kg256_b90"):
        body += "## 베이스 grid256 ep90 (플래토)\n\n" + table(res["kg256_b90"], "korean ep") + "\n"
    if res.get("kg256_b110"):
        body += "## 베이스 grid256 ep110 (피크)\n\n" + table(res["kg256_b110"], "korean ep") + "\n"
    w("results_korean_g256_ftbase_strict.md", "한국 grid256 FT 베이스 ablation — strict 곡선", body)
if res.get("kg256_b100"):
    w("results_korean_g256_ftbase_b100_strict.md", "한국 grid256 FT base ep100 — strict 곡선",
      "## 베이스 grid256 ep100\n\n" + table(res["kg256_b100"], "korean ep"))
if res.get("rg256"):
    w("results_rplan_grid256_strict.md", "RPLAN grid256 (rBoundary) — strict 곡선", table(res["rg256"], "RPLAN ep"))
if res.get("r128_noperm"):
    w("results_rplan_strict.md", "RPLAN grid128 no-perm — strict 곡선", table(res["r128_noperm"], "RPLAN ep"))
if res.get("r128_roomperm"):
    w("results_roomperm_rplan_strict.md", "RPLAN grid128 room-perm+seed42 — strict 곡선", table(res["r128_roomperm"], "room-perm+seed ep"))
print("[strict] 완료", flush=True)
