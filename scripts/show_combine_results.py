"""combine 매트릭스 현재 결과 조회 — 전이학습 런과 분리(version=vN & pretrain 없음)."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)
from plan2graph import experiments as e


def neu(rows):
    return [r for r in rows if r["generator"] == "신경망" and r.get("pretrain") in (None, "없음")]


def main():
    s = e.agg_summary()
    dw = {r["version"]: r for r in neu(s["dwelling"])}
    ev = {(r["version"], r["loop"]): r for r in neu(s["eval"])}
    un = {r["version"]: r for r in neu(s["generalization"]) if r["subset"] == "unseen"}
    print("ver seeds  APT   DEH   ROW   macro  micro  unseen")
    for v in ["v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7", "v0cap2x"]:
        d = dw.get(v); eo = ev.get((v, "off")); en = ev.get((v, "on")); u = un.get(v)
        if not d and not eo:
            print("%-4s  (none)" % v); continue
        f3=lambda x: ("%.3f"%x) if isinstance(x,(int,float)) else "-"
        mic = ("%.3f" % eo["adj_L1_mean"]) if eo else "-"
        uns = ("%.3f" % u["adj_L1_mean"]) if u else "-"
        sd = d["seeds"] if d else eo["seeds"]
        ap,de,ro,ma=(f3(d.get("APT")),f3(d.get("DEH")),f3(d.get("ROW")),f3(d.get("macro"))) if d else ("-","-","-","-")
        print("%-7s %2d  %5s %5s %5s  %5s  %5s  %5s" % (v, sd, ap,de,ro, ma, mic, uns))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
