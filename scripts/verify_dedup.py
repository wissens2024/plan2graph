"""지문(CRC32+크기) 기반 '고유 도면' dedup의 정확성 검증.
가정: 같은 도면 → label zip 간 PNG가 byte-identical. CRC32 충돌은 SHA256로 검증.
dual 도면은 SPA-zip·STR-zip 양쪽에 같은 PNG가 있으므로, 둘의 SHA256이 같아야 함.
"""
import hashlib
import sys
import zipfile

from plan2graph import inspect_excluded as ix

split = sys.argv[1] if len(sys.argv) > 1 else "Training"
groups = ix.build_index(split)
duals = [(s, d) for s, d in groups.items() if "SPA" in d and "STR" in d]


def sha(zp, entry):
    with zipfile.ZipFile(zp) as z:
        return hashlib.sha256(z.read(entry)).hexdigest()


print(f"=== dual 표본 8개: SPA-zip PNG vs STR-zip PNG ({split}) ===")
print(f"{'지문(CRC_크기)':24} {'SPA키':12} {'STR키':12} 키다름 SHA일치")
for s, d in duals[:8]:
    sp, stx = d["SPA"], d["STR"]
    h1, h2 = sha(sp["zip"], sp["entry"]), sha(stx["zip"], stx["entry"])
    print(f"{s:24} {sp['key']:12} {stx['key']:12} "
          f"{str(sp['key'] != stx['key']):6} {str(h1 == h2)}")

match = mism = 0
for s, d in duals:
    sp, stx = d["SPA"], d["STR"]
    if sha(sp["zip"], sp["entry"]) == sha(stx["zip"], stx["entry"]):
        match += 1
    else:
        mism += 1
print(f"\n=== 전수검사 dual {len(duals)}개 ===")
print(f"SHA256 일치(진짜 같은 도면): {match}   불일치(CRC 오충돌): {mism}")
print("결론:", "dedup 정확 ✅ (CRC+크기 = 도면 동일성 신뢰)" if mism == 0
      else f"⚠️ {mism}건 CRC 오충돌 — 지문에 SHA 보강 필요")
