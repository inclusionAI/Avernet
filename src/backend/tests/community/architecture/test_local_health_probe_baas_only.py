"""Architecture guard: LocalHealthProbe must not depend on LocalProcessManager
and must not reference 127.0.0.1 literals.

After plan-04, LocalHealthProbe walks ACTIVE bindings via BaaS get_http_info
+ httpx — the legacy in-process subprocess probe path is gone. A regression
that re-adds LocalProcessManager or 127.0.0.1 here would defeat the
"singlebox health goes via BaaS" invariant.
"""
import ast
from pathlib import Path


LOCAL_HEALTH_PATH = (
    Path(__file__).resolve().parents[3]
    / "src" / "agentclaw" / "community" / "plugins" / "local" / "health_probe.py"
)


def test_local_health_probe_no_process_manager_import():
    src = LOCAL_HEALTH_PATH.read_text()
    tree = ast.parse(src)
    forbidden_names = {"LocalProcessManager"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.name not in forbidden_names, (
                    f"LocalHealthProbe must not import {alias.name}; "
                    f"singlebox health goes via BaaS (plan-04)"
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden_names, (
                    f"LocalHealthProbe must not import {alias.name}"
                )


def test_local_health_probe_no_localhost_string():
    src = LOCAL_HEALTH_PATH.read_text()
    forbidden_substrings = ["127.0.0.1", "localhost"]
    for forbidden in forbidden_substrings:
        offenders = [
            f"line {i}: {line.strip()}"
            for i, line in enumerate(src.splitlines(), start=1)
            if forbidden in line
            and not line.lstrip().startswith("#")
        ]
        assert not offenders, (
            f"LocalHealthProbe must not contain '{forbidden}' literals "
            f"(singlebox routes via BaaS-supplied URLs):\n  "
            + "\n  ".join(offenders)
        )
