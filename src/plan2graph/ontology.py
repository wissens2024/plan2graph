"""Task 1-4 — 공간 온톨로지 (owlready2 OWL 스키마 + 그래프 적재).

원칙(README §1-d): 추상화 금지, **실데이터 23클래스에 1:1 매핑**.
- Space 하위에 각 공간 클래스(거실/침실/…)를 그대로 둔다.
- 위계 카테고리(Public/Private/Service)는 별도 마커 클래스로 다중상속.
- 관계: connectedTo(문/발코니 통로, 대칭), entranceTo(현관→외부).
- 속성: areaPx, isEntrance, nWindows.
적재: schema.serialize 레코드(layout) → 온톨로지 개체로 인스턴스화.

owlready2 미설치 환경에서도 import는 되도록(런타임에만 필요) 지연 import 사용.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import config  # noqa: E402

IRI = "http://plan2graph.org/floorplan.owl"

# 위계 카테고리 → 마커 클래스명
HIER_CLASS = {"public": "PublicSpace", "private": "PrivateSpace",
              "service": "ServiceSpace"}


def _ascii_name(korean_class: str) -> str:
    """'공간_거실' → 'Space_거실' (owlready 클래스명; 유니코드 식별자 허용)."""
    return korean_class.replace("공간_", "Space_").replace("구조_", "Struct_")


def build_ontology():
    """OWL 스키마 생성 → owlready2 ontology 객체 반환."""
    from owlready2 import Thing, ObjectProperty, SymmetricProperty, \
        DataProperty, FunctionalProperty, get_ontology
    import types as _t

    onto = get_ontology(IRI)
    with onto:
        class Space(Thing):
            pass

        class Exterior(Thing):  # 외부(가상 진입점)
            pass

        # 위계 마커 클래스
        for hc in HIER_CLASS.values():
            _t.new_class(hc, (Space,))

        # 23 공간 클래스 1:1 + 위계 다중상속
        space_classes = {}
        for kclass in config.SPACE_CLASSES:
            hier = config.HIERARCHY.get(kclass)
            bases = (Space,)
            if hier and HIER_CLASS[hier] in onto.__dict__:
                bases = (onto[HIER_CLASS[hier]],)
            cls = _t.new_class(_ascii_name(kclass), bases)
            space_classes[kclass] = cls

        # 관계
        class connectedTo(Space >> Space, SymmetricProperty):
            pass

        class entranceTo(ObjectProperty):
            domain = [Space]
            range = [Exterior]

        # 속성
        class areaPx(DataProperty, FunctionalProperty):
            domain = [Space]
            range = [float]

        class isEntrance(DataProperty, FunctionalProperty):
            domain = [Space]
            range = [bool]

        class nWindows(DataProperty, FunctionalProperty):
            domain = [Space]
            range = [int]

    onto._space_classes = space_classes  # 적재 시 참조용
    return onto


def populate_from_record(onto, record: dict):
    """schema 레코드(layout) → 온톨로지 개체 인스턴스화 후 onto 반환."""
    from owlready2 import Thing
    space_classes = getattr(onto, "_space_classes", {})
    gid = record["graph_id"]
    ext = onto.Exterior(f"{gid}__exterior")
    node_ind = {}
    with onto:
        for nd in record["layout"]["nodes"]:
            if nd.get("type") == "exterior":
                node_ind["exterior"] = ext
                continue
            kclass = "공간_" + nd["type"] if not nd["type"].startswith("공간_") else nd["type"]
            cls = space_classes.get(kclass, onto.Space)
            ind = cls(f"{gid}__n{nd['id']}")
            if nd.get("area_px2") is not None:
                ind.areaPx = float(nd["area_px2"])
            ind.isEntrance = bool(nd.get("is_entrance"))
            ind.nWindows = int(nd.get("n_windows", 0))
            node_ind[nd["id"]] = ind

        for e in record["layout"]["edges"]:
            a = node_ind.get(e["source"])
            b = node_ind.get(e["target"])
            if a is None or b is None:
                continue
            if e["via"] in ("door", "balcony"):
                a.connectedTo.append(b)
            elif e["via"] in ("entrance", "exterior_door"):
                if b is ext:
                    a.entranceTo.append(ext)
                elif a is ext:
                    b.entranceTo.append(ext)
    return onto


def save(onto, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    onto.save(file=str(path), format="rdfxml")
    return path


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import json
    onto = build_ontology()
    n_cls = len(list(onto.classes()))
    print(f"온톨로지 생성: 클래스 {n_cls}개 (IRI {IRI})")
    # 채택 그래프가 있으면 1개 적재 시험
    g = sorted((config.PROCESSED_DIR / "graphs").glob("*.json"))
    if g:
        rec = json.loads(g[0].read_text(encoding="utf-8"))
        populate_from_record(onto, rec)
        print(f"  '{rec['graph_id']}' 적재: 개체 {len(list(onto.individuals())):,}개")
        out = save(onto, ROOT / "ontology" / "floorplan_sample.owl")
        print(f"  저장: {out}")
    else:
        out = save(onto, ROOT / "ontology" / "floorplan.owl")
        print(f"  스키마 저장: {out}")
