# 에디터 SPEC: 방 나누기(SPLIT) — 연결공간(복도·파우더룸) 복원

대상: edit_server.py 담당 세션. 서버 분할 로직은 **이미 구현됨**(`src/plan2graph/graph_edit.py:split_room`, 검증 완료). 여기 적힌 것은 ① 붙여넣을 엔드포인트 ② UI 인터랙션. 

## 왜 (배경)
R2G 파서가 연결공간(복도, 드레스룸 앞 파우더룸/전실)을 옆 큰 방에 흡수 → 폴리곤이 길쭉해지고 위상이 틀어짐(침실·욕실 문이 거대한 거실/드레스룸에 매달림). [[canonical-kr-apartment-topology]]. **사람이 컷을 긋고(판단), 문·엣지는 기하로 자동 재분배** → 복도/파우더룸이 허브로 복원. merge의 역연산.

## ① 서버 함수 계약 (구현 완료 — 그대로 호출)
```python
from plan2graph.graph_edit import split_room
graph, err = split_room(graph, room_id, cut, roles)
# room_id : 나눌 방 id (str/int)
# cut     : [[x1,y1],[x2,y2]]  사람이 그은 분할 선분(폴리곤 좌표계=PNG px)
# roles   : [left_role, right_role]  컷 방향 p0→p1 기준 **좌측/우측** 역할
# 반환    : (수정된 graph, err). err=None이면 성공. 폴리곤 2분할 + 문/창/엣지 재분배
#           + 두 조각 open 연결 + 연결공간(복도/전실) is_connector=True 자동.
```
검증결과(실측): 거실 1개 → 거실+복도, edges 26→27(open연결 추가), 문 기하 재분배 OK.

## ② 추가할 엔드포인트 (edit_server.py — 붙여넣기)
do_POST 안에 분기 추가:
```python
if u.path == "/api/split":
    ln = int(self.headers.get("Content-Length", 0))
    body = json.loads(self.rfile.read(ln).decode("utf-8"))
    from plan2graph.graph_edit import split_room
    g, err = split_room(body["graph"], body["room_id"], body["cut"], body["roles"])
    if err:
        return self._send(400, json.dumps({"error": err}, ensure_ascii=False))
    return self._send(200, json.dumps({"graph": g, "status": _status(g)}, ensure_ascii=False))
```
(저장은 기존 흐름대로 사용자가 💾 누를 때 POST /api/graph로. split은 그래프만 변형해 돌려줌 = 클라가 G 교체·dirty.)

## ③ UI 인터랙션 (모드 추가: 역할 / 인접 / 합치기 / **나누기**)
1. **방 선택**: 길쭉한 방 클릭(선택 하이라이트).
2. **컷 그리기**: 방을 가로지르도록 **두 점 클릭**(PNG 배경 보고 복도가 좁아지는 실제 벽 위치). 두 점을 잇는 미리보기 선 표시. (선분은 방 밖까지 자동 연장되므로 양 끝이 방 경계 근처면 됨.)
3. **역할 지정**: 컷이 그려지면 두 조각을 시각 구분(예: 좌측=파랑, 우측=초록 반투명)하고 **각 측 역할 드롭다운** 2개:
   - 거실 케이스: 좌/우 = `거실` / `복도`
   - 드레스룸 케이스: `드레스룸` / `파우더룸`
   - ⚠️ 순서 = 컷 방향(첫클릭→둘째클릭) 기준 **좌측이 roles[0], 우측이 roles[1]**. 어느 쪽이 좌/우인지 색으로 보여줘 사람이 맞게 고르게.
4. **확정** → `POST /api/split {graph:G, room_id, cut, roles}` → 응답 `graph`로 G 교체, `render()`, `setDirty(true)`, status 갱신. (역할이 좌우 바뀌었으면 역할 모드로 클릭 수정 가능.)
5. 💾 저장 시 기존대로 corrected→`edits/` 저장 + 회계 done+1.

## 수용 기준
길쭉한 거실 선택 → 컷 2점 → 거실/복도 지정 → 분할되어 **복도가 침실·욕실 문을 갖는 허브**가 되고, 거실↔복도 open. 콘솔 에러 0, 실브라우저 검증. 드레스룸→드레스룸+파우더룸 동일.

## 참고
- 컷이 방을 못 가르면 err 반환(양 끝이 방을 통과하도록 안내).
- 연결공간 역할: 복도·전실은 is_connector 자동 True. 파우더룸은 일반 역할이나 엣지로 허브 됨(문 재분배가 핵심).
- 관련: [[canonical-kr-apartment-topology]] · ADR-0008(정보보정) · ADR-0009(용어).
