"""국가법령정보센터 Open API 클라이언트 (법규 기계어 변환 근거 수집).

사업계획서 1단계 "법규의 기계어 변환 — 국가법령정보센터 공개 API 활용".
법령을 검색·조회해 규칙 DB의 '근거 조문'을 자동 수집·캐시한다.

- 검색:  lawSearch.do?OC&target=law&query=...  → 법령명/MST 목록
- 본문:  lawService.do?OC&target=law&MST=...&type=XML → 조문 단위 전문
- 캐시:  data/interim/law_cache/ (재요청 최소화, 오프라인 재현)

OC는 config.LAW_API_OC(환경변수 LAW_API_OC 우선).
"""
from __future__ import annotations

import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402

CACHE = config.INTERIM_DIR / "law_cache"


def _get(url: str, cache_key: str, timeout: int = 60, force: bool = False) -> str:
    """URL GET(UTF-8) + 디스크 캐시. force=True면 캐시 무시하고 재조회."""
    CACHE.mkdir(parents=True, exist_ok=True)
    cp = CACHE / cache_key
    if cp.exists() and not force:
        return cp.read_text(encoding="utf-8")
    req = urllib.request.Request(url, headers={"User-Agent": "plan2graph/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read().decode("utf-8", errors="replace")
    cp.write_text(data, encoding="utf-8")
    return data


def search_law(query: str, force: bool = False) -> list[dict]:
    """법령명 검색 → [{name, mst, law_id, 시행일자}]. force=True면 현행 재조회."""
    import hashlib
    q = urllib.parse.quote(query)
    url = (f"{config.LAW_API_BASE}/lawSearch.do?OC={config.LAW_API_OC}"
           f"&target=law&type=XML&query={q}")
    key = "search_" + hashlib.md5(query.encode("utf-8")).hexdigest()[:16] + ".xml"
    xml = _get(url, key, force=force)
    out = []
    for blk in re.split(r"(?=<law\b)", xml)[1:]:
        name = re.search(r"<법령명한글><!\[CDATA\[(.*?)\]\]>", blk)
        mst = re.search(r"<법령일련번호>(\d+)</법령일련번호>", blk)
        lid = re.search(r"<법령ID>(\d+)</법령ID>", blk)
        eff = re.search(r"<시행일자>(\d+)</시행일자>", blk)
        if name and mst:
            out.append({"name": name.group(1), "mst": mst.group(1),
                        "law_id": lid.group(1) if lid else None,
                        "효력시행": eff.group(1) if eff else None})
    return out


def get_law_xml(mst: str) -> str:
    """법령 본문 XML 전체."""
    url = (f"{config.LAW_API_BASE}/lawService.do?OC={config.LAW_API_OC}"
           f"&target=law&MST={mst}&type=XML")
    return _get(url, f"law_{mst}.xml")


def articles(mst: str) -> list[dict]:
    """법령 본문 → [{조문번호, 제목, 본문텍스트}] (조문 단위)."""
    xml = get_law_xml(mst)
    out = []
    for blk in re.split(r"(?=<조문단위)", xml)[1:]:
        no = re.search(r"<조문번호>(.*?)</조문번호>", blk)
        title = re.search(r"<조문제목><!\[CDATA\[(.*?)\]\]>", blk)
        # 조문내용 CDATA들을 모아 평문화
        body = " ".join(re.findall(r"<!\[CDATA\[(.*?)\]\]>", blk))
        body = re.sub(r"\s+", " ", body).strip()
        out.append({"no": no.group(1) if no else None,
                    "title": title.group(1) if title else None,
                    "text": body})
    return out


def find_article(mst: str, keyword: str) -> list[dict]:
    """제목/본문에 keyword 포함 조문 검색."""
    return [a for a in articles(mst)
            if (a["title"] and keyword in a["title"]) or keyword in a["text"]]


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(f"OC = {config.LAW_API_OC}")
    for law in search_law("건축법 시행령")[:3]:
        print(law)
    print("\n채광 조문(건축법 시행령):")
    res = search_law("건축법 시행령")
    if res:
        for a in find_article(res[0]["mst"], "채광")[:2]:
            print(f"  제{a['no']}조 {a['title']}: {a['text'][:200]}")
