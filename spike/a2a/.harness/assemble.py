"""Assemble the spike harness from the plan document's python blocks.

Reads docs/superpowers/plans/2026-10-13-engine-a2a-spike.md, extracts every
fenced python block, routes each by its first-line marker into this
directory's src/ tree, then syntax-checks every assembled file.

Run from repo root:
    python3 spike/a2a/.harness/assemble.py
"""
import pathlib
import py_compile
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[3]
PLAN = REPO / "docs/superpowers/plans/2026-10-13-engine-a2a-spike.md"
SRC = pathlib.Path(__file__).resolve().parent / "src"

# first-line prefix -> target path (mode: w=first block wins, a=append)
ROUTE = [
    ("# spike/a2a/settings.py", "settings.py", "w"),
    ("# spike/a2a/kernel.py", "kernel.py", "w"),
    ("# spike/a2a/engine_mock.py", "engine_mock.py", "w"),
    ("# spike/a2a/server.py", "server.py", "w"),
    ("# server.py 追加段", "server.py", "a"),
    ("# spike/a2a/sse.py", "sse.py", "w"),
    ("# spike/a2a/v3_server.py", "v3_server.py", "w"),
    ("# spike/a2a/v3_echo_engine.py", "v3_echo_engine.py", "w"),
    ("# spike/a2a/bridge_v3.py", "bridge_v3.py", "w"),
    ("# spike/a2a/client_t.py", "client_t.py", "w"),
    ("# spike/a2a/fault_proxy.py", "fault_proxy.py", "w"),
    ("# spike/a2a/probes/g1a_validate.py", "probes/g1a_validate.py", "w"),
    ("# spike/a2a/probes/g1b_checks.py", "probes/g1b_checks.py", "w"),
    ("# spike/a2a/probes/g5_failure_paths.py", "probes/g5_failure_paths.py", "w"),
]
TEST_PREFIX = "# spike/a2a/tests/"


def main() -> int:
    md = PLAN.read_text()
    blocks = re.findall(r"```python\n(.*?)```", md, re.S)
    if SRC.exists():
        for stale in SRC.rglob("*.py"):
            prefix = stale.relative_to(SRC)
            (SRC / prefix).unlink()
    SRC.mkdir(parents=True, exist_ok=True)
    (SRC / "tests").mkdir(exist_ok=True)
    (SRC / "probes").mkdir(exist_ok=True)
    plan = {}
    for block in blocks:
        first = block.splitlines()[0]
        target, mode = None, "w"
        if first.startswith(TEST_PREFIX):
            stem = first[len(TEST_PREFIX):].split("（")[0].rstrip("）)").strip()
            base, _, suffix = stem.partition(".")
            target = "tests/" + (stem if suffix else stem + ".py")
            if not target.endswith(".py"):
                target += ".py"
        else:
            for prefix, tgt, m in ROUTE:
                if first.startswith(prefix):
                    target, mode = tgt, m
                    break
        if target is None:
            continue
        if target in plan and mode == "w":
            print(f"SKIP duplicate root block for {target}")
            continue
        plan.setdefault(target, "")
        plan[target] += block + "\n"
    failures = 0
    for target, text in sorted(plan.items()):
        path = SRC / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        try:
            py_compile.compile(str(path), doraise=True)
            print(f"OK      {target:44s} {len(text.splitlines()):4d} lines")
        except py_compile.PyCompileError as exc:
            failures += 1
            print(f"SYNTAX  {target}: {exc.msg}")
    print(f"files={len(plan)} syntax_failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
