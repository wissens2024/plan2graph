#!/usr/bin/env python3
"""gate2_patch_diffplanner.py — DiffPlanner 엔진 아키텍처를 한국형으로 확장(Gate 2).

ADR-0006/0007. 세 stage(node/adjacency/partitioning) 전부에 대해
    num_category 6 -> 13   (한국 13역할: 거실·안방·침실·주방·화장실·욕실·발코니·
                            드레스룸·전실·복도·실외기실·다목적·기타)
    max_num_rooms 8 -> 18  (온전 데이터 p95=17, 24는 쓰레기로 부풀려진 값)
을 적용한다. 매직넘버를 흩지 않고 **모듈 단일소스 상수**(NUM_CATEGORY/MAX_NUM_ROOMS)로
박고 transformer/script_util/sample 이 import 해서 쓰게 한다.

영향 레이어(아키텍처가 실제로 바뀌는 곳):
  node:         condition_number_emb Linear(8->18), condition_category_emb Linear(6->13)
  adjacency:    생성 타깃 = 방×방 인접행렬 -> in/out channels 8->18,
                condition_number_emb Linear(8->18)
  partitioning: condition_number_emb Linear(8->18), condition_adjacency_emb Linear(8->18)
주의: condition_door_emb=Linear(8)은 현관 4점×2라 **건드리지 않는다**(방 수 무관).

각 파일을 .bak_gate2 로 백업하고, old 문자열 발생횟수를 검증(불일치=중단)한다.
재실행해도 안전(이미 패치된 파일은 old 미발견 -> 명확히 실패 -> 백업에서 복구).

사용:  python gate2_patch_diffplanner.py            # 적용
       python gate2_patch_diffplanner.py --revert   # .bak_gate2 에서 복구
"""
import os
import sys
import shutil

ROOT = os.path.expanduser("~/diffplanner_work")

CONST = "NUM_CATEGORY = 13\nMAX_NUM_ROOMS = 18\n\n\n"

# op kinds: ("ins_before", anchor, text) | ("ins_after", anchor, text)
#           ("repl", old, new, count) | ("repl_all", old, new, count)
EDITS = {
    # ---- datasets: 단일소스 상수 + max_num_rooms + 카테고리 스칼라 인코드(/3 -> /(NC/2)) ----
    "node_diff/node_diff/rplan_datasets.py": [
        ("ins_before", "get_one_hot = lambda x, z: np.eye(z)[x]", CONST),
        ("repl", "        self.max_num_rooms = 8",
                 "        self.max_num_rooms = MAX_NUM_ROOMS", 1),
        ("repl", "    num_category = 6", "    num_category = NUM_CATEGORY", 1),
        ("repl", '["category"]) + 1) / 3 - 1',
                 '["category"]) + 1) / (NUM_CATEGORY / 2) - 1', 1),
    ],
    "adjacency_diff/adjacency_diff/rplan_datasets.py": [
        ("ins_before", "get_one_hot = lambda x, z: np.eye(z)[x]", CONST),
        ("repl", "        self.max_num_rooms = 8",
                 "        self.max_num_rooms = MAX_NUM_ROOMS", 1),
        ("repl_all", '["category"]) + 1) / 3 - 1',
                     '["category"]) + 1) / (NUM_CATEGORY / 2) - 1', 2),
    ],
    "partitioning_diff/partitioning_diff/rplan_datasets.py": [
        ("ins_before", "get_one_hot = lambda x, z: np.eye(z)[x]", CONST),
        ("repl", "        self.max_num_rooms = 8",
                 "        self.max_num_rooms = MAX_NUM_ROOMS", 1),
        ("repl_all", '["category"]) + 1) / 3 - 1',
                     '["category"]) + 1) / (NUM_CATEGORY / 2) - 1', 2),
    ],
    # ---- transformers: 영향 Linear 차원(상수 참조) ----
    "node_diff/node_diff/transformer.py": [
        ("ins_after", "from .nn import timestep_embedding",
                      "from .rplan_datasets import MAX_NUM_ROOMS, NUM_CATEGORY"),
        ("repl", "self.condition_number_emb = nn.Linear(8, self.model_channels)",
                 "self.condition_number_emb = nn.Linear(MAX_NUM_ROOMS, self.model_channels)", 1),
        ("repl", "self.condition_category_emb = nn.Linear(6, self.model_channels)",
                 "self.condition_category_emb = nn.Linear(NUM_CATEGORY, self.model_channels)", 1),
    ],
    "adjacency_diff/adjacency_diff/transformer.py": [
        ("ins_after", "from .nn import timestep_embedding",
                      "from .rplan_datasets import MAX_NUM_ROOMS"),
        ("repl", "self.condition_number_emb = nn.Linear(8, self.model_channels)",
                 "self.condition_number_emb = nn.Linear(MAX_NUM_ROOMS, self.model_channels)", 1),
    ],
    "partitioning_diff/partitioning_diff/transformer.py": [
        ("ins_after", "from .nn import timestep_embedding",
                      "from .rplan_datasets import MAX_NUM_ROOMS"),
        ("repl", "self.condition_number_emb = nn.Linear(8, self.model_channels)",
                 "self.condition_number_emb = nn.Linear(MAX_NUM_ROOMS, self.model_channels)", 1),
        ("repl", "self.condition_adjacency_emb = nn.Linear(8, self.model_channels)",
                 "self.condition_adjacency_emb = nn.Linear(MAX_NUM_ROOMS, self.model_channels)", 1),
    ],
    # ---- adjacency script_util: 생성 타깃 차원(방×방) ----
    "adjacency_diff/adjacency_diff/script_util.py": [
        ("ins_after", "from .transformer import TransformerModel",
                      "from .rplan_datasets import MAX_NUM_ROOMS"),
        ("repl", "        input_channels = 8",
                 "        input_channels = MAX_NUM_ROOMS", 1),
    ],
    # ---- sample: 카테고리 스칼라 디코드(*6 -> *NUM_CATEGORY) ----
    "node_diff/scripts/sample.py": [
        ("ins_after", "from node_diff.rplan_datasets import load_rplan_data",
                      "from node_diff.rplan_datasets import NUM_CATEGORY"),
        ("repl_all", "* 6) - 1", "* NUM_CATEGORY) - 1", 2),
    ],
    "adjacency_diff/scripts/sample.py": [
        ("ins_after", "from adjacency_diff.rplan_datasets import load_rplan_data",
                      "from adjacency_diff.rplan_datasets import NUM_CATEGORY"),
        ("repl", "* 6) - 1", "* NUM_CATEGORY) - 1", 1),
    ],
    "partitioning_diff/scripts/sample.py": [
        ("ins_after", "from partitioning_diff.rplan_datasets import load_rplan_data",
                      "from partitioning_diff.rplan_datasets import NUM_CATEGORY"),
        ("repl", "* 6) - 1", "* NUM_CATEGORY) - 1", 1),
    ],
}


def revert():
    n = 0
    for rel in EDITS:
        p = os.path.join(ROOT, rel)
        bak = p + ".bak_gate2"
        if os.path.exists(bak):
            shutil.copy2(bak, p)
            n += 1
            print(f"  reverted {rel}")
    print(f"reverted {n} files from .bak_gate2")


def apply():
    for rel, ops in EDITS.items():
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            sys.exit(f"FATAL: missing {p}")
        with open(p, encoding="utf-8") as f:
            s = f.read()
        orig = s
        for op in ops:
            kind = op[0]
            if kind in ("ins_before", "ins_after"):
                _, anchor, text = op
                c = s.count(anchor)
                if c != 1:
                    sys.exit(f"FATAL {rel}: anchor x{c} (need 1): {anchor!r}")
                if kind == "ins_before":
                    s = s.replace(anchor, text + anchor, 1)
                else:
                    s = s.replace(anchor, anchor + "\n" + text, 1)
            else:  # repl / repl_all
                _, old, new, cnt = op
                c = s.count(old)
                if c != cnt:
                    sys.exit(f"FATAL {rel}: {old!r} found x{c} (need {cnt})")
                s = s.replace(old, new)
        if s == orig:
            print(f"  (no change) {rel}")
            continue
        if not os.path.exists(p + ".bak_gate2"):
            shutil.copy2(p, p + ".bak_gate2")
        with open(p, "w", encoding="utf-8") as f:
            f.write(s)
        print(f"  patched {rel}")
    print("OK — all edits applied")


if __name__ == "__main__":
    if "--revert" in sys.argv:
        revert()
    else:
        apply()
