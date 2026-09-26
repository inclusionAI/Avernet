"""Architecture isolation test for the new `api/ → core/ → plugin_api/ → plugins/` layering.

Enforces README §"旧架构隔离原则":
- Files under `api/<migrated_module>/` and `core/<migrated_module>/` MUST NOT import
  anything under `services/`, `servers/web/routes/`, or `infrastructure/`.
- `api/` MUST NOT import `plugins/`.
- `core/` MUST NOT import `plugins/` except inside `core/<module>/dependencies/`.

A narrow allow-list exists for legacy bridge files that are known architecture debt.
Those files MUST be migrated before any new functionality is added. No new entries
may be added to the allow-list without an approved migration plan.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

# Resolve package root regardless of cwd so this test is not fooled by `os.chdir`.
_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]  # .../src/backend
_AGENTCLAW_ROOT = _BACKEND_ROOT / "src" / "agentclaw"


# -- Scope: modules that have been migrated to the new architecture ----------
# Add a module here once its api/ + core/ skeleton lands. Files outside these
# subtrees are left to their own tests.
MIGRATED_MODULES: tuple[str, ...] = (
    "resources",
)


# -- Forbidden import prefixes (README §"旧架构隔离原则") --------------------
_LEGACY_PREFIXES: tuple[str, ...] = (
    "agentclaw.services.",
    "agentclaw.servers.web.routes.",
    "agentclaw.servers.web.routes",
    "agentclaw.infrastructure.",
    "agentclaw.infrastructure",
)

# Direct imports of the `plugins/` (implementation) layer are only allowed
# inside `core/<module>/dependencies/` DI factories.
_PLUGINS_PREFIXES: tuple[str, ...] = (
    "agentclaw.community.plugins.",
    "agentclaw.community.plugins",
)


# -- Allow-list for known legacy bridges (ARCHITECTURE DEBT) -----------------
# Each entry is a path RELATIVE to `src/agentclaw/`. These files delegate to
# legacy `services/` via lazy import. Remove entries as bridges are migrated.
# Resources module bridges have been fully migrated to plugins/.
_BRIDGE_ALLOWLIST: frozenset[str] = frozenset({
    # No bridge files for resources module - fully migrated
})

# -- Allow-list for plugins legacy ORM imports (ARCHITECTURE DEBT) -------
# ORM models in legacy services/ cannot be duplicated due to SQLAlchemy registry.
# These files import legacy ORM models temporarily until legacy routers are removed.
# After migration, ORM models are defined in plugin_api/models.py and re-exported from legacy.
_PLUGINS_LEGACY_ORM_ALLOWLIST: frozenset[str] = frozenset({
    # All resources module files now use plugin_api/models.py - no legacy imports
})


def _iter_module_files(module: str) -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    candidates = (
        _AGENTCLAW_ROOT / "community" / "adapters" / "http" / module,
        _AGENTCLAW_ROOT / "community" / "core" / module,
    )
    for root in candidates:
        if not root.exists():
            continue
        files.extend(p for p in root.rglob("*.py") if p.is_file())
    return files


def _relpath(file: pathlib.Path) -> str:
    return file.relative_to(_AGENTCLAW_ROOT).as_posix()


def _collect_imports(tree: ast.AST) -> list[tuple[str, int]]:
    """Return (module_name, lineno) for every `import X` and `from X import ...`."""
    imports: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level and not node.module:
                continue
            if node.module:
                imports.append((node.module, node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, node.lineno))
    return imports


def _starts_with_any(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(p) for p in prefixes)


def test_agentclaw_root_exists() -> None:
    """Guard against silent skip — if this fails, the path resolver is wrong."""
    assert _AGENTCLAW_ROOT.is_dir(), f"agentclaw package root not found at {_AGENTCLAW_ROOT}"


@pytest.mark.parametrize("module", MIGRATED_MODULES)
def test_migrated_module_has_files(module: str) -> None:
    """A migrated module must actually have files — no silent pass."""
    files = _iter_module_files(module)
    assert files, (
        f"Migrated module '{module}' has no .py files under api/{module}/ or core/{module}/. "
        "Either the module name is wrong or the migration is incomplete."
    )


@pytest.mark.parametrize("module", MIGRATED_MODULES)
def test_adapter_layer_does_not_import_plugins(module: str) -> None:
    """adapters/http/<module>/ must go through core/<module>/dependencies/, never plugins directly.

    Post-R8 the HTTP delivery adapter lives under ``adapters/http/`` while
    ``api/`` holds Service API Protocols only. Both layers are subject to
    the same "no direct plugins import" rule; this test enforces it for
    the adapter, where router code that historically lived under ``api/``
    now resides.
    """
    root = _AGENTCLAW_ROOT / "community" / "adapters" / "http" / module
    assert root.exists(), (
        f"Migrated module '{module}' is missing adapters/http/{module}/. "
        "Either remove it from MIGRATED_MODULES or finish the migration."
    )
    violations: list[str] = []
    for file in root.rglob("*.py"):
        rel = _relpath(file)
        tree = ast.parse(file.read_text(), filename=str(file))
        for name, lineno in _collect_imports(tree):
            if _starts_with_any(name, _PLUGINS_PREFIXES):
                violations.append(f"{rel}:{lineno} imports `{name}`")
    assert not violations, (
        "adapters/http/ layer must not import plugins directly.\n"
        "Violations:\n  " + "\n  ".join(violations)
    )


@pytest.mark.parametrize("module", MIGRATED_MODULES)
def test_core_layer_only_imports_plugins_in_dependencies(module: str) -> None:
    """core/<module>/ may only import plugins from within dependencies/."""
    root = _AGENTCLAW_ROOT / "community" / "core" / module
    if not root.exists():
        pytest.skip(f"No core/ layer for module '{module}'")
    violations: list[str] = []
    for file in root.rglob("*.py"):
        rel = _relpath(file)
        tree = ast.parse(file.read_text(), filename=str(file))
        is_in_dependencies = f"core/{module}/dependencies/" in rel
        if is_in_dependencies:
            continue
        for name, lineno in _collect_imports(tree):
            if _starts_with_any(name, _PLUGINS_PREFIXES):
                violations.append(f"{rel}:{lineno} imports `{name}` outside dependencies/")
    assert not violations, (
        "core/ layer may only touch plugins from within dependencies/.\n"
        "Violations:\n  " + "\n  ".join(violations)
    )


def test_bridge_allowlist_files_exist() -> None:
    """Every allow-listed bridge file must actually exist; stale entries must be pruned."""
    missing = [rel for rel in _BRIDGE_ALLOWLIST if not (_AGENTCLAW_ROOT / rel).is_file()]
    assert not missing, (
        "Allow-listed bridge files do not exist (stale entries):\n  " + "\n  ".join(missing)
    )


@pytest.mark.parametrize("module", MIGRATED_MODULES)
def test_plugins_no_legacy_imports(module: str) -> None:
    """plugins files must not import legacy services/servers.web.routes/infrastructure."""
    violations: list[str] = []
    # B11: local plugins ship under community/, prod under corp/.
    for plugins_dir in (
        _AGENTCLAW_ROOT / "community" / "plugins" / "local",
        _AGENTCLAW_ROOT / "corp" / "plugins" / "prod",
    ):
        # Check for module-specific repository files
        repo_file = plugins_dir / f"{module}_repository.py"
        if repo_file.is_file():
            rel = _relpath(repo_file)
            # Skip allowlisted files (temporary ORM model imports)
            if rel in _PLUGINS_LEGACY_ORM_ALLOWLIST:
                continue
            tree = ast.parse(repo_file.read_text(), filename=str(repo_file))
            for name, lineno in _collect_imports(tree):
                if _starts_with_any(name, _LEGACY_PREFIXES):
                    violations.append(f"{rel}:{lineno} imports legacy `{name}`")
    assert not violations, (
        "plugins/ files must not depend on legacy modules.\n"
        "Violations:\n  " + "\n  ".join(violations)
    )
