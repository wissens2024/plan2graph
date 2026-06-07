"""헤드리스 Streamlit 스모크 — 위상 편집 화면을 AppTest로 실제 실행, 예외/위젯 검증."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def app() -> None:
    from plan2graph import topoedit
    topoedit.render_editor()


def main() -> int:
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_function(app, default_timeout=90)
    at.run()
    if at.exception:
        print("❌ UI 예외 발생:")
        for e in at.exception:
            print("  ", e)
        return 1
    print(f"[title]      {[t.value for t in at.title]}")
    print(f"[widgets]    selectbox={len(at.selectbox)} multiselect={len(at.multiselect)} "
          f"button={len(at.button)} text_input={len(at.text_input)}")
    print(f"[sidebar]    {[s.label for s in at.sidebar.selectbox]}")
    # 핵심 위젯이 실제로 떴는지
    assert at.title, "title 없음 — 화면 미렌더"
    assert len(at.selectbox) >= 3, "편집 컨트롤(연결공간/엣지/역할) 미렌더"
    print("✅ UI 스모크 PASS — 화면 예외 없이 렌더, 편집 위젯 정상")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
