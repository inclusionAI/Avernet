"""Architecture enforcement: no private (``_``-prefixed) exports in ``__init__.py``.

Per convention:
  ❌  ``from ._module import _private_func``  (import makes it a public attr)
  ❌  ``__all__ = ["PublicThing", "_private_helper"]``
  ✅  ``_internal = PluginAccessor(...)``  (module-level private var, NOT in __all__)

Only names that are *actually exported* — via ``__all__``, a module-level
``import`` / ``from .. import``, or a top-level function/class definition —
are checked.  Module-level private assignments (``_x = ...``) that sit
outside ``__all__`` are legitimate implementation details.

Dunder names (``__version__``, ``__author__``, etc.) are always allowed.

Currently 5 __init__.py files have known private exports:
- core/service/config_manage/__init__.py: _record_to_response
- core/service/device_manage/__init__.py: _decrypt_header_rule_values,
  _encrypt_header_rule_values, _safe_format_hook
- core/service/template_manage/__init__.py: _ensure_api_key_encrypted,
  _record_to_response
- core/service/tenant_manage/__init__.py: _record_to_response
- spi/bot/teclaw/__init__.py: _BotCreateResult, _BotDestroyResult,
  _BotInfo, _BotRestartResult, _BotUpdateResult
"""

import ast
import warnings
from pathlib import Path

SECBAAS = Path(__file__).resolve().parents[2] / "src" / "secbaas" / "community"

_KNOWN_PRIVATE_EXPORTS: dict[str, set[str]] = {
    "bootstrap/__init__.py": {"_inject_enterprise_plugins"},
    "core/repository/__init__.py": {"_is_expected_distributed_lock_conflict"},
    "core/service/config_manage/__init__.py": {"_record_to_response"},
    "core/service/device_manage/__init__.py": {
        "_decrypt_header_rule_values",
        "_encrypt_header_rule_values",
        "_safe_format_hook",
    },
    "core/service/template_manage/__init__.py": {
        "_ensure_api_key_encrypted",
        "_record_to_response",
    },
    "core/service/tenant_manage/__init__.py": {"_record_to_response"},
    "plugins/logger/bare/__init__.py": {"_TraceIdFilter", "_resolve_log_level"},
    "spi/bot/teclaw/__init__.py": {
        "_BotCreateResult",
        "_BotDestroyResult",
        "_BotInfo",
        "_BotRestartResult",
        "_BotUpdateResult",
    },
}


def _is_dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def _collect_private_exports(init_file: Path) -> set[str]:
    """Return the set of ``_``-prefixed names made visible in an __init__.py.

    Only names that are *actually exported* are collected:
      - Names in ``__all__``
      - Names imported at module level (``import`` / ``from ... import``)
      - Top-level function / class definitions

    Module-level private assignments (``_x = ...``) that are NOT in ``__all__``
    are skipped — they are internal implementation details.
    """
    try:
        tree = ast.parse(init_file.read_text())
    except SyntaxError:
        return set()

    all_names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
            and isinstance(node.value, ast.List)
        ):
            all_names.update(
                elt.value for elt in node.value.elts if isinstance(elt, ast.Constant)
            )

    private: set[str] = set()

    for node in ast.iter_child_nodes(tree):
        # 1. __all__ entries that start with _ (and are not dunder)
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
            and isinstance(node.value, ast.List)
        ):
            for elt in node.value.elts:
                if not isinstance(elt, ast.Constant):
                    continue
                name = elt.value
                if name.startswith("_") and not _is_dunder(name):
                    private.add(name)

        # 2. ImportFrom: ``from ._foo import _bar, baz as _qux``
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.asname or alias.name
                if name.startswith("_") and not _is_dunder(name):
                    private.add(name)

        # 3. Import: ``import _module as _alias``
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name
                if name.startswith("_") and not _is_dunder(name):
                    private.add(name)

        # 4. Top-level function / class definitions
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name.startswith("_") and not _is_dunder(node.name):
                private.add(node.name)

        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                name = target.id
                if name.startswith("_") and not _is_dunder(name) and name in all_names:
                    private.add(name)

    return private


def test_no_private_exports_in_init():
    """No __init__.py may export names that start with ``_``.

    Emits warnings for known debt files; fails for new violations.
    """
    violations: list[str] = []
    warned: list[str] = []

    for init_file in sorted(SECBAAS.rglob("__init__.py")):
        if "__pycache__" in str(init_file):
            continue

        rel = init_file.relative_to(SECBAAS).as_posix()
        private = _collect_private_exports(init_file)

        if not private:
            continue

        known = _KNOWN_PRIVATE_EXPORTS.get(rel, set())
        new = private - known
        already_known = private & known

        if already_known:
            warned.append(rel)
        if new:
            violations.append(f"  {rel}: {', '.join(sorted(new))}")

    for rel in sorted(warned):
        warnings.warn(
            f"\nKnown private exports in {rel} (pre-existing debt)", stacklevel=1
        )

    if violations:
        raise AssertionError(
            f"\n{len(violations)} __init__.py file(s) export private (_-prefixed) names:\n"
            + "\n".join(sorted(violations))
            + "\n\nRemove private exports from the public surface. "
            "If an export must remain private, put it in a private module "
            "(_foo.py) instead."
        )
