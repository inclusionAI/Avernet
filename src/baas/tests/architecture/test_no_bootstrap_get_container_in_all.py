"""Architecture enforcement: get_container must not leak beyond bootstrap.

``community.bootstrap.get_container`` provides raw access to the DI container.
Components must receive their dependencies via constructor injection, not by
reaching out to the global container — that creates hidden coupling, makes
testing harder, and bypasses the explicit wiring that bootstrap provides.

Only the ``bootstrap/`` layer itself (where ``get_container`` is defined) may
import it.  All other production layers must rely on constructor injection.

Derived from the Microkernel Architecture Constitution (Rules 6, 7, 14):
- Rule 6 —  Architectural layers constrain imports.
- Rule 7 —  Core APIs are library-style; delivery is a thin adapter.
- Rule 14 — Configuration drives all wiring (not ad-hoc container grabs).
"""

import ast
from pathlib import Path

SECBAAS = Path(__file__).resolve().parents[2] / "src" / "secbaas" / "community"

# Only the bootstrap/ layer defines and owns get_container
_ALLOWED_LAYERS = {"bootstrap"}

# Known pre-existing violations (lazy/function-body imports inside core/plugins)
# These are tracked debt — the test warns about them but doesn't fail.
_KNOWN_DEBT: dict[str, list[tuple[str, int]]] = {
    "core": [
        ("core/utils/callback_utils.py", 31),
        ("core/repository/ws_relay_session/_factory.py", 11),
        ("core/service/paas/_local_paas_service.py", 775),
        ("core/service/paas/_local_paas_service.py", 1982),
    ],
    "plugins": [
        ("plugins/sandbox/utils/arca_utils.py", 92),
        ("plugins/sandbox/utils/arca_utils.py", 121),
    ],
}


def _collect_get_container_imports() -> dict[str, list[tuple[str, int, str]]]:
    """Scan all production .py files for imports of get_container from community.bootstrap.

    Returns dict mapping layer name to list of (rel_path, lineno, import_text) tuples.
    """
    results: dict[str, list[tuple[str, int, str]]] = {}

    for py_file in sorted(SECBAAS.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        rel_path = py_file.relative_to(SECBAAS)
        layer = rel_path.parts[0]

        # bootstrap/ is the only allowed owner of get_container
        if layer in _ALLOWED_LAYERS:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != "secbaas.community.bootstrap":
                continue

            names = [a.name for a in node.names]
            if "get_container" not in names:
                continue

            import_text = f"from {node.module} import {', '.join(names)}"
            lineno = getattr(node, "lineno", 0)
            results.setdefault(layer, []).append((str(rel_path), lineno, import_text))

    return results


def _is_known_debt(rel_path: str, lineno: int) -> bool:
    """Check if a specific violation is in the known debt baseline."""
    for layer_violations in _KNOWN_DEBT.values():
        for known_path, known_lineno in layer_violations:
            if known_path == rel_path and known_lineno == lineno:
                return True
    return False


def test_no_forbidden_get_container_imports():
    """Fail on any NEW import of get_container from community.bootstrap.

    Only bootstrap/ is allowed to import get_container.
    Known pre-existing violations are reported as warnings only.
    If a known violation is fixed, the test warns about debt paydown.
    """
    results = _collect_get_container_imports()
    new_violations: list[str] = []
    debt_found: list[str] = []

    for layer in sorted(results):
        for rel_path, lineno, import_text in results[layer]:
            entry = f"  {rel_path}:{lineno}: {import_text}"
            if _is_known_debt(rel_path, lineno):
                debt_found.append(entry)
            else:
                new_violations.append(entry)

    if new_violations:
        raise AssertionError(
            f"\n{len(new_violations)} NEW get_container import(s) outside of bootstrap:\n"
            + "\n".join(new_violations)
            + "\n\nComponents must receive dependencies via constructor "
            "injection, not by importing get_container directly."
        )

    if debt_found:
        import warnings

        warnings.warn(
            f"\n{len(debt_found)} known get_container import(s) outside of bootstrap "
            f"(tracked debt):\n"
            + "\n".join(debt_found)
            + "\n\nThese are legacy lazy imports. Fix by injecting the required "
            "dependencies via constructor injection."
        )


def test_get_container_debt_paydown_detected():
    """Warn when a known debt violation has been fixed (count drops below baseline)."""
    results = _collect_get_container_imports()

    total_remaining = sum(len(sites) for sites in results.values())
    total_baseline = sum(len(sites) for sites in _KNOWN_DEBT.values())

    if total_remaining < total_baseline:
        import warnings

        warnings.warn(
            f"\nget_container debt decreased! Now {total_remaining} sites "
            f"(was {total_baseline}). Update _KNOWN_DEBT in {__file__}."
        )


def test_get_container_import_audit():
    """Audit report of all get_container imports outside bootstrap.

    This test always passes — it's a documentation / audit helper viewable
    with ``pytest -v``.
    """
    results = _collect_get_container_imports()

    if not results:
        import warnings

        warnings.warn("\nNo get_container imports found outside bootstrap/. ✓")
        return

    total = sum(len(sites) for sites in results.values())
    lines: list[str] = []
    for layer in sorted(results):
        lines.append(f"  [{layer}] ({len(results[layer])} site(s)):")
        for rel_path, lineno, import_text in sorted(results[layer]):
            tag = " [KNOWN DEBT]" if _is_known_debt(rel_path, lineno) else " [NEW]"
            lines.append(f"    {rel_path}:{lineno}: {import_text}{tag}")

    import warnings

    warnings.warn(
        f"\nget_container import audit ({total} site(s) outside bootstrap):\n"
        + "\n".join(lines)
    )
