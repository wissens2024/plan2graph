"""중복 그룹의 역할 라벨 통일 — 정책의 효과를 먼저 재고, 정해진 뒤에 적용한다.

왜 따로 만드나: 같은 평면의 사본을 작업자들이 다르게 라벨링했다. 다수결로는 못 닫는다
(보정본 59%가 전파 사본이라 '다수'가 한 사람이다 — DEDUP_STATUS §0-1). 닫는 방법은
**어느 이름이 이긴다는 기준(정책)을 정하는 것**이고, 그 기준은 역할 쌍의 병합으로 표현된다.

기본 모드(`--report`)는 **읽기만 한다**: 각 그룹이 "어느 역할들을 한 이름으로 합치면"
닫히는지 전수로 뽑아 빈도순으로 보여준다. 즉 정책 후보를 데이터가 제안한다.

`--apply`는 edits만 다시 쓴다(parsed/graphs 불변). 원본은
`data/staging/corrected/_dedup_backup/<시각>_unify/`로 먼저 복사하고,
고친 방에 `verified_by="rule"`과 `rule`(적용한 규칙)을 남긴다 — 사람 보증과 섞지 않는다.

usage:
  python scripts/dedup_unify.py --report                 # 정책 후보 전수(읽기전용)
  python scripts/dedup_unify.py --map 팬트리=펜트리,화장실=욕실   # 그 정책의 효과만
  python scripts/dedup_unify.py --map ... --apply        # 실제 적용(백업 후)
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import shutil

INDEX = "data/staging/dedup_index.json"
EDITS = "data/staging/corrected/edits"
BACKUP = "data/staging/corrected/_dedup_backup"
GATED = "data/staging/korean_gated_ids.json"


def load_gated(path):
    if not os.path.exists(path):
        return set()
    d = json.load(open(path, encoding="utf-8"))
    if isinstance(d, dict):
        for k in ("ids", "plan_ids", "kept", "items"):
            if isinstance(d.get(k), list):
                return set(str(x) for x in d[k])
        return set(str(x) for x in d)
    return set(str(x) for x in d)


def parse_map(s):
    """'a=b,c=b' → {a: b, c: b}. 왼쪽이 사라지고 오른쪽이 이긴다."""
    m = {}
    for part in (s or "").split(","):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("=")
        a, b = a.strip(), b.strip()
        if not a or not b:
            raise SystemExit("--map 형식: 왼쪽=오른쪽 (예: 팬트리=펜트리)")
        m[a] = b
    return m


def resolve(role, m, depth=8):
    """연쇄 매핑(a=b,b=c)도 끝까지 따라간다. 순환은 depth로 끊는다."""
    for _ in range(depth):
        nxt = m.get(role)
        if nxt is None or nxt == role:
            return role
        role = nxt
    return role


def derive_master(cnt):
    """안방/침실은 취향이 아니라 **도출값**이다 — 침실 중 가장 큰 하나만 안방
    (suggest_roles가 쓰는 신호와 같다). 침실 총수가 같으면 조합이 결정된다.
    집계만으로 판정할 수 있어 면적을 다시 읽지 않는다."""
    nb = cnt.get("안방", 0) + cnt.get("침실", 0)
    if nb <= 0:
        return cnt
    cnt.pop("안방", None)
    cnt.pop("침실", None)
    cnt["안방"] = 1
    if nb - 1 > 0:
        cnt["침실"] = nb - 1
    return cnt


DERIVES = {"master": derive_master}


def norm_roles(roles, m, derive=()):
    out = collections.Counter()
    for r, n in roles.items():
        out[resolve(r, m)] += n
    for d in derive:
        out = DERIVES[d](out)
    return tuple(sorted(out.items()))


def diff_roles(dicts):
    """변종 사이에서 **개수가 갈리는 역할**들의 집합. 여기 든 이름이 분쟁 당사자다."""
    keys = set()
    for d in dicts:
        keys |= set(d)
    return {k for k in keys if len({d.get(k, 0) for d in dicts}) > 1}


def variant_sig(v, m, derive=()):
    return (v["n_rooms"], norm_roles(v["roles"], m, derive))


def closes(variants, m, derive=()):
    return len({variant_sig(v, m, derive) for v in variants}) == 1


def pre(variants, m, derive=()):
    """변종별 정규화된 역할 집계 리스트(판정의 공통 입력)."""
    return [dict(norm_roles(v["roles"], m, derive)) for v in variants]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default=INDEX)
    ap.add_argument("--edits", default=EDITS)
    ap.add_argument("--gated", default=GATED)
    ap.add_argument("--map", default="", help="역할 정규화 규칙 '왼쪽=오른쪽,...'")
    ap.add_argument("--derive", default="", help="도출 규칙(병합 아님): master")
    ap.add_argument("--report", action="store_true", help="정책 후보 전수(읽기전용)")
    ap.add_argument("--consolidate", action="store_true",
                    help="그룹마다 대표 1개만 남기는 경우의 규모(읽기전용)")
    ap.add_argument("--apply", action="store_true", help="edits에 실제 적용(백업 후)")
    ap.add_argument("--house", default="APT")
    ap.add_argument("--scope", default="unit", help="unit|floor|all")
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()

    derive = tuple(x.strip() for x in args.derive.split(",") if x.strip())
    for d in derive:
        if d not in DERIVES:
            raise SystemExit("--derive 가능값: %s" % ", ".join(DERIVES))

    idx = json.load(open(args.index, encoding="utf-8"))
    gated = load_gated(args.gated)
    groups = {
        gid: g for gid, g in idx["groups"].items()
        if g.get("status") == "conflict"
        and (args.house == "all" or g.get("house") == args.house)
        and (args.scope == "all" or g.get("scope") == args.scope)
    }
    print("[pool] 불일치 그룹 %d (house=%s scope=%s) · 인덱스 %s"
          % (len(groups), args.house, args.scope, idx.get("built_at")))

    def gated_n(g):
        return sum(1 for mb in g["members"] if mb in gated)

    if args.report:
        # 각 그룹: "갈리는 역할들을 한 이름으로 합치면 닫히나?" → 그 역할 집합을 센다.
        fam = collections.Counter()
        fam_g = collections.Counter()
        struct = eqrooms = pos = 0
        for gid, g in groups.items():
            vs = g["variants"]
            if len({v["n_rooms"] for v in vs}) > 1:
                struct += 1
                continue
            eqrooms += 1
            ds = pre(vs, {}, derive)
            d = diff_roles(ds)
            if not d:
                pos += 1          # 조합 동일 = 위치만 다름 → 라벨 정책으로는 못 닫는다
                continue
            m = {r: sorted(d)[0] for r in d}
            if closes(vs, m, derive):
                key = " · ".join(sorted(d))
                fam[key] += 1
                fam_g[key] += gated_n(g)
        if derive:
            print("  도출 규칙 적용: %s" % ", ".join(derive))
        print("  방 수 다름(구조) %d · 방 수 같음 %d (그중 위치만 %d)" % (struct, eqrooms, pos))
        print("-" * 78)
        print("역할 라벨만 갈린 그룹 — '이 이름들을 하나로 합치면 닫힌다'")
        print("%-46s %6s %6s" % ("합쳐야 하는 역할", "그룹", "학습"))
        for key, n in fam.most_common(args.top):
            print("%-46s %6d %6d" % (key, n, fam_g[key]))
        tot, totg = sum(fam.values()), sum(fam_g.values())
        print("-" * 78)
        print("합계: %d그룹 · 학습 %d장이 역할 정규화만으로 닫힌다" % (tot, totg))
        # 역할 하나가 몇 개 분쟁에 끼어 있나 = 정책의 레버 크기
        lever = collections.Counter()
        lever_g = collections.Counter()
        for key, n in fam.items():
            for r in key.split(" · "):
                lever[r] += n
                lever_g[r] += fam_g[key]
        print("-" * 78)
        print("분쟁에 끼어든 역할(= 기준을 정해야 하는 이름) 빈도")
        for r, n in lever.most_common(args.top):
            print("  %-12s %5d그룹  학습 %5d장" % (r, n, lever_g[r]))
        return

    if args.consolidate:
        # 의미 정책으로 못 닫으면 남는 선택지: 그룹마다 결과 하나만 남긴다(동결). 어느 변종이
        # 이기는지는 의미가 아니라 **동점 깨기**다 — 883그룹 중 803이 독립 판단 1대1이므로
        # 사본이 가장 많은(= 작업자가 전파를 택한) 변종을 대표로 잡은 규모를 센다.
        kinds = collections.Counter()
        tot = collections.Counter()
        for gid, g in groups.items():
            vs = sorted(g["variants"], key=lambda v: -len(v["members"]))
            rep, rest = vs[0], vs[1:]
            lost = [mb for v in rest for mb in v["members"]]
            k = ("구조" if len({v["n_rooms"] for v in vs}) > 1
                 else ("위치" if not diff_roles(pre(vs, {})) else "라벨"))
            kinds[k] += 1
            tot[k + "/덮어쓸 도면"] += len(lost)
            tot[k + "/그중 학습"] += sum(1 for mb in lost if mb in gated)
            tot["덮어쓸 도면"] += len(lost)
            tot["그중 학습"] += sum(1 for mb in lost if mb in gated)
            tot["대표로 남는 도면"] += len(rep["members"])
        print("-" * 78)
        print("그룹마다 대표 1개만 남기면 (대표 = 사본 최다 변종)")
        for k in ("구조", "라벨", "위치"):
            print("  %-4s %4d그룹 → 덮어쓸 도면 %5d장 (그중 학습 %4d장)"
                  % (k, kinds[k], tot[k + "/덮어쓸 도면"], tot[k + "/그중 학습"]))
        print("  합계      %4d그룹 → 덮어쓸 도면 %5d장 (그중 학습 %4d장) · 대표로 남는 도면 %d장"
              % (sum(kinds.values()), tot["덮어쓸 도면"], tot["그중 학습"],
                 tot["대표로 남는 도면"]))
        print("  ⚠ 이건 '옳은 라벨'을 고르는 게 아니라 **한 결과로 동결**하는 것이다.")
        return

    m = parse_map(args.map)
    if not m and not derive:
        raise SystemExit("--report 또는 --map/--derive 중 하나는 필요하다")
    print("[정책] " + ", ".join("%s→%s" % (a, b) for a, b in sorted(m.items()))
          + ("  도출: %s" % ", ".join(derive) if derive else ""))

    hit, hit_g, part = [], 0, 0
    for gid, g in groups.items():
        vs = g["variants"]
        if closes(vs, m, derive) and not closes(vs, {}):
            hit.append(gid)
            hit_g += gated_n(g)
        elif diff_roles(pre(vs, {})) & set(m):
            part += 1
    print("  닫히는 그룹 %d · 학습 도면 %d장 · 규칙이 닿지만 못 닫는 그룹 %d"
          % (len(hit), hit_g, part))
    if not args.apply:
        print("  (읽기만 함 — 적용은 --apply)")
        for gid in hit[:10]:
            print("   예: %s (사본 %d)" % (gid, groups[gid]["n"]))
        return

    if derive:
        # 도출은 '어느 침실이 가장 큰가'를 폴리곤에서 다시 봐야 한다(집계만으로는 효과 측정까지).
        raise SystemExit("--derive는 효과 측정 전용 — 적용은 --map만 (면적 재판독 미구현)")

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    bdir = os.path.join(BACKUP, "%s_unify" % stamp)
    os.makedirs(bdir, exist_ok=True)
    nfile = nroom = 0
    for gid in hit:
        for pid in groups[gid]["corrected"]:
            p = os.path.join(args.edits, "%s.json" % pid)
            if not os.path.exists(p):
                continue
            ge = json.load(open(p, encoding="utf-8"))
            chg = 0
            for r in (ge.get("rooms") or {}).values():
                old = r.get("role") or r.get("base")
                new = resolve(old, m)
                if new != old:
                    r["role"] = new
                    r["verified_by"] = "rule"
                    r["rule"] = "dedup_unify:%s→%s" % (old, new)
                    chg += 1
            if not chg:
                continue
            shutil.copy2(p, os.path.join(bdir, "%s.json" % pid))
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(ge, f, ensure_ascii=False)
            os.replace(tmp, p)
            nfile += 1
            nroom += chg
    print("  적용: 파일 %d · 방 %d · 백업 %s" % (nfile, nroom, bdir))
    print("  ⚠ dedup_index.py를 다시 돌려야 현황판 숫자가 맞는다")


if __name__ == "__main__":
    main()
