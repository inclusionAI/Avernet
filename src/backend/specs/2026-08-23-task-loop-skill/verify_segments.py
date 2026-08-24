"""逐段字节校验:deployed SKILL.md 各段 == deploy_strip(body(src_for(k))) + marker_for(k)。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import assemble as A

SKILL = os.path.join(A.PKG, "SKILL.md")
lines = open(SKILL, encoding="utf-8").read().split("\n")

marks = {}
for i, l in enumerate(lines):
    for k in A.ORDER:
        if l == A.marker_for(k):
            marks[k] = i
for k in A.ORDER:
    assert k in marks, f"missing marker {k} ({A.marker_for(k)!r})"
order_idx = [marks[k] for k in A.ORDER]

allok = True
for i, k in enumerate(A.ORDER):
    start = order_idx[i] + 1
    seg_lines = lines[start:order_idx[i + 1]] if i + 1 < len(A.ORDER) else lines[start:]
    seg = "\n".join(seg_lines).strip("\n")
    expected = A.deploy_strip(A.body(A.src_for(k)))
    ok = seg == expected
    print(f"[{k}] ok={ok} seg_len={len(seg)} exp_len={len(expected)}")
    if not ok:
        n = min(len(seg), len(expected))
        d = next((j for j in range(n) if seg[j] != expected[j]), n)
        print("  diverge at", d, "(len diff", len(seg)-len(expected), ")")
        print("  got :", repr(seg[max(0,d-50):d+50]))
        print("  want:", repr(expected[max(0,d-50):d+50]))
        allok = False
    else:
        allok = allok and ok
print("ALL_SEGMENTS_BYTE_EQUAL =", allok)
sys.exit(0 if allok else 1)
