"""한국형 아파트 도면 교정 룰 (hand-curated) — 데이터(알바 라벨) 오류 보정 참조.

왜 별도 모듈인가:
  AI-Hub 원본 라벨은 외주 작업자(알바)가 단 것이라 **체계적 오류**가 있다. 예) 전실을
  전부 '현관'으로 라벨([[vestibule-vs-entrance-mislabel]]). 이런 오류는 코퍼스 통계로
  자동 채굴(constraints.py)되지 않는다 — 틀린 라벨이 다수라 통계도 틀리기 때문. 오직
  한국 아파트 **도메인 지식**으로만 잡힌다. 그 지식을 여기 한 곳에 데이터로 모은다.

룰 위치 분담:
  - constraints.py  : 코퍼스 통계 채굴 룰        (source: mined)
  - rules_legal.py  : 강행 법규 DB               (source: legal)
  - rules.py        : 위상 무결성 R1~R5(실행기)   (integrity)
  - **rules_arch.py**: 건축 관행/도메인 룰         (source: arch)  ← 본 모듈

도면 교정(geom_correct·gen_loop·topoedit)이 이 카탈로그를 참조해 라벨오류를 감지·보정한다.
각 룰 = 데이터 한 줄(무엇/왜/신호/교정). 실행 엔진(감독기)은 후속 — 여기선 정의가 진실원본.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ArchRule:
    id: str
    name: str
    kind: str                 # integrity | motif | relabel | adjacency | geometry
    correct_pattern: str      # 한국 아파트의 실제 모습(정답)
    labeler_error: str        # 알바가 내는 체계적 라벨 오류(감지 대상)
    signal: str               # 그래프/기하/구조 신호로 어떻게 탐지하나
    fix: str                  # 교정 동작
    severity: str             # high | med | low
    refs: list[str] = field(default_factory=list)  # 교차참조(다른 룰/메모/모듈)
    source: str = "arch"
    evidence: str = ""        # 코퍼스 측정 근거(있으면 '증거 기반', 없으면 '도메인 가설')


# ── 한국형 아파트 도면 교정 룰 카탈로그 ──
#    추가 시 id 안정 유지(교정 로그·테스트가 id로 참조). 신호/교정은 후속 엔진이 구현.
RULES: list[ArchRule] = [
    ArchRule(
        id="A1_no_room_through_room",
        name="룸 지나 룸 금지 · 방문 없는 방 금지",
        kind="integrity",
        correct_pattern=(
            "모든 방은 문으로 직접 접근된다. 세대 내 순환은 거실·복도·현관·전실 등 "
            "공용/연결공간을 경유하며, 사적 방(침실)을 통과해야만 닿는 방은 없다."),
        labeler_error=(
            "알바가 문을 누락하거나 방-방을 직결로 라벨 → 문 없는 고립 방, 또는 "
            "침실을 지나야 다른 방에 닿는 비정상 동선."),
        signal=(
            "rules.py R2(degree 0=문없음)·R3(현관 도달불가)로 1차 탐지. 추가로 사적공간"
            "(침실/안방)이 다른 방의 '유일 경로'인지(서브그래프 betweenness/cut-vertex) 검사."),
        fix="누락 문 복원, 또는 연결공간(복도·전실) 노드를 삽입해 사적공간 통과를 우회.",
        severity="high",
        refs=["rules.check_integrity:R2", "rules.check_integrity:R3"],
    ),
    ArchRule(
        id="A2_master_suite_motif",
        name="안방 스위트 모티프(파우더룸 허브)",
        kind="motif",
        correct_pattern=(
            "안방 → (오픈형)파우더룸 → 드레스룸 + 전용욕실(화장실). 파우더룸이 허브로서 "
            "드레스룸·전용욕실을 잇는다. 드레스룸·전용욕실이 안방에 직결되지 않는다."),
        labeler_error=(
            "알바가 파우더룸/전실을 누락하거나 현관·기타로 라벨, 드레스룸·전용욕실을 "
            "안방에 바로 붙여 라벨 → 허브 공간 소실."),
        signal=(
            "안방 인접에 파우더룸이 없는데 드레스룸·욕실이 안방에 직결돼 있으면 허브 누락 의심. "
            "안방-드레스룸-욕실 삼각이 한 곳에 모여 있으면 그 중심이 파우더룸 후보."),
        fix="파우더룸 노드를 매개로 재구성(안방-파우더룸-{드레스룸,전용욕실}), 욕실을 전용으로 표시.",
        severity="med",
        refs=["[[canonical-kr-apartment-topology]]"],
        evidence=("v0 5,648 측정: 드레스룸 보유 80.5%, 그 중 침실+화장실 둘다 직결 81.2% "
                  "(스위트 클러스터 실재) — 그러나 중간 허브는 9.1%뿐(파우더룸 붕괴 확인)."),
    ),
    ArchRule(
        id="A3_long_entrance_is_vestibule",
        name="긴 현관 + 중문 = 전실(현관 오라벨)",
        kind="relabel",
        correct_pattern=(
            "현관 = 짧고 중문 없음(세대 안쪽 신발 공간). "
            "전실 = 길고 중문(구조_출입문 중 내부문) 있음(신축 고층 多·구축 無)."),
        labeler_error=(
            "AI-Hub 원본은 전실을 전부 '현관'으로 라벨(전실 클래스 0건). 데이터 자체의 "
            "체계적 오류라 보정 전량은 불가 — 도면 생성/렌더 단계에서 감안."),
        signal=(
            "'현관' 노드가 종횡비/길이가 비정상으로 크고, 내부에 중문(구조_출입문 내부문)을 "
            "끼면 실제 전실. 짧고 중문 없으면 진짜 현관."),
        fix="role 현관→전실 재지정(또는 전실+현관 두 노드로 분리). 자동 불가 시 후보로 표시.",
        severity="med",
        refs=["[[vestibule-vs-entrance-mislabel]]", "config.SPACE_CLASSES:공간_전실"],
        evidence=("그래프 코퍼스로는 측정 불가 — 노드에 area·centroid만 있고 폴리곤/종횡비 없음. "
                  "'긴 현관' 신호는 형상이 있는 렌더/기하 단계에서만 검증·적용 가능(graph 단계 아님)."),
    ),
    ArchRule(
        id="A4_hvac_on_balcony",
        name="실외기실은 발코니에 붙는다",
        kind="adjacency",
        correct_pattern=(
            "실외기실은 발코니를 통해 접근·환기되는 외기 인접 공간. 거의 항상 발코니에 인접."),
        labeler_error=(
            "실외기실을 발코니 아닌 방(주방/거실)에 직결로 라벨하거나 발코니 연결을 누락."),
        signal="실외기실 노드가 어떤 발코니에도 인접하지 않으면 위반.",
        fix="가장 가까운(또는 임의) 발코니에 창호통로(via=balcony)로 연결.",
        severity="med",
        refs=["config.WINDOW_PASSAGE_CLASSES"],
        evidence=("v0 5,648 측정: 실외기실 이웃 발코니 4,011 vs 다음(주방) 486 — 8배 압도. "
                  "현관 인접 3건뿐."),
    ),
    ArchRule(
        id="A5_balcony_extension",
        name="발코니 확장/미확장 구분",
        kind="geometry",
        correct_pattern=(
            "미확장: 방│샷시(창호)│발코니(별도 타일 공간). "
            "확장: 벽·샷시 제거, 발코니가 방에 흡수, 옛 벽선만 점선으로 남음. 신축 대부분 확장."),
        labeler_error=(
            "확장/미확장이 라벨에 없어 추론 필요. 완전 흡수된 발코니는 노드가 사라져 그래프서 안 보임."),
        signal=(
            "그래프: 방↔발코니 edge via — balcony(샷시)=미확장 / open(벽끊김)=확장. "
            "기하: 발코니 노드 없는데 방이 깊고 옛 벽선 있으면 흡수형 확장."),
        fix=(
            "생성: 확장 정책 기본(via=open, 노드 extended=True; 렌더서 발코니 흡수+옛벽선 점선). "
            "미확장 시 샷시 경계 유지(via=balcony). 교정: 방-발코니 via를 정책에 맞게 정규화."),
        severity="med",
        refs=["topology.py:via=balcony|open", "[[geometry-realization-is-bottleneck]]"],
        evidence=(
            "코드 확정: via=balcony=창호검출(미확장)/open=벽30%끊김(확장). "
            "v0 측정 방-발코니: balcony 13,823 / open 1,068 / door 3,295. "
            "단 흡수형은 노드소실로 미관측(미확장 과대). 로컬 원본 1장 육안=미확장(샷시 확인)."),
    ),
]

# id → 룰 (교정기·테스트 참조용)
BY_ID: dict[str, ArchRule] = {r.id: r for r in RULES}


def by_kind(kind: str) -> list[ArchRule]:
    return [r for r in RULES if r.kind == kind]


# ─────────────────────────────────────────────────────────────────────────────
# 실행 엔진 — 그래프에 arch 룰 적용 (gen_loop의 verify/생성 후처리가 호출).
#   그래프 단계에서 실행 가능한 룰만(A2·A4·A5). 형상 단계 신호(A3·A5 흡수형)는 렌더에서.
# ─────────────────────────────────────────────────────────────────────────────
_BED = ("침실", "안방")
_LIVING = ("거실", "침실", "안방")


def _ntype(G, n):
    return G.nodes[n].get("type")


def check_arch(G) -> list[dict]:
    """arch 룰 위반 탐지(verify용). 그래프 실행 가능 룰만."""
    V = []
    for n, d in G.nodes(data=True):
        t = d.get("type")
        if t == "실외기실":                                   # A4
            if not any(_ntype(G, m) == "발코니" for m in G.neighbors(n)):
                V.append({"rule": "A4_hvac_on_balcony", "node": n,
                          "msg": "실외기실이 발코니에 인접하지 않음"})
        elif t == "드레스룸":                                 # A2
            nb = {_ntype(G, m) for m in G.neighbors(n)}
            if (nb & set(_BED)) and "화장실" in nb and "파우더룸" not in nb:
                V.append({"rule": "A2_master_suite_hub", "node": n,
                          "msg": "드레스룸이 침실·화장실에 직결(파우더룸 허브 없음)"})
    return V


def apply_arch(G, balcony_extend: bool = True) -> list[str]:
    """arch 룰 교정/정책을 그래프에 적용(생성 후처리). 적용 내역 반환.
    balcony_extend=True → 발코니 확장 기본(요즘 아파트). 발코니 노드에 extended 표시."""
    fixes = []
    # A5: 발코니 확장/미확장 정책 — 방↔발코니 via 정규화 + 노드 표시
    for n, d in G.nodes(data=True):
        if d.get("type") != "발코니":
            continue
        d["extended"] = bool(balcony_extend)
        for m in list(G.neighbors(n)):
            if _ntype(G, m) in _LIVING:
                G[n][m]["via"] = "open" if balcony_extend else "balcony"
                G[n][m]["door_type"] = None if balcony_extend else "미닫이창"
        fixes.append(f"A5:발코니#{n} {'확장(open)' if balcony_extend else '미확장(샷시)'}")
    # A4: 실외기실을 발코니에 연결(없으면)
    bals = [n for n, dd in G.nodes(data=True) if dd.get("type") == "발코니"]
    if bals:
        for n, d in G.nodes(data=True):
            if d.get("type") == "실외기실" and \
                    not any(_ntype(G, m) == "발코니" for m in G.neighbors(n)):
                G.add_edge(n, bals[0], via="balcony", door_type="미닫이창")
                fixes.append(f"A4:실외기실#{n}→발코니#{bals[0]} 연결")
    # A2: 파우더룸 허브 삽입(침실-드레스룸-욕실 직결 삼각 → 허브 경유로 재배선)
    for n in [x for x, dd in list(G.nodes(data=True)) if dd.get("type") == "드레스룸"]:
        nb = {m: _ntype(G, m) for m in G.neighbors(n)}
        if any(t == "파우더룸" for t in nb.values()):
            continue
        beds = [m for m, t in nb.items() if t in _BED]
        baths = [m for m, t in nb.items() if t == "화장실"]
        if not (beds and baths):
            continue
        bed, bath = beds[0], baths[0]
        pid = max([x for x in G.nodes if isinstance(x, int)], default=-1) + 1
        G.add_node(pid, type="파우더룸", hierarchy="private",
                   is_entrance=False, centroid=None, n_windows=0)
        G.add_edge(pid, bed, via="open")
        G.add_edge(pid, n, via="open")          # 파우더룸-드레스룸
        G.add_edge(pid, bath, via="door")       # 파우더룸-전용욕실
        for a, b in ((n, bath), (bed, n), (bed, bath)):   # 직결 단축 제거 → 허브 경유
            if G.has_edge(a, b):
                G.remove_edge(a, b)
        fixes.append(f"A2:파우더룸#{pid} 허브 삽입(침실#{bed}·드레스룸#{n}·욕실#{bath})")
    return fixes


def apply_arch_program(rooms, edges, balcony_extend: bool = True):
    """Corrected용 어댑터 — (rooms[(role,area,nwin)], edges[(i,j)]) 표현에 arch 룰 적용.
    apply_arch(그래프)를 그대로 재사용(룰 로직 단일소스, Parsed과 동일). A2가 방을 신설하면
    rooms에 append. 반환: (new_rooms, new_edges, fixes)."""
    import networkx as nx
    G = nx.Graph()
    for i, r in enumerate(rooms):
        G.add_node(i, type=r[0], n_windows=r[2], _area=r[1])
    for a, b in edges:
        G.add_edge(int(a), int(b), via="door")
    fixes = apply_arch(G, balcony_extend=balcony_extend)
    new_rooms = list(rooms)
    for n in sorted(x for x in G.nodes if isinstance(x, int) and x >= len(rooms)):
        d = G.nodes[n]
        new_rooms.append((d.get("type"), d.get("_area", 0.05), d.get("n_windows", 0)))
    new_edges = [(int(a), int(b)) for a, b in G.edges]
    return new_rooms, new_edges, fixes


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(f"한국형 아파트 교정 룰 {len(RULES)}개\n")
    for r in RULES:
        print(f"[{r.id}] ({r.kind}/{r.severity}) {r.name}")
        print(f"   정답 : {r.correct_pattern}")
        print(f"   오류 : {r.labeler_error}")
        print(f"   신호 : {r.signal}")
        print(f"   교정 : {r.fix}")
        print(f"   근거 : {r.evidence or '(미측정 — 도메인 가설)'}")
        if r.refs:
            print(f"   참조 : {', '.join(r.refs)}")
        print()
