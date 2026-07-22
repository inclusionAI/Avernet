"""Architecture enforcement: core layer rules.

Derived from the Microkernel Architecture Constitution:

- **Rule 7** — Core logic must be transport-agnostic.  Core must not
  import adapters (``gateway.community.adapters``) or web/RPC framework
  packages (``fastapi``, ``aiohttp``, ``starlette``, etc).

- **Rule 14** — Configuration drives all wiring; environment-specific
  branching must occur only in composition roots (``bootstrap/``).
"""

import ast
from pathlib import Path

from pytestarch import Rule

GATEWAY = Path(__file__).resolve().parents[2] / "src" / "gateway" / "community"

# Transport frameworks that should never appear in core
_TRANSPORT_FRAMEWORKS = {  # noqa: F841
    "fastapi",
    "aiohttp",
    "starlette",
    "flask",
    "django",
    "uvicorn",
    "httpx",
    "requests",
}

# Known pre-existing debt — excluded from failures.  See RULES-MANIFEST.md.
_KNOWN_TRANSPORT_DEBT: set[tuple[str, str]] = set()  # no known violations


# ═══════════════════════════════════════════════════════════════════════════
# Rule 7: Core is transport-agnostic (pytestarch import check)
# ═══════════════════════════════════════════════════════════════════════════


def test_core_does_not_import_adapters(project_architecture):  # noqa: ANN001, ANN201
    """Rule 7: Core must not import delivery layer (adapters)."""
    rule = (
        Rule()
        .modules_that()
        .are_sub_modules_of("gateway.community.core")
        .should_not()
        .import_modules_that()
        .are_sub_modules_of("gateway.community.adapters")
    )
    rule.assert_applies(project_architecture)


# ═══════════════════════════════════════════════════════════════════════════
# Rule 7: Core must not import web/RPC frameworks (AST scan)
# ═══════════════════════════════════════════════════════════════════════════


def test_core_no_transport_framework_imports() -> None:
    """Rule 7: Core must not import transport frameworks at module level."""
    core_dir = GATEWAY / "core"
    violations: list[str] = []

    for py_file in sorted(core_dir.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    pkg = alias.name.split(".")[0]
                    if pkg in _TRANSPORT_FRAMEWORKS:
                        violations.append(
                            f"  {py_file.relative_to(GATEWAY)}: import {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module is None:
                    continue
                pkg = node.module.split(".")[0]
                if pkg in _TRANSPORT_FRAMEWORKS:
                    names = [a.name for a in node.names]
                    violations.append(
                        f"  {py_file.relative_to(GATEWAY)}: "
                        f"from {node.module} import {', '.join(names)}"
                    )

    # Filter out known pre-existing debt
    new_violations = [
        v
        for v in violations
        if tuple(v.strip().split(": ", 1)) not in _KNOWN_TRANSPORT_DEBT
    ]

    if new_violations:
        raise AssertionError(
            f"\n{len(new_violations)} transport framework import(s) in core:\n"
            + "\n".join(sorted(new_violations))
            + "\n\nCore must be transport-agnostic (Rule 7)."
        )


# ═══════════════════════════════════════════════════════════════════════════
# Rule 14: Wiring only in approved composition roots
# ═══════════════════════════════════════════════════════════════════════════


def test_bootstrap_not_imported_from_core_api_spi(project_architecture):  # noqa: ANN001, ANN201
    """Rule 14: Core, api, and spi must not import from bootstrap."""
    for layer in (
        "gateway.community.core",
        "gateway.community.api",
        "gateway.community.spi",
    ):
        rule = (
            Rule()
            .modules_that()
            .are_sub_modules_of(layer)
            .should_not()
            .import_modules_that()
            .are_sub_modules_of("gateway.community.bootstrap")
        )
        try:
            rule.assert_applies(project_architecture)
        except AssertionError as exc:
            lines = exc.args[0].splitlines() if exc.args else []
            raise AssertionError(
                f"\n{layer!r} must not import from gateway.community.bootstrap:"
                + "\n".join(lines)
            )
