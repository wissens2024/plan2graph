"""역할 어휘 정합 테스트 — 사람이 쓰는 이름이 토큰에서 조용히 '기타'로 떨어지지 않는지.

왜 있나: `wallcycle_codec.ROLES`에 `펜트리` 키가 없어서 `ROLE2ID.get(role, ROLE_OTHER)`가
보정된 펜트리 ~1.1만 건을 전부 `기타`로 떨어뜨리고 있었다(2026-11-09 수정,
EXPERIMENTS §3-③). 어휘가 또 갈라지면 재학습이 조용히 망가지므로 테스트로 못 박는다.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from plan2graph import geomgraph, topoedit  # noqa: E402
from plan2graph import wallcycle_codec as wc  # noqa: E402


def test_pantry_spellings_share_one_token():
    assert wc.role_id("펜트리") == wc.role_id("팬트리") != wc.ROLE_OTHER


def test_alias_targets_exist():
    """alias 오른쪽은 반드시 실제 어휘여야 한다(오타 방지)."""
    for src, dst in wc.ROLE_ALIAS.items():
        assert dst in wc.ROLE2ID, "alias %s→%s: 대상이 어휘에 없다" % (src, dst)
        assert src not in wc.ROLE2ID, "alias %s: 어휘에 중복으로 들어 있다" % src


def test_human_vocabularies_are_covered():
    """에디터·topoedit·geomgraph가 쓰는 이름 전부가 토큰으로 살아야 한다."""
    known = set(wc.ROLES) | set(wc.ROLE_ALIAS)
    for name, roles in (("topoedit.ROLES", topoedit.ROLES),
                        ("topoedit.DRAW_BASES", topoedit.DRAW_BASES),
                        ("geomgraph.PRIVACY", geomgraph.PRIVACY)):
        missing = sorted(set(roles) - known)
        assert not missing, "%s의 %s 가 코덱 어휘에 없다 → 기타로 증발" % (name, missing)


def test_role_ids_are_stable():
    """토큰 id = 리스트 위치. 재정렬·삽입하면 기존 ckpt가 깨진다."""
    assert wc.ROLES[:4] == ["거실", "안방", "침실", "주방"]
    assert wc.ROLE2ID["기타"] == 20 and wc.ROLE_OTHER == 20
    assert wc.ROLE2ID["펜트리"] == 23


def test_unknown_still_falls_back():
    assert wc.role_id("없는역할") == wc.ROLE_OTHER
    assert wc.role_id(None) == wc.ROLE_OTHER
    assert wc.role_id(" 펜트리 ") == wc.ROLE2ID["펜트리"]
