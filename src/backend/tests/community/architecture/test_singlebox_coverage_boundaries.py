"""Keep singlebox coverage instrumentation out of business wiring and Core."""

from __future__ import annotations

import ast
from pathlib import Path


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_SOURCE_ROOT = _BACKEND_ROOT / "src/agentclaw/community"
_FORBIDDEN_ROOTS = (_SOURCE_ROOT / "core", _SOURCE_ROOT / "di")
_FORBIDDEN_PREFIX = "agentclaw.community.utils.singlebox_coverage"


def _coverage_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            _FORBIDDEN_PREFIX
        ):
            imports.append(f"{path.relative_to(_BACKEND_ROOT)}:{node.lineno}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(_FORBIDDEN_PREFIX):
                    imports.append(
                        f"{path.relative_to(_BACKEND_ROOT)}:{node.lineno}"
                    )
    return imports


def test_business_core_and_di_do_not_import_singlebox_coverage_runtime() -> None:
    violations = [
        violation
        for root in _FORBIDDEN_ROOTS
        for path in root.rglob("*.py")
        for violation in _coverage_imports(path)
    ]

    assert not violations, (
        "singlebox coverage is an observation concern; keep it out of Core and "
        "business/profile DI modules:\n  " + "\n  ".join(violations)
    )
