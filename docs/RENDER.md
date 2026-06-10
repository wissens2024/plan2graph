# RENDER — 도면 렌더링 (생성형 SVG + AutoCAD DXF) · 자기교정 루프

> 목표: **위상도면(버블 다이어그램)은 산출물이 아니다.** 사용자가 필요로 하는 두 가지만 낸다.
> 1. **생성형 도면(SVG/PNG)** — 화면에서 바로 보는 도면(CAD 프로그램 불필요).
> 2. **AutoCAD 도면(DXF)** — 건축 사무소가 열어 편집하는 작업도면.
>
> 두 산출물은 **같은 기하에서 렌더만 다름**. **T·G 공용 렌더 코어**, 단 **G-라인 먼저**.
> 렌더 전 **검사→고치기→재검사 자기교정 루프**를 동일 패턴으로 적용([[geometry-realization-is-bottleneck]] 대응).

---

## 0. 파이프라인
```
(G) geomgraph(g-0.3)  ─┐
                        ├→ to_geometry() → Geometry ─[verify→fix 루프]→ clean Geometry ─┬→ render_svg → SVG/PNG
(T) floorgeom(treemap) ─┘  (어댑터, 스키마 분리 유지·ADR-0002)                            └→ render_dxf → DXF
```
- **Geometry** = 렌더 공통 입력(정규화). T·G 어댑터가 각자 채움 → 코어는 1벌.
- 위상도면 렌더는 만들지 않는다(폐기).

---

## 1. 공통 Geometry (렌더 입력 계약)
```python
@dataclass
class RoomG:   id; role; polygon: list[(x,y)]; area_m2; label_pt: (x,y); fixtures: list[FixtureG]
@dataclass
class WallG:   seg: ((x1,y1),(x2,y2)); type: "interior|exterior"; openings: list[str]  # door/window id
@dataclass
class DoorG:   pos:(x,y); on_wall; hinge:(x,y)|None; swing_deg:float|None; width_px; is_entrance:bool
@dataclass
class WindowG: pos:(x,y); on_wall; width_px; orient_deg
@dataclass
class FixtureG:cls: "객체_*"; bbox:(x,y,w,h)
@dataclass
class Geometry:
    plan_id; house; scale_mm_per_px; bbox:(x,y,w,h)
    rooms: list[RoomG]; walls: list[WallG]; doors: list[DoorG]; windows: list[WindowG]
    issues: list[str] = []      # verify가 채움
```

### 어댑터 (G 먼저)
```python
def from_geomgraph(g: dict) -> Geometry:
    # rooms[*].polygon/role/area_m2/fixtures, walls[*].segment/type/openings,
    # doors[*].position/on_wall/orientation(hinge,swing_dir_deg)/width_px/is_entrance,
    # windows[*].position/on_wall/width_px/orientation_deg, scale_mm_per_px, bbox_px
    # → Geometry 그대로 매핑(이미 g-0.3에 다 있음)

def from_floorgeom(layout: dict) -> Geometry:   # T-라인(나중) — treemap 사각형→방, 벽=사각경계, 문=엣지 근사
    ...
```

---

## 2. 자기교정 루프 (검사→고치기→재검사, 동일 패턴)
> [[geometry-realization-is-bottleneck]]: 도면이 깨지는 진짜 원인은 배치/정합. 렌더 직전 정합을 강제한다.
> 패턴은 기존 `geom_correct`의 verify→correct 루프와 **동일**. 단 G는 *실측 기하 정리*(스냅·정렬·폐합),
> T는 *treemap 재배치*(기존 geom_correct.correct).

```python
def autocorrect(geom: Geometry, max_iter=5) -> Geometry:
    for _ in range(max_iter):
        issues = verify(geom)          # 검사
        if not issues: break
        geom = fix(geom, issues)       # 고치기
    geom.issues = verify(geom)         # 잔여(렌더에 경고 표시)
    return geom

def verify(geom) -> list[Issue]:       # 렌더 무결성 검사
    return [...]  # 검사 항목:
    #  R1 방 겹침(overlap)            R2 벽 틈/미정렬(gap, 비직교 코너)
    #  R3 문이 벽 위 아님(off-wall)    R4 기구가 방 밖
    #  R5 세대 외곽 미폐합            R6 문/창 폭 0·비정상
    #  R7 고립 방(문·개구부 0)         R8 치수 불가(scale 없음)

def fix(geom, issues) -> Geometry:     # G: 실측 기하 정리(treemap 교체 아님)
    #  R1 겹침 → 작은 방을 경계로 클립/이동
    #  R2 벽 → 인접 벽 끝점 스냅(tol)·직교 정렬(near-90° 스냅)
    #  R3 문 → 최근접 wall.seg에 투영(on_wall 갱신)
    #  R4 기구 → 방 폴리곤 안으로 이동(clamp)
    #  R5 외곽 → 방 합집합 외곽으로 폐합
    #  R6 폭 → subtype 기본폭으로 보정
    #  R7 고립 → 인접 방과 최소 개구부(문) 생성(경고 플래그)
    return geom
```

---

## 3. render_svg — 생성형 도면(화면용)
```python
def render_svg(geom: Geometry, *, dims=True, labels=True, fixtures=True) -> str:
    svg = Canvas(geom.bbox, margin=치수공간)
    # 1) 벽: exterior=두껍게(예 8px)·interior=얇게(4px), 개구부(문·창)는 끊고 그림
    for w in geom.walls: svg.wall(w.seg, thick=8 if w.type=="exterior" else 4, gaps=opening_spans(w))
    # 2) 문: 벽 끊김 + 호(arc) 스윙(hinge·swing_deg). orientation 없으면 단순 개구부
    for d in geom.doors: svg.door_arc(d.pos, d.hinge, d.swing_deg, d.width_px) or svg.opening(d.pos,d.width_px)
    # 3) 창: 벽 내 이중선
    for win in geom.windows: svg.window(win.pos, win.on_wall, win.width_px)
    # 4) 기구 심볼: 변기/세면대/싱크대/욕조/가스레인지 표준 픽토그램
    if fixtures:
        for r in geom.rooms:
            for f in r.fixtures: svg.symbol(FIX_SYMBOL[f.cls], f.bbox)
    # 5) 실명+면적 라벨
    if labels:
        for r in geom.rooms: svg.text(r.label_pt, f"{ROLE_KO[r.role]}\n{r.area_m2:.1f}㎡")
    # 6) 치수선 체인(scale): 세대 외곽 4면 + 주요 방 경계
    if dims and geom.scale_mm_per_px: svg.dim_chains(geom, mm_per_px=geom.scale_mm_per_px)
    # 7) 잔여 issues는 빨강 경고(보정필요 표시)
    for iss in geom.issues: svg.warn(iss)
    return svg.tostring()
```
> 출력은 PNG로도(matplotlib/cairosvg) — 화면 미리보기·캡처용.

---

## 4. render_dxf — AutoCAD 작업도면 (ezdxf)
```python
def render_dxf(geom: Geometry) -> bytes:        # 단위 mm, 좌표 = px×scale_mm_per_px
    doc = ezdxf.new("R2010", setup=True); msp = doc.modelspace()
    for name in ("WALL","DOOR","WINDOW","FIXTURE","FURNITURE","DIM","TEXT","GRID","ROOM"):
        doc.layers.add(name, color=LAYER_COLOR[name])     # 레이어 분리(사무소 편집)
    s = geom.scale_mm_per_px or 1.0
    P = lambda x,y: (x*s, -y*s)                            # px→mm, y축 뒤집기(CAD 상향)
    # 벽(두께=폴리라인 width 또는 이중선), 문(arc), 창(블록), 방 외곽(ROOM 레이어 폴리라인)
    for w in geom.walls:  msp.add_lwpolyline([P(*w.seg[0]),P(*w.seg[1])], dxfattribs={"layer":"WALL"})
    for d in geom.doors:  add_door_block(msp, d, P, layer="DOOR")     # arc 스윙
    for win in geom.windows: add_window_block(msp, win, P, layer="WINDOW")
    for r in geom.rooms:
        msp.add_lwpolyline([P(*p) for p in r.polygon]+[P(*r.polygon[0])], dxfattribs={"layer":"ROOM"})
        msp.add_text(f"{ROLE_KO[r.role]} {r.area_m2:.1f}㎡", dxfattribs={"layer":"TEXT"}).set_pos(P(*r.label_pt))
        for f in r.fixtures: add_fixture_block(msp, f, P, layer="FIXTURE")
    add_dim_chains(msp, geom, P, layer="DIM")             # associative 치수
    add_title_block(doc, geom)                            # 도면틀·축척·제목
    buf = io.BytesIO(); doc.write(io.TextIOWrapper(buf)); return buf.getvalue()
```
> 의존성: `ezdxf`(requirements 추가, 서버 설치). 부가 출력: PDF(matplotlib backend) · IFC(후순위).

---

## 5. GUI (📗 G-라인 도면생성) — 버튼 2개
```python
if which.startswith("📗"):                  # G-라인 도면생성
    g = pick_or_generate_graph()            # 사용셋 그래프 선택 OR 모델 생성결과
    geom = autocorrect(cadrender.from_geomgraph(g))
    st.subheader("🖼 생성형 도면")
    st.image(cadrender.render_png(geom))                 # 화면 표시(SVG→PNG)
    if geom.issues: st.warning(f"자기교정 잔여 {len(geom.issues)}건")
    st.download_button("📐 AutoCAD(DXF) 받기", cadrender.render_dxf(geom),
                       file_name=f"{geom.plan_id}.dxf")
    # (위상도면 렌더는 두지 않음)
```
- **T-라인(📘)도 같은 버튼** — `from_floorgeom`만 갈아끼움(나중). 비교 화면(⚖️): T∥G 생성형 도면 + 각 [DXF].

---

## 6. 구현 파일·순서
| # | 산출 | 파일 | 비고 |
|---|---|---|---|
| 1 | 공통 Geometry + `from_geomgraph` | `src/plan2graph/cadrender.py` | G 어댑터 |
| 2 | `verify`/`fix`/`autocorrect` | 〃 | geom_correct 패턴, G 정리 |
| 3 | `render_svg`/`render_png` | 〃 | 의존성 없음 → 로컬 검증 가능 |
| 4 | `render_dxf` | 〃 | ezdxf(서버 설치) |
| 5 | 📗 G도면생성 버튼 2개 | `admin.py` | 위상도면 제거 |
| 6 | T 어댑터 `from_floorgeom` + 비교화면 | 〃 | 나중 |

> 원칙: 스키마는 T·G 분리 유지(ADR-0002), **렌더 코어·출력만 공용**(=도면품질 비교). 헤비 학습/생성은 사용자 클릭([[goal-is-comparable-program-not-results]]).
