#!/usr/bin/env python3
"""gate2c_sample_datadir_patch.py — sample.py의 하드코딩 dataset_json_dir도 env로.

gate2b는 학습 로더(rplan_datasets.py)만 DIFFPLANNER_DATA_DIR로 바꿨다. 그런데
sample.py 최상단의 `dataset_json_dir = '../../dataset/dataset_json'`는 별도라,
한국 샘플링 때 dataloader는 한국(boundary/entrance)·name2index는 RPLAN을 봐서
KeyError가 난다. 3 stage sample.py 전부 같은 env로 통일한다(import os 이미 있음).
.bak_gate2c 백업.
"""
import os
import sys
import shutil

ROOT = os.path.expanduser("~/diffplanner_work")
FILES = [
    "node_diff/scripts/sample.py",
    "adjacency_diff/scripts/sample.py",
    "partitioning_diff/scripts/sample.py",
]
OLD = "dataset_json_dir = '../../dataset/dataset_json'"
NEW = "dataset_json_dir = os.environ.get('DIFFPLANNER_DATA_DIR', '../../dataset/dataset_json')"


def main():
    revert = "--revert" in sys.argv
    for rel in FILES:
        p = os.path.join(ROOT, rel)
        bak = p + ".bak_gate2c"
        if revert:
            if os.path.exists(bak):
                shutil.copy2(bak, p)
                print(f"  reverted {rel}")
            continue
        with open(p, encoding="utf-8") as f:
            s = f.read()
        if "DIFFPLANNER_DATA_DIR" in s:
            print(f"  (already) {rel}")
            continue
        if s.count(OLD) != 1:
            sys.exit(f"FATAL {rel}: line x{s.count(OLD)} (need 1)")
        s = s.replace(OLD, NEW, 1)
        if not os.path.exists(bak):
            shutil.copy2(p, bak)
        with open(p, "w", encoding="utf-8") as f:
            f.write(s)
        print(f"  patched {rel}")
    print("reverted." if revert else "OK — sample.py dataset dir env-aware")


if __name__ == "__main__":
    main()
