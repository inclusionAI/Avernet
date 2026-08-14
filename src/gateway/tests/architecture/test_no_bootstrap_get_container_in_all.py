"""Architecture enforcement: PluginAccessor must not leak beyond bootstrap.

``gateway.community.plugin_accessor`` provides access to the plugin system.
Components must receive plugins via constructor injection, not by reaching
out to the global accessor.

Only ``bootstrap/``, ``config/``, ``logger/``, and ``tracer/`` may import PluginAccessor.
"""

import ast
import warnings
from pathlib import Path

GATEWAY = Path(__file__).resolve().parents[2] / "src" / "gateway" / "community"

# Only these layers may import PluginAccessor — logger and tracer are
# boot-time singleton bridges that expose get_logger_plugin() /
# get_tracer_plugin() via lazy PluginAccessor, same pattern as config/.
_ALLOWED_LAYERS = {"bootstrap", "config", "logger", "tracer"}

# Known pre-existing violations (lazy imports)
_KNOWN_DEBT: dict[str, list[tuple[str, int]]] = {}


def _collect_plugin_accessor_imports() -> dict[str, list[tuple[str, int, str]]]:
    """Scan all production .py files for imports of PluginAccessor."""
    results: dict[str, list[tuple[str, int, str]]] = {}

    for py_file in sorted(GATEWAY.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        rel_path = py_file.relative_to(GATEWAY)
        layer = rel_path.parts[0]

        if layer in _ALLOWED_LAYERS:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module is None:
                continue
            if "plugin_accessor" not in node.module:
                continue

            names = [a.name for a in node.names]
            if "PluginAccessor" not in names:
                continue

            import_text = f"from {node.module} import {', '.join(names)}"
            lineno = getattr(node, "lineno", 0)
            results.setdefault(layer, []).append((str(rel_path), lineno, import_text))

    return results


def _is_known_debt(rel_path: str, lineno: int) -> bool:
    for layer_violations in _KNOWN_DEBT.values():
        for known_path, known_lineno in layer_violations:
            if known_path == rel_path and known_lineno == lineno:
                return True
    return False


def test_no_forbidden_plugin_accessor_imports() -> None:
    """Fail on any NEW import of PluginAccessor outside bootstrap/config."""
    results = _collect_plugin_accessor_imports()
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
            f"\n{len(new_violations)} NEW PluginAccessor import(s) outside of "
            f"bootstrap/config:\n"
            + "\n".join(new_violations)
            + "\n\nComponents must receive plugins via constructor injection, "
            "not by importing PluginAccessor directly."
        )

    if debt_found:
        warnings.warn(
            f"\n{len(debt_found)} known PluginAccessor import(s) outside bootstrap "
            f"(tracked debt):\n" + "\n".join(debt_found),
            stacklevel=1,
        )


def test_plugin_accessor_import_audit() -> None:
    """Audit report of all PluginAccessor imports outside bootstrap/config."""
    results = _collect_plugin_accessor_imports()

    if not results:
        return  # clean — silently pass

    total = sum(len(sites) for sites in results.values())
    lines: list[str] = []
    for layer in sorted(results):
        lines.append(f"  [{layer}] ({len(results[layer])} site(s)):")
        for rel_path, lineno, import_text in sorted(results[layer]):
            tag = " [KNOWN DEBT]" if _is_known_debt(rel_path, lineno) else " [NEW]"
            lines.append(f"    {rel_path}:{lineno}: {import_text}{tag}")

    warnings.warn(
        f"\nPluginAccessor import audit ({total} site(s) outside bootstrap/config):\n"
        + "\n".join(lines),
        stacklevel=1,
    )
