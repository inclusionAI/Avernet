"""Architecture enforcement: no infrastructure middleware leakage.

Verifies that core architecture layers (``adapters/``, ``api/``, ``core/``,
``spi/``) do not directly import transport framework packages at module level.
Transport frameworks (FastAPI, Starlette, uvicorn, aiohttp, httpx) are
delivery concerns that must be isolated behind SPI abstractions and handled
only by the appropriate layers (``adapters/`` for HTTP delivery, ``plugins/``
for concrete implementations).

The ``adapters/`` layer is the approved place for transport framework usage
since that layer translates protocol details.  All other architecture layers
must remain transport-agnostic.

**Known violations** (waived, see RULES-MANIFEST.md):
- (none yet)
"""

import ast
import warnings
from pathlib import Path

GATEWAY = Path(__file__).resolve().parents[2] / "src" / "gateway" / "community"

_FORBIDDEN_NAMES = {
    "fastapi",
    "starlette",
    "uvicorn",
    "aiohttp",
    "httpx",
    "requests",
}

_ALLOWED_MODULES: set[str] = set()

_KNOWN_VIOLATIONS: set[str] = set()


def _is_forbidden(name: str) -> bool:
    first_dot = name.index(".") if "." in name else len(name)
    return name[:first_dot] in _FORBIDDEN_NAMES


def _module_name(py_file: Path) -> str:
    rel = py_file.relative_to(GATEWAY.parent)
    return str(rel.with_suffix("")).replace("/", ".")


def _scan_dir(layer_dir: Path) -> dict[str, list[str]]:
    violations: dict[str, list[str]] = {}
    for py_file in sorted(layer_dir.rglob("*.py")):
        if "__pycache__" in str(py_file) or py_file.name == "__init__.py":
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        mod = _module_name(py_file)
        if mod in _ALLOWED_MODULES:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_forbidden(alias.name):
                        violations.setdefault(mod, []).append(
                            f"  L{node.lineno}: import {alias.name}"
                        )
            if isinstance(node, ast.ImportFrom):
                if node.module and _is_forbidden(node.module):
                    names = [a.name for a in node.names]
                    violations.setdefault(mod, []).append(
                        f"  L{node.lineno}: from {node.module} import {', '.join(names)}"
                    )
    return violations


def _classify(violations: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    known: list[str] = []
    new: list[str] = []
    for mod, msgs in sorted(violations.items()):
        if mod in _KNOWN_VIOLATIONS:
            known.append(f"{mod}:\n" + "\n".join(msgs))
        else:
            new.append(f"{mod}:\n" + "\n".join(msgs))
    return known, new


def _report(label: str, known: list[str], new: list[str]) -> None:
    if known:
        warnings.warn(
            f"\nKnown waived infra imports in {label} "
            f"({len(known)} modules):\n" + "\n".join(known)
        )
    if new:
        raise AssertionError(
            f"\n{len(new)} NEW forbidden infra import(s) in {label}:\n"
            + "\n".join(new)
            + "\n\nRefactor behind SPI or add to _KNOWN_VIOLATIONS."
        )


# ── Tests ────────────────────────────────────────────────────────────────


def test_adapters_no_forbidden_infra_imports():
    """adapters/ is the approved transport layer — skip transport checks."""
    # Gateway's adapters layer legitimately uses FastAPI/Starlette/Uvicorn
    # because it is the HTTP delivery adapter.  This test exists as a
    # placeholder so that any future non-transport infrastructure imports
    # in adapters/ can be caught here.


def test_api_no_forbidden_infra_imports():
    """api/ must remain transport-agnostic."""
    violations = _scan_dir(GATEWAY / "api")
    _report("api/", *_classify(violations))


def test_core_no_forbidden_infra_imports():
    """core/ must remain transport-agnostic."""
    violations = _scan_dir(GATEWAY / "core")
    _report("core/", *_classify(violations))


def test_spi_no_forbidden_infra_imports():
    """spi/ must remain transport-agnostic."""
    violations = _scan_dir(GATEWAY / "spi")
    _report("spi/", *_classify(violations))
