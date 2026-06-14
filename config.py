"""Plan2Graph 전역 설정 — 경로, 23-클래스 매핑, 임계값.

실데이터(AI-Hub dataSetSn=71465) 검증으로 확정된 사실:
- 라벨 JSON은 UTF-8. COCO 형식(categories/images[len=1]/annotations).
- 모든 라벨이 동일한 23-클래스 `categories` 리스트를 공유하지만,
  COCO 규약상 category_id는 파일별 정의이므로 **id→name 매핑은 각 JSON에서 읽는다.**
  이 파일은 클래스 '이름' 기준의 그룹/위계/세부유형 키만 상수화한다.
- 세부유형(여닫이문/미닫이창/철근콘크리트벽 등)은 annotation.attributes 안에 있다.
"""
from __future__ import annotations

import os
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# 국가법령정보센터 Open API (법규 기계어 변환용)
#   OC = 신청 승인된 사용자 식별자. 개인 키이므로 환경변수 LAW_API_OC 우선,
#   없으면 아래 기본값(사용자 제공). 공개 법령 데이터 read-only.
# ─────────────────────────────────────────────────────────────────────────────
LAW_API_OC = os.environ.get("LAW_API_OC", "juchul_law_engine")
LAW_API_BASE = "https://www.law.go.kr/DRF"


def _envf(name: str, default: float) -> float:
    """임계값 환경변수 오버라이드(P2G_*). 서버서 튜닝을 코드수정·커밋 없이.
    예: export P2G_OPEN_MIN_RATIO=0.4 → 재빌드만으로 임계 변경."""
    try:
        return float(os.environ.get("P2G_" + name, default))
    except (TypeError, ValueError):
        return default

# ─────────────────────────────────────────────────────────────────────────────
# 경로
# ─────────────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
# 데이터 경로는 환경변수로 분리(노트북↔115서버 공용). 코드는 동일, 경로만 환경별.
#   PLAN2GRAPH_DATA : data/ 위치. 기본=프로젝트/data
#   PLAN2GRAPH_RAW  : AI-Hub 원본 ZIP 루트. 기본=data/raw/aihub/01-1.정식개방데이터
# 구조: data/raw/{aihub,cubicasa5k,rplan}(원본) → staging(작업) → releases(동결). 전부 gitignore.
DATA_DIR = Path(os.environ.get("PLAN2GRAPH_DATA", str(PROJECT_ROOT / "data")))
RAW_DIR = DATA_DIR / "raw"            # 원본 다운로드 한 지붕(출처별), gitignore
RAW_SOURCE_ROOT = Path(os.environ.get(
    "PLAN2GRAPH_RAW", str(RAW_DIR / "aihub" / "01-1.정식개방데이터")))
INTERIM_DIR = DATA_DIR / "interim"   # inventory.csv 등 중간 산출물
PROCESSED_DIR = DATA_DIR / "processed"  # (레거시, 은퇴) — staging/aihub로 대체

INVENTORY_CSV = INTERIM_DIR / "inventory.csv"

# ─────────────────────────────────────────────────────────────────────────────
# 라인별 분리 (ADR-0002): releases·runs를 Parsed/Corrected 하위폴더로 격리.
#   리졸버는 라인 하위폴더(parsed/corrected)를 먼저 찾고, 없으면 옛 flat 경로로 폴백.
#   → 데이터 물리 이전 전에는 flat로 동작(무변화), 이전 후엔 자동으로 새 위치 사용.
# ─────────────────────────────────────────────────────────────────────────────
RELEASES_DIR = DATA_DIR / "releases"
RUNS_DIR = PROJECT_ROOT / "runs"
DATASET_LINES = ("parsed", "corrected")        # T=위상(자동 detection→그래프), G=위상+기하(SVG→추출)


def dataset_line(version: str) -> str:
    """버전명 → 라인. 이미 이전됐으면 그 위치, 아니면 내용(geom.jsonl)·이름으로 추정."""
    v = str(version)
    if (RELEASES_DIR / "corrected" / v).exists():
        return "corrected"
    if (RELEASES_DIR / "parsed" / v).exists():
        return "parsed"
    if (RELEASES_DIR / v / "geom.jsonl").exists():     # 미이전 flat: 기하 데이터셋
        return "corrected"
    if v.startswith("g") and not v.startswith("global"):
        return "corrected"
    return "parsed"


def release_dir(version: str) -> Path:
    """버전 데이터 폴더 경로. 라인 하위폴더 우선 → 없으면 옛 flat 폴백(안 깨짐)."""
    for line in DATASET_LINES:
        p = RELEASES_DIR / line / version
        if p.exists():
            return p
    return RELEASES_DIR / version                       # 옛 flat


def run_dir(run_id: str) -> Path:
    """런(모델) 폴더 경로. 라인 하위폴더 우선 → 없으면 옛 flat 폴백."""
    for line in DATASET_LINES:
        p = RUNS_DIR / line / run_id
        if p.exists():
            return p
    return RUNS_DIR / run_id                            # 옛 flat


def release_write_dir(version: str) -> Path:
    """새 릴리스를 만들 폴더(라인 자동 결정) — 빌더·freeze가 사용."""
    return RELEASES_DIR / dataset_line(version) / version


def list_releases():
    """모든 릴리스 (version, line, path). 라인 하위폴더 + 옛 flat 스캔(manifest.json 보유만)."""
    out = {}
    for line in DATASET_LINES:
        d = RELEASES_DIR / line
        if d.is_dir():
            for p in sorted(d.iterdir()):
                if p.is_dir() and (p / "manifest.json").exists():
                    out[p.name] = (p.name, line, p)
    if RELEASES_DIR.is_dir():                              # 미이전 flat(라인 폴더에 없는 것만)
        for p in sorted(RELEASES_DIR.iterdir()):
            if (p.is_dir() and p.name not in DATASET_LINES
                    and p.name not in out and (p / "manifest.json").exists()):
                out[p.name] = (p.name, dataset_line(p.name), p)
    return list(out.values())


def run_line(run_id: str) -> str:
    """런 id → 라인. 기하 모델(geom*)=corrected, 그 외(gen* 등)=parsed."""
    return "corrected" if str(run_id).startswith("geom") else "parsed"


def run_write_dir(run_id: str) -> Path:
    """새 런(모델) 저장 폴더(라인 자동)."""
    return RUNS_DIR / run_line(run_id) / run_id


def iter_runs(pattern: str = "*"):
    """런 폴더들 — 라인 하위폴더 + 옛 flat (admin 진행보드 glob용)."""
    seen, res = set(), []
    for base in [RUNS_DIR / l for l in DATASET_LINES] + [RUNS_DIR]:
        if base.is_dir():
            for p in sorted(base.glob(pattern)):
                if p.is_dir() and p.name not in DATASET_LINES and p.name not in seen:
                    seen.add(p.name)
                    res.append(p)
    return res

# ─────────────────────────────────────────────────────────────────────────────
# 파일명 규칙: {주택3자}_{도면2자}_{라벨3자}_{9자리}.png|json
# ─────────────────────────────────────────────────────────────────────────────
HOUSE_TYPES = ("APT", "DEH", "ROW")        # 아파트 / 단독주택 / 연립다세대
DRAWING_TYPES = ("FP", "CS", "EP", "SD")   # 평면도 / 단면도 / 입면도 / 구조도
LABEL_TYPES = ("STR", "SPA", "OBJ", "OCR")  # 구조 / 공간 / 객체 / 문자

# 위상 그래프는 평면도(FP)에서만 의미가 있다.
TARGET_DRAWING_TYPE = "FP"

# ─────────────────────────────────────────────────────────────────────────────
# 클래스 분류 (이름 기준) — 실데이터 23-클래스 검증 결과
# ─────────────────────────────────────────────────────────────────────────────
# 공간(SPA) = 그래프 노드 후보
SPACE_CLASSES = (
    "공간_거실", "공간_침실", "공간_주방", "공간_현관", "공간_발코니",
    "공간_화장실", "공간_실외기실", "공간_드레스룸", "공간_다목적공간",
    "공간_엘리베이터홀", "공간_계단실", "공간_엘리베이터", "공간_기타",
    # ── 큐레이션 전용 공간(topoedit이 사람 손으로 신설; 검출 라벨엔 없음) ──
    #   반드시 끝에 append. YOLO V2V·생성모델은 위치=class id라 중간 삽입/재정렬 금지.
    #   전실: 현관과 별개 공간이지만 AI-Hub 원본은 전실을 전부 '현관'으로 라벨함
    #     (roomdist 기준 전실 클래스 0건, 현관=거실=7101). OCR 텍스트·공용부 노드도 없어
    #     자동 복원 거의 불가 → 사람 큐레이션으로만 분리, 도면 생성/렌더 단계에서 라벨오류 감안.
    #     구분 신호(도메인): 현관=짧고 중문 없음 / 전실=길고 중문(구조_출입문 내부문) 있음.
    "공간_복도", "공간_전실", "공간_파우더룸",
)
# 구조(STR)
STRUCTURE_CLASSES = ("구조_출입문", "구조_창호", "구조_벽체")
DOOR_CLASS = "구조_출입문"   # 그래프 엣지
WINDOW_CLASS = "구조_창호"   # 노드 속성(채광)
WALL_CLASS = "구조_벽체"
# 객체(OBJ) — 방 귀속·분류 교차검증용
OBJECT_CLASSES = (
    "객체_변기", "객체_세면대", "객체_싱크대", "객체_욕조", "객체_가스레인지",
)
# 기타
TEXT_CLASS = "OCR"
BACKGROUND_CLASS = "background"

# 외부 진입점으로 표시할 공간
ENTRANCE_CLASS = "공간_현관"

# 창호(구조_창호) 중 '통로'로 승격할 대상: 한쪽이 아래 클래스면 슬라이딩 출입로 본다.
#   근거: 발코니는 항상 미닫이창(슬라이딩)으로 드나드는 건축 사실(데이터 확인).
#   실외기실은 통상 발코니를 통해 접근 → 함께 포함. 그 외 창호는 채광(노드 속성)만.
WINDOW_PASSAGE_CLASSES = ("공간_발코니", "공간_실외기실")

# 개방통로(via:"open") — 문·발코니창 없이 벽이 끊긴 개구부로 연결된 방(개방형 LDK 등).
#   두 방 경계대(buffer 교집합)에서 구조_벽체가 덮지 않은 '열린' 면적 비율이 임계 이상이면 통로.
OPEN_MAX_GAP_PX = _envf("OPEN_MAX_GAP_PX", 60.0)    # 개방통로 최대 간격(px)
OPEN_MIN_RATIO = _envf("OPEN_MIN_RATIO", 0.30)      # 벽 미피복 면적 비율 임계

# 개방통로(via:open) 금지 타입쌍 — 건축적으로 직접 트일 수 없는 방쌍.
#   (게이트 측정: ratio 튜닝으로 안 걸러지는 과연결의 주원인. 침실끼리·침실-주방 등.)
#   정렬된 (클래스, 클래스) 집합. 문(라벨 근거)에는 적용 안 함, 추론 open에만.
def _pair(a, b):
    return tuple(sorted(("공간_" + a, "공간_" + b)))
OPEN_FORBIDDEN_PAIRS = frozenset([
    _pair("침실", "침실"), _pair("침실", "주방"), _pair("침실", "실외기실"),
    _pair("침실", "현관"), _pair("주방", "화장실"), _pair("화장실", "화장실"),
    _pair("주방", "주방"), _pair("화장실", "실외기실"), _pair("드레스룸", "실외기실"),
    _pair("드레스룸", "주방"), _pair("현관", "실외기실"), _pair("현관", "주방"),
])

# ─────────────────────────────────────────────────────────────────────────────
# 노이즈/비주거 공간 필터 — 세대 그래프 노드에서 제외
#   코어: 엘리베이터·계단실 등 건물 공용부(주거 아님). 면적과 무관히 제외.
#   기타 노이즈: 공간_기타 중 작은 단편(벽두께·자투리). 면적 하한 미만 제외.
#     (실측: 기타 중앙값 ~5,600px², 실제 방은 ≥~8,000px²이고 모두 다른 클래스라
#      이 임계는 기타에만 적용해도 실제 방을 떨어뜨리지 않는다.)
# ─────────────────────────────────────────────────────────────────────────────
CORE_CLASSES = ("공간_엘리베이터", "공간_엘리베이터홀", "공간_계단실")
ETC_CLASS = "공간_기타"
MIN_ETC_AREA_PX = _envf("MIN_ETC_AREA_PX", 10000.0)

# ─────────────────────────────────────────────────────────────────────────────
# 면적 의존 법규 수치 (scale 확보분에만 적용). ⚠️ 전문가 확인 필요(docs/ROADMAP.md §8 열린 질문).
#   채광: 피난·방화규칙 §17① — 창 면적 ≥ 거실·침실 바닥의 1/10.
#     라벨엔 창 '폭'만 있어 면적은 폭×추정높이로 근사(보수적).
#   침실 최소면적·세대 최소면적: 관행/최저주거기준 고시 참고값(확정 전).
# ─────────────────────────────────────────────────────────────────────────────
LEGAL_DAYLIGHT_RATIO = _envf("LEGAL_DAYLIGHT_RATIO", 0.10)   # 채광창/바닥 하한
WINDOW_EST_HEIGHT_M = _envf("WINDOW_EST_HEIGHT_M", 1.2)      # 창 높이 추정(m)
LEGAL_BEDROOM_MIN_M2 = _envf("LEGAL_BEDROOM_MIN_M2", 7.0)    # 침실 최소면적(㎡) 참고값
LEGAL_MIN_DWELLING_M2 = _envf("LEGAL_MIN_DWELLING_M2", 14.0)  # 세대 최소면적(㎡)

# ─────────────────────────────────────────────────────────────────────────────
# 데이터셋 채택 기준 — '완벽한 세대'만 채택, 나머지는 격리(quarantine)해 후일 검토
#   완벽한 주거 = 살림이 가능한 필수 5요소를 모두 갖춘 단일 연결 세대.
#   (개방형 LDK 트임 미연결로 조각난 세대는 필수요소 결손으로 자동 격리된다.)
# ─────────────────────────────────────────────────────────────────────────────
ESSENTIAL_ROOM_CLASSES = (
    "공간_현관", "공간_거실", "공간_침실", "공간_주방", "공간_화장실",
)
ACCEPT_MIN_ROOMS = 5      # 필수 5요소 → 최소 5방
ACCEPT_MAX_ROOMS = 60     # 비정상 병합/다세대 잔류 방지 상한

# ─────────────────────────────────────────────────────────────────────────────
# 공간 위계 (Public / Private / Service) — 온톨로지·프라이버시 속성
# ─────────────────────────────────────────────────────────────────────────────
HIERARCHY = {
    "공간_거실": "public",
    "공간_현관": "public",
    "공간_침실": "private",
    "공간_드레스룸": "private",
    "공간_화장실": "private",
    "공간_주방": "service",
    "공간_발코니": "service",
    "공간_실외기실": "service",
    "공간_계단실": "service",
    "공간_엘리베이터홀": "service",
    "공간_엘리베이터": "service",
    "공간_다목적공간": "service",
    "공간_기타": "service",
    # 큐레이션 전용 공간
    "공간_복도": "service",       # 순환/연결 공간
    "공간_전실": "service",       # 안방 등 앞의 연결 공간
    "공간_파우더룸": "private",   # 화장실 계열(사적)
}

# ─────────────────────────────────────────────────────────────────────────────
# 세부유형(attributes) 키 — 실데이터 검증값
#   주의: 창호의 attribute 키는 '구조_창호'가 아니라 '창호'다.
# ─────────────────────────────────────────────────────────────────────────────
SUBTYPE_ATTR_KEYS = ("구조_출입문", "구조_창호", "창호", "구조_벽체")
OCR_ATTR_KEY = "OCR"

# ─────────────────────────────────────────────────────────────────────────────
# 임계값 (튜닝 대상 — 1장/20장 게이트에서 조정, 변경 시 NOTES.md 기록)
# ─────────────────────────────────────────────────────────────────────────────
# 문 폴리곤을 확장해 인접 공간을 탐색할 때 쓰는 buffer(픽셀). 문 두께에 비례.
DOOR_BUFFER_PX = 30.0
# 문 법선 방향 probe 거리(픽셀) — 문을 사이에 둔 양쪽 공간 내부를 찌르는 거리.
DOOR_PROBE_DIST_PX = 40.0
# 문-공간 후보로 인정할 최대 거리(픽셀).
DOOR_MAX_GAP_PX = 60.0

# pixel→실측(㎡) 변환 계수. 현재 건축개요 CSV(scale) 미확보 → None.
# 확보 시 도면별 scale을 주입한다. None이면 면적은 픽셀² 단위로 통과.
DEFAULT_SCALE = None

# 데이터셋 분할 비율
SPLIT_RATIOS = {"train": 0.8, "val": 0.1, "test": 0.1}
SPLIT_SEED = 42
