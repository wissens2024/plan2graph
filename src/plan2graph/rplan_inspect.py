"""RPLAN 도면 검수 — 정상분(그래프 변환)·제외분(사유)을 사람이 눈으로 확인.

RPLAN 샘플(256×256 4채널 PNG: boundary/category/instance/inside)을 어댑터로 변환:
  converted : 방 인스턴스 추출 성공 → global_rplan 그래프化(정상분)
  excluded  : 방 0개/이미지 형식 불량 → 제외분(사유)

⚠️ RPLAN 원본 PNG는 '인덱스 맵'이라 그대로는 사람이 못 본다. 검수 렌더는
   category 채널을 방 종류별 색으로 칠하고(=사람이 보는 도면), 오버레이 시
   instance 경계를 검정 외곽선으로, 문(FrontDoor/InteriorDoor)을 강조한다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402
from plan2graph.adapters import rplan as _rp  # noqa: E402

# 데이터 위치: 기본 data/external/rplan. 다른 곳에 풀었으면 PLAN2GRAPH_RPLAN로 지정.
RP_ROOT = Path(os.environ.get("PLAN2GRAPH_RPLAN",
                              str(config.DATA_DIR / "external" / "rplan")))
GRAPHS = config.DATA_DIR / "releases" / "global_rplan" / "graphs"

# 카테고리(0~17) → 표시색(RGB). 0~12 방, 13 외부, 14·16 벽, 15·17 문.
CAT_COLORS = {
    0: (255, 179, 186),   # 거실 LivingRoom
    1: (174, 198, 255),   # 안방 MasterRoom
    2: (255, 223, 159),   # 주방 Kitchen
    3: (181, 234, 215),   # 화장실 Bathroom
    4: (226, 197, 247),   # 식당 DiningRoom
    5: (199, 224, 255),   # 자녀방 ChildRoom
    6: (213, 245, 178),   # 서재 StudyRoom
    7: (160, 196, 255),   # 두번째방 SecondRoom
    8: (190, 190, 246),   # 손님방 GuestRoom
    9: (175, 240, 240),   # 발코니 Balcony
    10: (255, 205, 130),  # 현관 Entrance
    11: (214, 214, 214),  # 창고 Storage
    12: (235, 205, 250),  # 드레스룸 Walk-in
    13: (250, 250, 250),  # 외부 External(거의 흰색)
    14: (90, 90, 90),     # 외벽 ExteriorWall
    15: (220, 40, 40),    # 현관문 FrontDoor
    16: (140, 140, 140),  # 내벽 InteriorWall
    17: (245, 130, 40),   # 실내문 InteriorDoor
}
CAT_KO = {0: "거실", 1: "안방", 2: "주방", 3: "화장실", 4: "식당", 5: "자녀방",
          6: "서재", 7: "둘째방", 8: "손님방", 9: "발코니", 10: "현관", 11: "창고",
          12: "드레스룸"}
UPSCALE = 4   # 256px 원본을 ×4(=1024)로 확대(인덱스 맵이라 NEAREST로 또렷하게)


def scan() -> dict:
    """RPLAN 원본 PNG 스캔 → {all, converted, excluded}.

    all       : 원본 도면 전체(변환 여부 무관) — zip만 풀면 바로 검수 가능.
    converted : 이미 그래프化된 것(graph_id=RPLAN_<stem> 존재).
    excluded  : 아직 변환 안 됐거나 변환 실패한 것.
    ※ 어댑터(global_rplan) 미실행이면 converted=∅, all로 원본 검수.
    """
    converted = {p.stem[len("RPLAN_"):] for p in GRAPHS.glob("*.json")} if GRAPHS.exists() else set()
    allrecs = []
    if RP_ROOT.is_dir():
        for png in sorted(RP_ROOT.glob("**/*.png")):
            sid = png.stem
            allrecs.append({"id": sid, "png": str(png), "_conv": sid in converted})
    return {"all": allrecs,
            "converted": [r for r in allrecs if r["_conv"]],
            "excluded": [r for r in allrecs if not r["_conv"]]}


def exclude_reason(rec: dict) -> str:
    """제외분 사유(재파싱)."""
    try:
        r = _rp.parse(rec["png"])
    except Exception as e:  # noqa: BLE001
        return f"파싱 오류: {str(e)[:40]}"
    if r is None:
        return "방 인스턴스 0개 / 4채널 PNG 아님"
    nroom = sum(1 for n in r["layout"]["nodes"] if isinstance(n["id"], int))
    return "방 0개" if nroom == 0 else f"방 {nroom}개(변환됨)"


def _load_channels(png_path: str):
    """RPLAN PNG → (category, instance) 2D 배열. 형식 불량이면 (None, None)."""
    import numpy as np
    from PIL import Image
    arr = np.array(Image.open(png_path))
    if arr.ndim != 3 or arr.shape[2] <= max(_rp.CAT_CH, _rp.INST_CH):
        return None, None
    return arr[:, :, _rp.CAT_CH], arr[:, :, _rp.INST_CH]


def render(rec: dict, overlay: bool = True):
    """RPLAN PNG → 방 종류색 도면(PIL). overlay 시 instance 경계 외곽선 + 문 강조."""
    import numpy as np
    from PIL import Image
    cat, inst = _load_channels(rec["png"])
    if cat is None:
        raise ValueError("4채널 PNG 아님(RPLAN 형식 불량)")
    h, w = cat.shape
    rgb = np.full((h, w, 3), 245, dtype="uint8")
    for code, color in CAT_COLORS.items():
        rgb[cat == code] = color
    if overlay:
        # instance 경계(오른쪽·아래 이웃과 다르면 외곽선) → 방 구분 시각화
        edge = np.zeros((h, w), bool)
        edge[:, :-1] |= inst[:, :-1] != inst[:, 1:]
        edge[:-1, :] |= inst[:-1, :] != inst[1:, :]
        rgb[edge] = (40, 40, 40)
        # 문 픽셀(FrontDoor·InteriorDoor) 강조 — 경계 위에 덮어 다시 칠함
        door = np.isin(cat, list(_rp.DOOR_CATS))
        rgb[door] = (220, 30, 30)
    img = Image.fromarray(rgb, "RGB")
    if UPSCALE > 1:
        img = img.resize((w * UPSCALE, h * UPSCALE), Image.NEAREST)
    return img


if __name__ == "__main__":
    s = scan()
    print(f"RPLAN: 원본 {len(s['all'])} · 변환됨 {len(s['converted'])} · 미변환 {len(s['excluded'])}")
