"""diffplanner_to_cadrender — 한국형 엔진 출력(샘플) → 도면(이미지) + DXF (CLI).

로직은 전부 패키지 단일소스 `plan2graph.engine_render`에 있다(GUI·비교 화면과 공유).
이 스크립트는 그 위의 얇은 배치 CLI일 뿐이다.

실행:
  PYTHONPATH=src python scripts/diffplanner_to_cadrender.py \
      --engine-json /tmp/korean_engine_out.json --n 8 --out /tmp/p2g_render
  (--engine-json = partitioning stage 최종 출력. 단일 record dict 또는 record list.)
"""
from __future__ import annotations

import argparse
import json
import os

from plan2graph import engine_render


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine-json", required=True)
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--out", default="/tmp/p2g_render")
    args = ap.parse_args()

    data = json.load(open(args.engine_json, encoding="utf-8"))
    recs = (data if isinstance(data, list) else [data])[:args.n]
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    print(f"렌더 {len(recs)}개 → {args.out}_*")
    for k, rec in enumerate(recs):
        try:
            s = engine_render.render_record(rec, f"{args.out}_{k}")
            print(f"  [{k}] {s['plan_id']}: 방{s['rooms']} 문{s['doors']} 창{s['windows']} "
                  f"기구{s['fixtures']} 잔여{s['issues']} PNG✓ DXF{'✓' if s['dxf'] else '✗'}")
        except Exception as e:
            import traceback
            print(f"  [{k}] 실패: {e}")
            traceback.print_exc()


if __name__ == "__main__":
    main()
