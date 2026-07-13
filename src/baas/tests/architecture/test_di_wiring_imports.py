"""Architecture enforcement: dependency injection wiring import rules.

Derived from the Microkernel Architecture Constitution (Rule 14):

``dependency_injector.wiring`` (Provide, inject) is the mechanism for
wiring concrete implementations at composition roots.  Only approved
wiring layers (``bootstrap/``, ``adapters/``) may import from it.

Core logic, API contracts, and SPI contracts must NOT import DI wiring
directly — they receive their dependencies via constructor injection.
"""

import ast
import warnings
from pathlib import Path

SECBAAS = Path(__file__).resolve().parents[2] / "src" / "secbaas" / "community"

ALLOWED_LAYERS = {"bootstrap", "adapters"}
FORBIDDEN_LAYERS = {"core", "api", "spi", "infra", "plugins", "logger", "config"}

DI_WIRING_PATTERNS = [
    "dependency_injector.wiring",
]


def test_di_wiring_only_in_bootstrap_and_adapters():
    """Rule 14: dependency_injector.wiring must only be imported
    from bootstrap/ and adapters/ layers.
    """
    violations: list[str] = []

    for py_file in sorted(SECBAAS.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        rel_path = py_file.relative_to(SECBAAS)
        layer = rel_path.parts[0]

        if layer in ALLOWED_LAYERS:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(pattern in alias.name for pattern in DI_WIRING_PATTERNS):
                        violations.append(f"  {rel_path}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and any(
                    pattern in node.module for pattern in DI_WIRING_PATTERNS
                ):
                    names = [a.name for a in node.names]
                    violations.append(
                        f"  {rel_path}: from {node.module} import {', '.join(names)}"
                    )

    if violations:
        raise AssertionError(
            f"\n{len(violations)} forbidden dependency_injector.wiring "
            f"import(s) outside bootstrap/ and adapters/:\n"
            + "\n".join(violations)
            + "\n\nDI wiring must only occur in composition roots "
            "(bootstrap/, adapters/). Core/api/spi should use "
            "constructor injection."
        )
