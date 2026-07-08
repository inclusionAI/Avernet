"""Architecture compliance test for the agentclaw four-layer structure.

Enforces the one-way dependency rules:
  api/ → core/ → plugin_api/ → plugins/

Rules:
  - api/    : may import core/, api/ (same layer); must NOT import plugins/
              EXCEPT: adapters/http/app.py and api/*/dependencies.py (Composition Root / DI factories)
  - core/   : may import plugin_api/, core/ (same layer); must NOT import api/ or plugins/
              EXCEPT: core/*/dependencies/*.py (DI factories)
  - plugin_api/: must NOT import api/, core/, or plugins/
              EXCEPT: plugin_api/models.py importing core.base (canonical SQLAlchemy Base)
  - plugins/: may import plugin_api/ and core/; must NOT import api/
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]          # .../src/backend
_AGENTCLAW_ROOT = _BACKEND_ROOT / "src" / "agentclaw"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _all_py_files(layer: str) -> list[pathlib.Path]:
    # B11: the four layers are migrating under ``agentclaw/community/<layer>``.
    # During the migration a layer may live at either location — union both so
    # the purity guard keeps scanning it whichever side of the move it is on.
    files: list[pathlib.Path] = []
    for root in (_AGENTCLAW_ROOT / layer, _AGENTCLAW_ROOT / "community" / layer):
        if root.is_dir():
            files.extend(p for p in root.rglob("*.py") if p.is_file())
    return files


def _rel(path: pathlib.Path) -> str:
    # Normalise the ``community/`` prefix out of the layer-relative path so the
    # whitelist keys ("plugin_api/models.py") match regardless of subtree (B11).
    rel = path.relative_to(_AGENTCLAW_ROOT).as_posix()
    return rel[len("community/"):] if rel.startswith("community/") else rel


def _collect_imports(path: pathlib.Path) -> list[tuple[str, int]]:
    """Return (module_dotted_name, lineno) for every import statement."""
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError:
        return []
    results: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                results.append((node.module, node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                results.append((alias.name, node.lineno))
    return results


def _layer_of(module: str) -> str | None:
    """Return the top-level layer name for an agentclaw.X import, or None."""
    # B11: layers may be imported at their new ``agentclaw.community.<layer>``
    # home or the legacy ``agentclaw.<layer>`` path during the migration.
    m = re.match(
        r"agentclaw\.(?:community\.)?(api|core|plugins|plugin_api)(?:\.|$)", module
    )
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# White-list: (rel_path, imported_layer) pairs that are known-allowed exceptions
# ---------------------------------------------------------------------------

# Exact rel-path exceptions (layer-check skipped entirely for these files)
_FULL_FILE_EXCEPTIONS: frozenset[str] = frozenset({
    # Composition Root — assembles all DI wiring, may import plugins
    "adapters/http/app.py",
    # Health check router queries infrastructure state directly (LocalProcessManager)
    "adapters/http/system/router.py",
    # core/services/identity.py re-exports api/ types; known debt
    "core/services/identity.py",
    # plugin_api/models.py imports core.base (canonical SQLAlchemy Base registry)
    "plugin_api/models.py",
})

# api/*/dependencies.py: DI factory files inside api sub-modules
_API_DEPENDENCIES_PATTERN = re.compile(r"^api/[^/]+/dependencies\.py$")

# core/**/dependencies/**/*.py: DI factory files inside core sub-modules
_CORE_DEPENDENCIES_PATTERN = re.compile(r"^core/(?:[^/]+/)+dependencies/")

# Specific (file, imported_module_prefix) exceptions
_IMPORT_EXCEPTIONS: frozenset[tuple[str, str]] = frozenset({
    # device_lifecycle.py uses a lazy import of core.bot_management.services
    (
        "plugins/local/device_lifecycle.py",
        "agentclaw.community.core.bot_management.services",
    ),
    # collaborator_service.py imports PassportPlugin from plugins for permission checking
    (
        "core/bot_collaborator/services/collaborator_service.py",
        "agentclaw.community.plugins.passport",
    ),
    # BotBuildService is wired with the Channel service Protocol for build-time
    # OpenClaw config generation; keep this as a test-only exception for the
    # current code shape.
    (
        "core/service_bot/services/bot_build_service.py",
        "agentclaw.community.api.channel_service",
    ),
    # BotService consumes the PolicyService API Protocol (defined in api/,
    # implemented in core/access) to resolve the per-owner bot-count ceiling.
    # Same cross-core consumption shape as bot_build_service above; kept as a
    # test-only exception for the current code shape.
    (
        "core/bot_management/services/bot_service.py",
        "agentclaw.community.api.policy_service",
    ),
    # BetaQuotaService consumes the PolicyService API Protocol (defined in api/,
    # implemented in core/access) to whitelist the caller on quota adjust.
    # Same cross-core consumption shape as bot_service above; kept as a
    # test-only exception for the current code shape.
    (
        "core/common_config/beta_quota_service.py",
        "agentclaw.community.api.policy_service",
    ),
    # PublishApprovalService implements the API Protocol defined in api/publish_approval.py.
    # Core services implement API Protocols for DI wiring; exceptions document this pattern.
    (
        "core/service_bot/services/publish_approval_service.py",
        "agentclaw.community.api.publish_approval",
    ),
})


def _is_allowed(rel: str, imported_layer: str, module: str) -> bool:
    """Return True when an import would normally violate a rule but is whitelisted."""
    if rel in _FULL_FILE_EXCEPTIONS:
        return True
    if _API_DEPENDENCIES_PATTERN.match(rel):
        return True
    if _CORE_DEPENDENCIES_PATTERN.match(rel):
        return True
    for exc_rel, exc_prefix in _IMPORT_EXCEPTIONS:
        if rel == exc_rel and module.startswith(exc_prefix):
            return True
    return False


# ---------------------------------------------------------------------------
# Violation collectors per layer rule
# ---------------------------------------------------------------------------

def _collect_violations(
    layer: str,
    forbidden_imports: list[str],
) -> list[str]:
    """
    Scan all .py files under `layer/` and return human-readable violation strings
    for any import whose target layer is in `forbidden_imports`, unless whitelisted.
    """
    violations: list[str] = []
    for path in _all_py_files(layer):
        rel = _rel(path)
        for module, lineno in _collect_imports(path):
            imported_layer = _layer_of(module)
            if imported_layer in forbidden_imports:
                if not _is_allowed(rel, imported_layer, module):
                    violations.append(
                        f"{rel}:{lineno}  imports agentclaw.{imported_layer} "
                        f"({module!r})"
                    )
    return violations


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_agentclaw_root_exists() -> None:
    """Sanity-guard: fail loudly if the package root path is wrong."""
    assert _AGENTCLAW_ROOT.is_dir(), (
        f"agentclaw package root not found at {_AGENTCLAW_ROOT}. "
        "Check the path constants in this file."
    )


@pytest.mark.unit
def test_api_layer_does_not_import_plugins() -> None:
    """api/ must not directly import plugins/ (except Composition Root and DI files)."""
    violations = _collect_violations("api", ["plugins"])
    assert not violations, (
        "api/ layer must not import plugins/ directly.\n"
        "Allowed exceptions: adapters/http/app.py (Composition Root) and api/*/dependencies.py (DI factories).\n"
        "Violations found:\n  " + "\n  ".join(violations)
    )


@pytest.mark.unit
def test_core_layer_does_not_import_api() -> None:
    """core/ must not import api/ (except known bridge files in the white-list)."""
    violations = _collect_violations("core", ["api"])
    assert not violations, (
        "core/ layer must not import api/.\n"
        "core/ may only depend on plugin_api/ and the same core/ layer.\n"
        "Violations found:\n  " + "\n  ".join(violations)
    )


@pytest.mark.unit
def test_core_layer_does_not_import_plugins_outside_dependencies() -> None:
    """core/ may only import plugins/ from within its DI factory directories."""
    violations = _collect_violations("core", ["plugins"])
    assert not violations, (
        "core/ layer must not import plugins/ outside DI factory directories.\n"
        "Allowed: core/*/dependencies/**/*.py.\n"
        "Violations found:\n  " + "\n  ".join(violations)
    )


@pytest.mark.unit
def test_plugins_layer_does_not_import_upper_layers() -> None:
    """plugin_api/ must not import api/, core/, or plugins/."""
    violations = _collect_violations("plugin_api", ["api", "core", "plugins"])
    assert not violations, (
        "plugin_api/ layer must not import api/, core/, or plugins/.\n"
        "plugin_api/ defines abstract interfaces only.\n"
        "Exception: plugin_api/models.py may import core.base (canonical SQLAlchemy Base).\n"
        "Violations found:\n  " + "\n  ".join(violations)
    )


@pytest.mark.unit
def test_plugins_layer_does_not_import_api() -> None:
    """plugins/ must not import api/."""
    violations = _collect_violations("plugins", ["api"])
    assert not violations, (
        "plugins/ layer must not import api/.\n"
        "plugins/ may depend on plugin_api/ and core/, but never api/.\n"
        "Violations found:\n  " + "\n  ".join(violations)
    )


@pytest.mark.unit
def test_exception_files_still_exist() -> None:
    """Stale white-list entries must be pruned — every whitelisted file must exist."""
    # B11: a layer-relative key ("plugin_api/models.py") may resolve under the
    # new ``community/`` home or the legacy root during the migration.
    missing = [
        rel for rel in _FULL_FILE_EXCEPTIONS
        if not (_AGENTCLAW_ROOT / rel).is_file()
        and not (_AGENTCLAW_ROOT / "community" / rel).is_file()
    ]
    assert not missing, (
        "White-listed files no longer exist. Remove stale entries from "
        "_FULL_FILE_EXCEPTIONS:\n  " + "\n  ".join(missing)
    )
