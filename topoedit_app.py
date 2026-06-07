"""독립 미리보기 진입점 — 위상 편집기만 단독 streamlit 실행(서버 검수용).

  streamlit run topoedit_app.py
기존 admin.py와 분리. 검수 후 admin 메뉴 통합은 별도(git 동기화 정리 후).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from plan2graph import topoedit  # noqa: E402

topoedit.render_editor()
