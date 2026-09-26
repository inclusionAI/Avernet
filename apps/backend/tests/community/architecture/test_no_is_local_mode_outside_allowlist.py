"""Forbid ``is_local_mode()`` / ``_is_sqlite_mode()`` outside an allowlist.

Rule 14 (``docs/arch/arch.rules.md``) — Configuration-Driven Wiring —
forbids services from re-reading environment variables to decide
behavior. The single source of truth is the ``DEPLOY_PROFILE`` switch,
resolved once at the composition root via ``DeployProfile.detect()`` and
fed into ``build_injector(profile=...)``.

Downstream consumers must depend on:
- An injected Plugin Protocol whose impl varies by mode, **or**
- A resolved value/callable supplied by the DI factory.

This test fails on any file outside the allowlist that:
- Calls ``is_local_mode`` / ``_is_sqlite_mode`` (bare or attribute call), **or**
- Imports either name via ``from ... import ...``, **or**
- Defines a top-level function with either name.

Local helpers that *accept a dependency parameter* (e.g.
``core/channel/json_config_utils.py:_is_local_mode(device_fs)``) are
NOT flagged: those are DI-driven introspection, not env-var reads.
Detection: AST-only; whitelisted helpers are recognized by having
parameters in their signature.

Adding to ``_ALLOWLIST`` is a review-level decision. Each entry MUST
have a one-line justification. The allowlist will tighten as Rule 14
migration progresses; the final state is exactly one path
(``utils/env_utils.py``, which defines the ``is_local_mode`` shim) — and
that shim itself goes away once its last caller (the devices cluster)
stops branching on "am I local".
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]                # .../src/backend
_AGENTCLAW_ROOT = _BACKEND_ROOT / "src" / "agentclaw"


# ---------------------------------------------------------------------------
# Allowlist — paths under src/agentclaw/ that may reference is_local_mode /
# _is_sqlite_mode. Each entry MUST have a one-line justification.
#
# This is the documented final state. The only sites permitted to touch
# the mode env vars are the helper that defines the read and the typed
# config that consumes it once at the composition root. Everywhere else
# branches on a typed config from ConfigModule (e.g. WorkspaceConfig)
# or on the plugin implementation injected by DI.
# ---------------------------------------------------------------------------
_ALLOWLIST: dict[str, str] = {
    "utils/env_utils.py": (
        "Defines is_local_mode (the profile-derived shim). Sole legitimate "
        "reader of DEPLOY_PROFILE for the local-mode question."
    ),
    "core/workspace/path_factory.py": (
        "Pre-existing mode read (was RuntimeMode.detect().is_local) "
        "consolidated onto is_local_mode by B1; removed with the workspace "
        "cluster's Rule 14 migration."
    ),
    "core/skill_center/services/skill_set_service.py": (
        "Pre-existing mode read (was RuntimeMode.detect().is_local) "
        "consolidated onto is_local_mode by B1; removed with the skill_center "
        "cluster's Rule 14 migration."
    ),
}


def _norm(rel: str) -> str:
    # B11: layers are migrating under ``agentclaw/community/<layer>``. The
    # allowlist keys stay layer-relative ("utils/env_utils.py"), so strip the
    # ``community/`` prefix before matching them whichever side of the move a
    # file is on.
    return rel[len("community/"):] if rel.startswith("community/") else rel


_FORBIDDEN_NAMES = {"is_local_mode", "_is_sqlite_mode"}

# Retired mode env vars — replaced by the single ``DEPLOY_PROFILE`` switch
# (B1). Kept forbidden so they cannot be reintroduced; nothing reads them.
_FORBIDDEN_ENV_VARS = {"LOCAL_DEV_MODE", "DATABASE_MODE"}


def _violations_in_file(path: pathlib.Path) -> list[str]:
    """Return a list of ``"<rel_path>:<line> <kind> <name>"`` strings."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    rel = str(path.relative_to(_AGENTCLAW_ROOT))
    out: list[str] = []

    for node in ast.walk(tree):
        # Call: bare ``is_local_mode()`` / attribute ``mod.is_local_mode()``,
        # plus os.getenv("LOCAL_DEV_MODE"|"DATABASE_MODE", ...) and the
        # equivalent ``os.environ.get(...)`` forms.
        if isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name in _FORBIDDEN_NAMES:
                out.append(f"{rel}:{node.lineno} call {name}")
            elif (
                isinstance(fn, ast.Attribute)
                and fn.attr in ("getenv", "get")
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value in _FORBIDDEN_ENV_VARS
            ):
                out.append(
                    f"{rel}:{node.lineno} env-read {node.args[0].value}"
                )
        # from ... import is_local_mode (or _is_sqlite_mode).
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in _FORBIDDEN_NAMES:
                    out.append(f"{rel}:{node.lineno} import {alias.name}")
        # Function/method definition with a forbidden name.
        elif isinstance(node, ast.FunctionDef):
            if node.name in _FORBIDDEN_NAMES:
                # Allow local helpers that take parameters — those are
                # DI-driven introspection, not env-var reads. The canonical
                # is_local_mode in utils/env_utils.py takes no args.
                params = node.args
                takes_args = bool(
                    params.args or params.posonlyargs or params.kwonlyargs
                    or params.vararg or params.kwarg
                )
                if not takes_args:
                    out.append(f"{rel}:{node.lineno} def {node.name}")

    return out


def test_no_is_local_mode_calls_outside_allowlist():
    forbidden: list[str] = []
    for py in _AGENTCLAW_ROOT.rglob("*.py"):
        rel = str(py.relative_to(_AGENTCLAW_ROOT))
        if _norm(rel) in _ALLOWLIST:
            continue
        forbidden.extend(_violations_in_file(py))

    if forbidden:
        pytest.fail(
            "Found is_local_mode/_is_sqlite_mode references outside the "
            "Rule 14 allowlist:\n  "
            + "\n  ".join(forbidden)
            + "\n\nFix: depend on an injected Plugin (whose impl varies by "
            "mode) or accept a resolved value from the DI factory. Mode "
            "should never re-enter core/ via env-var reads. If the site "
            "genuinely belongs in the allowlist (rare), add it to "
            "_ALLOWLIST with a one-line justification."
        )


def test_allowlist_paths_exist():
    """Every allowlist entry must point at a real file. Stale entries hide
    coverage gaps."""
    missing = [
        rel for rel in _ALLOWLIST
        if not (_AGENTCLAW_ROOT / rel).exists()
        and not (_AGENTCLAW_ROOT / "community" / rel).exists()
    ]
    if missing:
        pytest.fail(
            "Allowlist entries no longer point at real files (delete them): "
            + ", ".join(missing)
        )
