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
from plan2graph import sources  # noqa: E402
from plan2graph.adapters import rplan as _rp  # noqa: E402

# 데이터 위치: 기본 data/external/rplan. 다른 곳에 풀었으면 PLAN2GRAPH_RPLAN로 지정.
RP_ROOT = Path(os.environ.get("PLAN2GRAPH_RPLAN",
                              str(config.DATA_DIR / "external" / "rplan")))
GRAPHS = sources.graphs_dir("rplan")   # staging/rplan/graphs 우선·없으면 레거시

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
    """RPLAN 검수 universe = 변환된 그래프 레코드 전량 = 다운로드(.mat) 총수.

    원본 1장 = 그래프 레코드 1개(RPLAN_<idx>.json)다. 콤보 '전체'가 곧 그래프 수이고,
    이는 종합 패널 총수(scan_status)·다운로드 .mat 엔트리 수와 항상 동일하다(같은 graphs
    디렉터리를 셈). 상태(정상/격리)는 레코드 meta로 갈리며 패널과 한 소스를 공유한다.

    원본 미리보기:
      png  : snapshot_train RGB 렌더가 있으면 그 경로(부분집합 ~67k, 예쁜 도면).
      idx  : .mat 인덱스(=stem 숫자) — PNG 없는 ~13k는 gtBoxNew 박스로 렌더(render 참조).
    반환: {all, with_png, no_png}. (이전의 converted/excluded는 폐기 — 전량이 변환됨)
    """
    png_by_stem: dict = {}
    if RP_ROOT.is_dir():
        for png in RP_ROOT.glob("**/*.png"):
            png_by_stem.setdefault(png.stem, str(png))
    recs = []
    if GRAPHS.is_dir():
        for j in GRAPHS.glob("RPLAN_*.json"):
            stem = j.stem[len("RPLAN_"):]
            recs.append({"id": stem, "graph_id": j.stem,
                         "png": png_by_stem.get(stem),
                         "idx": int(stem) if stem.isdigit() else None})
    recs.sort(key=lambda r: (r["idx"] is None, r["idx"] if r["idx"] is not None else 0, r["id"]))
    return {"all": recs,
            "with_png": [r for r in recs if r.get("png")],
            "no_png": [r for r in recs if not r.get("png")]}


def exclude_reason(rec: dict) -> str:
    """제외분 사유(재파싱)."""
    import numpy as np
    from PIL import Image
    arr = np.array(Image.open(rec["png"]))
    if arr.ndim != 3 or arr.shape[2] < 4:
        return "렌더된 도면 이미지(4채널 인덱스맵 아님) — 그래프 변환 불가, 보기 전용"
    try:
        r = _rp.parse(rec["png"])
    except Exception as e:  # noqa: BLE001
        return f"파싱 오류: {str(e)[:40]}"
    if r is None:
        return "방 인스턴스 0개 / 4채널 PNG 아님"
    nroom = sum(1 for n in r["layout"]["nodes"] if isinstance(n["id"], int))
    return "방 0개" if nroom == 0 else f"방 {nroom}개(변환됨)"


def _load_channels(png_path: str):
    """RPLAN '원본 인덱스맵' PNG → (category, instance) 2D 배열.
    인덱스맵은 4채널(boundary/category/instance/inside)이어야 함. 그 외(이미 렌더된
    3채널 RGB 도면 등)는 (None, None) → render가 원본 그대로 표시."""
    import numpy as np
    from PIL import Image
    arr = np.array(Image.open(png_path))
    if arr.ndim != 3 or arr.shape[2] < 4:
        return None, None
    return arr[:, :, _rp.CAT_CH], arr[:, :, _rp.INST_CH]


def render(rec: dict, overlay: bool = True):
    """원본 도면 1장(PIL). snapshot PNG가 있으면 그걸로, 없으면 .mat 벡터 박스로 렌더."""
    png = rec.get("png")
    if png:
        return _render_png(png, overlay)
    img = _render_boxes(rec.get("idx"), overlay)
    if img is not None:
        return img
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (512, 512), (245, 245, 245))
    ImageDraw.Draw(img).text((16, 16), f"{rec.get('graph_id', '?')}\n미리보기 불가\n(snapshot PNG·data.mat 모두 없음)",
                             fill=(120, 120, 120))
    return img


def _render_png(png_path: str, overlay: bool = True):
    """snapshot_train PNG 표시. 4채널 인덱스맵이면 방 종류색으로 칠하고(경계·문 오버레이),
    이미 렌더된 RGB 도면이면 원본 그대로."""
    import numpy as np
    from PIL import Image
    cat, inst = _load_channels(png_path)
    if cat is None:
        img = Image.open(png_path).convert("RGB")
        if max(img.size) < 600:
            img = img.resize((img.size[0] * 3, img.size[1] * 3), Image.LANCZOS)
        return img
    h, w = cat.shape
    rgb = np.full((h, w, 3), 245, dtype="uint8")
    for code, color in CAT_COLORS.items():
        rgb[cat == code] = color
    if overlay:
        edge = np.zeros((h, w), bool)
        edge[:, :-1] |= inst[:, :-1] != inst[:, 1:]
        edge[:-1, :] |= inst[:-1, :] != inst[1:, :]
        rgb[edge] = (40, 40, 40)
        door = np.isin(cat, list(_rp.DOOR_CATS))
        rgb[door] = (220, 30, 30)
    img = Image.fromarray(rgb, "RGB")
    if UPSCALE > 1:
        img = img.resize((w * UPSCALE, h * UPSCALE), Image.NEAREST)
    return img


_MAT_DATA = None   # data.mat(전체 80,788 벡터) 1회 로드 캐시 — PNG 없는 레코드 렌더용


def _mat_data():
    """RPLAN Network/data.mat의 'data'(플랜별 구조 배열)를 1회 로드해 캐시.
    RP_ROOT가 snapshot_train을 가리켜도 rplan 루트 밑에서 data.mat을 탐색한다."""
    global _MAT_DATA
    if _MAT_DATA is None:
        import scipy.io as sio
        cands = [RP_ROOT / "Network" / "data.mat",
                 config.DATA_DIR / "external" / "rplan" / "Network" / "data.mat"]
        p = next((c for c in cands if c.exists()), None)
        if p is None:
            found = list((config.DATA_DIR / "external" / "rplan").glob("**/data.mat"))
            p = found[0] if found else None
        try:
            _MAT_DATA = (sio.loadmat(str(p), struct_as_record=False,
                                     squeeze_me=True)["data"] if p and p.exists() else [])
        except Exception:  # noqa: BLE001
            _MAT_DATA = []
    return _MAT_DATA


def _render_boxes(idx, overlay: bool = True):
    """data.mat[idx]의 gtBoxNew+rType → 방 박스 색칠(PIL).
    어댑터와 동일 좌표 규약([x0,y0,x1,y1]=x:열·y:행, 방 타입 0~12만) → 그래프뷰·centroid와 일치."""
    import numpy as np
    from PIL import Image
    data = _mat_data()
    if idx is None or not (0 <= idx < len(data)):
        return None
    e = data[idx]
    boxes = getattr(e, "gtBoxNew", None)
    if boxes is None:
        boxes = getattr(e, "gtBox", None)
    types = getattr(e, "rType", None)
    if boxes is None or types is None:
        return None
    boxes = np.atleast_2d(np.asarray(boxes))
    types = np.atleast_1d(np.asarray(types)).ravel()
    if boxes.ndim != 2 or boxes.shape[0] == 0:
        return None
    w = int(boxes[:, :4].max()) + 1
    rgb = np.full((w, w, 3), 245, dtype="uint8")
    n = min(len(types), len(boxes))
    # 큰 방부터 칠해 작은 방이 위에 오게(겹침 시 가독성)
    order = sorted(range(n), key=lambda i: -abs(int(boxes[i][2] - boxes[i][0]) *
                                                int(boxes[i][3] - boxes[i][1])))
    for i in order:
        t = int(types[i])
        if t > 12:   # 방(0~12)만 — 어댑터 ROOM_CAT_MAX와 동일(13+ 외부·벽·문 제외)
            continue
        x0, y0, x1, y1 = (int(boxes[i][0]), int(boxes[i][1]),
                          int(boxes[i][2]), int(boxes[i][3]))
        if x1 <= x0 or y1 <= y0:
            continue
        rgb[y0:y1, x0:x1] = CAT_COLORS.get(t, (200, 200, 200))
        if overlay:   # 방 외곽선
            rgb[y0:y1, x0] = (40, 40, 40); rgb[y0:y1, x1 - 1] = (40, 40, 40)
            rgb[y0, x0:x1] = (40, 40, 40); rgb[y1 - 1, x0:x1] = (40, 40, 40)
    img = Image.fromarray(rgb, "RGB")
    s = max(1, 1024 // w)
    return img.resize((w * s, w * s), Image.NEAREST)


if __name__ == "__main__":
    s = scan()
    print(f"RPLAN: 변환 전량 {len(s['all'])} · PNG미리보기 {len(s['with_png'])} · "
          f".mat렌더 {len(s['no_png'])}")
