"""Architecture enforcement: no infrastructure middleware leakage.

Verifies that core architecture layers (``adapters/``, ``api/``, ``core/``,
``spi/``) do not directly import infrastructure middleware packages.
These middleware (sofa, mosn, layotto, zdas, zcache, arca, poolab, buservice)
are infrastructure concerns that must be isolated behind SPI abstractions
and handled only by the ``plugins/`` and ``infra/`` layers.

**Known violations** (waived, see RULES-MANIFEST.md):
- ``core/service/paas/_arca_paas_service.py`` — imports ``arca.model``
- ``core/service/paas/_standalone_paas_service.py`` — imports ``sofapy_base``
- ``api/device_manage/*.py`` — imports ``arca.model.sandbox``
"""

import ast
import warnings
from pathlib import Path

SECBAAS = Path(__file__).resolve().parents[2] / "src" / "secbaas"

_FORBIDDEN_NAMES = {
    "sofapy",
    "sofapy_base",
    "mosn",
    "layotto",
    "zdas",
    "zcache",
    "arca",
    "poolab",
    "buservice",
}

_ALLOWED_MODULES: set[str] = {
    "secbaas.spi.sandbox.arca",
    "secbaas.spi.sandbox.poolab",
}

_KNOWN_VIOLATIONS: set[str] = {}


def _is_forbidden(name: str) -> bool:
    first_dot = name.index(".") if "." in name else len(name)
    return name[:first_dot] in _FORBIDDEN_NAMES


def _module_name(py_file: Path) -> str:
    rel = py_file.relative_to(SECBAAS.parent)
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
    violations = _scan_dir(SECBAAS / "adapters")
    _report("adapters/", *_classify(violations))


def test_api_no_forbidden_infra_imports():
    violations = _scan_dir(SECBAAS / "api")
    _report("api/", *_classify(violations))


def test_core_no_forbidden_infra_imports():
    violations = _scan_dir(SECBAAS / "core")
    _report("core/", *_classify(violations))


def test_spi_no_forbidden_infra_imports():
    violations = _scan_dir(SECBAAS / "spi")
    _report("spi/", *_classify(violations))
