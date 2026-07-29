"""Architecture guard for the task module (Phase 0.9, Rule II/IV).

Enforces the four-layer one-way dependency for the new task module:

  api/task  ──▶  core/task/domain  ──▶  (stdlib only)
                                      ◀── plugin_api (infra Protocols)
  adapters/http/task  ──▶  api/task
  plugins/community/task  ──▶  api/task + core/task/domain
  di/.../task  ──▶  plugins/community/task + api/task

Forbidden (AST-based, both top-level + function-local imports):
- ``api/task`` and ``core/task/domain`` must NOT import ``plugins`` or
  ``adapters`` or ``di`` (no upward/outward leaks).
- ``core/task/domain`` must stay pure: no ``plugins`` / ``adapters`` / ``di``
  / ``api`` imports (domain depends on nothing in-app except its own siblings).

This guards the Phase 0 skeleton so later phases can't quietly break layering.
"""
from __future__ import annotations

import ast
import pathlib

_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]                       # .../src/backend
_SRC = _BACKEND_ROOT / "src" / "agentclaw" / "community"

_API_TASK = _SRC / "api" / "task"
_CORE_DOMAIN = _SRC / "core" / "task" / "domain"

_FORBIDDEN_FOR_DOMAIN = ("plugins", "adapters", "di")
_FORBIDDEN_FOR_API = ("plugins", "adapters")


def _import_targets(path: pathlib.Path) -> list[str]:
    """Return all top-level + function-local import module strings."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod:
                targets.append(mod)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                targets.append(alias.name)
    return targets


def _hits_any(modules: list[str], forbidden: tuple[str, ...]) -> list[str]:
    hits = []
    for m in modules:
        # only flag imports of *this* codebase's plugins/adapters/di, not stdlib
        if not m.startswith("agentclaw"):
            continue
        for bad in forbidden:
            seg = f"agentclaw.community.{bad}"
            if m == seg or m.startswith(seg + "."):
                hits.append(m)
    return hits


def _py_files(d: pathlib.Path) -> list[pathlib.Path]:
    if not d.exists():
        return []
    return sorted(p for p in d.rglob("*.py") if p.name != "__init__.py")


def test_core_task_domain_is_pure():
    bad_files: list[str] = []
    for py in _py_files(_CORE_DOMAIN):
        hits = _hits_any(_import_targets(py), _FORBIDDEN_FOR_DOMAIN)
        if hits:
            bad_files.append(f"{py.relative_to(_BACKEND_ROOT)} -> {hits}")
    assert not bad_files, (
        "core/task/domain must not import plugins/adapters/di:\n" + "\n".join(bad_files)
    )


def test_api_task_does_not_import_plugins_or_adapters():
    bad_files: list[str] = []
    for py in _py_files(_API_TASK):
        hits = _hits_any(_import_targets(py), _FORBIDDEN_FOR_API)
        if hits:
            bad_files.append(f"{py.relative_to(_BACKEND_ROOT)} -> {hits}")
    assert not bad_files, (
        "api/task must not import plugins/adapters:\n" + "\n".join(bad_files)
    )


def test_task_module_files_exist():
    # sanity: Phase 0 skeleton present (Phase 4 relocated the task Protocols
    # from api/task/protocols.py to core/task/protocols.py so core services +
    # plugins may depend on them without importing api; api/task/__init__.py
    # re-exports them as the DI binding keys).
    assert (_SRC / "core" / "task" / "protocols.py").exists()
    assert (_API_TASK / "__init__.py").exists()
    assert (_CORE_DOMAIN / "models.py").exists()
    assert (_SRC / "adapters" / "http" / "task" / "router.py").exists()
    assert (_SRC / "plugins" / "community" / "task" / "__init__.py").exists()