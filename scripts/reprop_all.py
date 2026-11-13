#!/usr/bin/env python3
"""reprop_all — 전수 재보정 **뒤에 반드시** 전파를 다시 돌린다.

사용자 확정값(`data/link_overrides.json`)은 **plan_id 로** 걸린다. 그래서 전수
재산출을 돌리면 원본만 확정값을 받고, 같은 그림의 사본은 기하에서 다시 뽑은
값으로 돌아간다. 실측(2026-11-14 전수 적용 뒤): 전파본 **62장이 전부** 갈렸다.
예) c6f3a6f8_u1 문 11(외부1) ↔ 사본 d1193438_u0 문 7(외부0).

보정은 plan_id 가 아니라 **그림**에 속한다. override 를 그림 단위로 바꾸는 게
근본 해법이지만, 전파 변환(평행이동·미러)을 override 좌표에 다시 먹여야 해서
손이 많이 간다. 그때까지는 **전수 체인의 마지막에 전파를 다시 돌린다** —
이미 검증된 기계(dedup_review.propagate / prop_cross)를 그대로 쓰므로 안전하다.

`data/propagated_plans.json` 에 쌓인 (원본 → 대상) 기록을 그대로 재생한다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = "/home/ju/plan2graph"
PY = "/home/ju/.local/share/mamba/envs/p2g/bin/python"
LOG = os.path.join(ROOT, "data", "propagated_plans.json")
APPLY = "--apply" in sys.argv

log = json.load(open(LOG, encoding="utf-8"))
jobs = {}
for j in log:                       # 같은 (sig, 원본, 대상집합) 은 한 번만
    jobs[(j["sig"], j["source"], tuple(sorted(j["targets"])))] = j

print("재생할 전파 %d건 · 대상 %d장"
      % (len(jobs), sum(len(j["targets"]) for j in jobs.values())))
n_ok = n_fail = 0
for (sig, src, _tg), j in jobs.items():
    if sig == "cross":
        cmd = [PY, "-I", "scripts/prop_cross.py", src] + list(j["targets"]) + ["--identity"]
    else:
        cmd = [PY, "-I", "scripts/prop1.py", sig, src]
    if APPLY:
        if sig == "cross":
            cmd.append("--apply")
    else:
        print("   [드라이런] " + " ".join(cmd[2:]))
        continue
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    tail = [l for l in r.stdout.strip().split("\n") if l.strip()][-1:]
    ok = r.returncode == 0
    n_ok += ok
    n_fail += (not ok)
    print("   %-9s %-34s %s" % (sig[:8], src, tail[0].strip() if tail else r.stderr[-120:]))

if APPLY:
    print("\n성공 %d · 실패 %d" % (n_ok, n_fail))
    print("이제 scripts/verify_prop.py 로 내용 대조해라.")
else:
    print("\n[드라이런] --apply 로 실행")
