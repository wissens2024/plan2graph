#!/usr/bin/env python3
"""gate2b_datadir_patch.py — DiffPlanner 데이터 디렉터리를 env로 교체 가능하게.

문제: 로더가 `../../dataset/dataset_json/data_{set}.json`를 하드코딩 → 사전학습(RPLAN)과
파인튜닝(한국)이 같은 파일명(data_train.json)을 두고 충돌. RPLAN 데이터를 덮어쓰지 않고
두 단계를 깨끗이 돌리려면 데이터 디렉터리를 바꿀 수 있어야 한다.

해결: 환경변수 DIFFPLANNER_DATA_DIR(기본=기존 경로)로 디렉터리만 교체.
  사전학습:  DIFFPLANNER_DATA_DIR=../../dataset/dataset_json          (RPLAN, 그대로)
  파인튜닝:  DIFFPLANNER_DATA_DIR=../../dataset/dataset_json_korean   (한국 온전)

3 stage 로더 동일 수정. .bak_gate2b 백업 + 카운트 검증.
"""
import os
import sys
import shutil

ROOT = os.path.expanduser("~/diffplanner_work")

OLD = "        dataset_json_path = f'../../dataset/dataset_json/data_{self.set_name}.json'"
NEW = ("        _ddir = os.environ.get('DIFFPLANNER_DATA_DIR', "
       "'../../dataset/dataset_json')\n"
       "        dataset_json_path = f'{_ddir}/data_{self.set_name}.json'")

FILES = [
    "node_diff/node_diff/rplan_datasets.py",
    "adjacency_diff/adjacency_diff/rplan_datasets.py",
    "partitioning_diff/partitioning_diff/rplan_datasets.py",
]


def main():
    revert = "--revert" in sys.argv
    for rel in FILES:
        p = os.path.join(ROOT, rel)
        bak = p + ".bak_gate2b"
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
            sys.exit(f"FATAL {rel}: path line x{s.count(OLD)} (need 1)")
        if s.count("\nimport json") != 1:
            sys.exit(f"FATAL {rel}: 'import json' anchor not unique")
        s = s.replace("\nimport json", "\nimport os\nimport json", 1)
        s = s.replace(OLD, NEW, 1)
        if not os.path.exists(bak):
            shutil.copy2(p, bak)
        with open(p, "w", encoding="utf-8") as f:
            f.write(s)
        print(f"  patched {rel}")
    print("reverted." if revert else "OK — DIFFPLANNER_DATA_DIR enabled")


if __name__ == "__main__":
    main()
