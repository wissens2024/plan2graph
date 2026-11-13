#!/usr/bin/env python3
"""verify_overrides — data/link_overrides.json 의 **사용자 확정값**이 보정본에
그대로 들어 있는지 검사한다. 읽기 전용. 어긋나면 비정상 종료한다.
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDITS = os.path.join(ROOT, "data", "staging", "corrected", "edits")
OVR = os.path.join(ROOT, "data", "link_overrides.json")


def pairs_of(g):
    ed = {}
    for e in (g.get("edges") or []):
        ed[frozenset((str(e.get("from")), str(e.get("to"))))] = e
    return ed


def doors_of(g):
    out = {}
    for d in (g.get("doors") or []):
        c = [str(x) for x in (d.get("connects") or [])]
        out.setdefault(frozenset(c), []).append(d)
    return out


def main():
    ovr = json.load(open(OVR, encoding="utf-8"))
    bad = 0
    for pid, o in ovr.items():
        if pid.startswith("_"):
            continue
        p = os.path.join(EDITS, pid + ".json")
        if not os.path.exists(p):
            print("!! %s 파일 없음" % pid)
            bad += 1
            continue
        g = json.load(open(p, encoding="utf-8"))
        ed = pairs_of(g)
        dr = doors_of(g)
        msgs = []
        # forbid 한 쌍을 doors/force 가 **다시 넣는** 경우가 있다(09f1fdb3: 가짜 d7 을
        # 지우고 진짜 d_pan 을 그 자리에 넣는다). rebuild_links 는 forbid → doors 순서라
        # 다시 넣은 쪽이 최종값이다. 그래서 금지 검사에서 빼 준다.
        readd = set()
        for d in (o.get("doors") or []):
            rr = [str(x) for x in (d.get("rooms") or [])]
            if len(rr) == 2:
                readd.add(frozenset(rr))
        for f in (o.get("force") or []):
            readd.add(frozenset((str(f[0]), str(f[1]))))
        for a, b in (o.get("forbid") or []):
            k = frozenset((str(a), str(b)))
            if k in readd:
                continue
            if k in ed:
                msgs.append("금지쌍 %s-%s 에 엣지(via=%s)가 남아 있다"
                            % (a, b, ed[k].get("via")))
            if k in dr:
                msgs.append("금지쌍 %s-%s 에 문 레코드 %s 가 남아 있다"
                            % (a, b, [d.get("id") for d in dr[k]]))
        for f in (o.get("force") or []):
            a, b, kind = str(f[0]), str(f[1]), f[2]
            k = frozenset((a, b))
            if k not in ed:
                msgs.append("확정 연결 %s-%s(%s) 가 없다" % (a, b, kind))
            elif ed[k].get("via") != kind:
                msgs.append("확정 연결 %s-%s 종류 %s 인데 %s 다"
                            % (a, b, kind, ed[k].get("via")))
        have_ids = {str(d.get("id")) for d in (g.get("doors") or [])}
        for d in (o.get("doors") or []):
            if str(d.get("id")) not in have_ids:
                msgs.append("확정 문 %s 레코드가 없다" % d.get("id"))
            else:
                rec = [x for x in (g.get("doors") or [])
                       if str(x.get("id")) == str(d.get("id"))][0]
                want = [float(v) for v in d.get("pos")]
                got = [float(v) for v in (rec.get("position") or [-1, -1])]
                if max(abs(want[0] - got[0]), abs(want[1] - got[1])) > 0.6:
                    msgs.append("확정 문 %s 위치 %s 인데 %s 다"
                                % (d.get("id"), want, got))
        for dp in (o.get("door_pos") or []):
            k = frozenset((str(dp[0]), str(dp[1])))
            if k not in dr:
                msgs.append("확정 문 위치 %s-%s 에 문 레코드가 없다" % (dp[0], dp[1]))
                continue
            want = [float(dp[2]), float(dp[3])]
            ok = False
            for d in dr[k]:
                got = d.get("position") or [-1, -1]
                if max(abs(want[0] - float(got[0])),
                       abs(want[1] - float(got[1]))) <= 0.6:
                    ok = True
            if not ok:
                msgs.append("확정 문 위치 %s-%s = %s 인데 %s 다"
                            % (dp[0], dp[1], want,
                               [d.get("position") for d in dr[k]]))
        n_f = len(o.get("forbid") or []) + len(o.get("force") or []) \
            + len(o.get("doors") or []) + len(o.get("door_pos") or [])
        if msgs:
            bad += 1
            print("!! %s  확정값 %d개 중 %d건 불일치" % (pid, n_f, len(msgs)))
            for m in msgs:
                print("     - " + m)
        else:
            print("ok %s  확정값 %d개 전부 일치 (엣지 %d · 문 %d)"
                  % (pid, n_f, len(g.get("edges") or []), len(g.get("doors") or [])))
    print("\n불일치 도면 %d개" % bad)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
