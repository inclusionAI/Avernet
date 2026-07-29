"""Forbid the ``get_app_injector()`` service-locator pattern outside ``di/``.

The DI refactor (Steps 1-4) replaced runtime ``get_app_injector().get(T)``
lookups with proper injection:

  * **Routes** receive deps via ``Injected(T)`` route-signature params.
  * **Service classes** receive deps via ``@inject __init__`` constructors.
  * **Helpers** take deps as function parameters (caller passes them down).
  * **Boot-time callbacks** (middleware, lifespan handlers, background
    tasks) capture deps at registration time via constructor or closure.

The few remaining call sites are *bootstrap* points — places where the
injector boundary itself lives, or where eliminating the call would
require an architectural change beyond the scope of the original
refactor (cycle-breakers, third-party-controlled webhook entrypoints,
pre-DI legacy fallbacks). Every retained site is listed in
``_ALLOWLIST`` below with a one-line justification.

**Adding a new entry is a review-level decision.** New ``get_app_injector()``
calls outside ``di/`` are forbidden by this guard. If a new feature needs
a dep, use one of the four injection mechanisms above.

Detection: AST-only. A violation is any ``Call`` node whose callee is
``get_app_injector`` (or ``X.get_app_injector``) inside a file under
``src/agentclaw/`` other than ``src/agentclaw/di/``.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]                # .../src/backend
_AGENTCLAW_ROOT = _BACKEND_ROOT / "src" / "agentclaw"


# ---------------------------------------------------------------------------
# Allowlist — paths under src/agentclaw/ that may keep get_app_injector().
# Each entry MUST have a one-line justification. The DI package itself
# (di/**) is excluded wholesale via the directory check below — it is the
# implementation of the injector and naturally references its own factory.
# ---------------------------------------------------------------------------
_ALLOWLIST: dict[str, str] = {
    "core/devices/services/device_service.py": (
        "DeviceService.get_device_connection() needs injector to resolve "
        "CollaboratorService for permission checking. TODO: refactor to accept "
        "service in __init__ or pass from caller."
    ),
    "core/nas_usage/service.py": (
        "NasUsageService needs DatabasePlugin for cooldown checks and DB writes. "
        "Background task pattern requires service locator."
    ),
    "adapters/http/task/router.py": (
        "WS /api/tasks/{task_id}/graph/stream endpoint has no FastAPI request "
        "scope, so Injected(T) is unavailable; resolves TaskService via the "
        "app injector at connection time (canvas graph stream bootstrap)."
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rel(path: pathlib.Path) -> str:
    rel = path.relative_to(_AGENTCLAW_ROOT).as_posix()
    # B11: layers migrate under ``agentclaw/community/<layer>``. Strip the
    # ``community/`` prefix so layer-relative allowlist keys ("core/...") match
    # whichever side of the move a file is on.
    return rel[len("community/"):] if rel.startswith("community/") else rel


def _is_get_app_injector_call(node: ast.AST) -> bool:
    """True iff ``node`` is a Call whose callee is ``get_app_injector``."""
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    if isinstance(fn, ast.Name) and fn.id == "get_app_injector":
        return True
    if isinstance(fn, ast.Attribute) and fn.attr == "get_app_injector":
        return True
    return False


def _service_locator_lines(tree: ast.Module) -> list[int]:
    """Return line numbers of every ``get_app_injector()`` call in the tree."""
    hits: list[int] = []
    for node in ast.walk(tree):
        if _is_get_app_injector_call(node):
            hits.append(node.lineno)
    return hits


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_no_service_locator_calls_outside_di() -> None:
    """Forbid new ``get_app_injector()`` call sites outside ``di/``.

    The allowlist above enumerates every retained bootstrap site. Any new
    file containing a call — or any new call in an unlisted file — is a
    violation. To fix: inject the dep via constructor (``@inject __init__``),
    route param (``Injected(T)``), or function parameter. As a last resort,
    add the file to ``_ALLOWLIST`` with a one-line justification.
    """
    violations: list[str] = []
    for path in _AGENTCLAW_ROOT.rglob("*.py"):
        if not path.is_file():
            continue
        rel = _rel(path)
        # The di/ package implements the injector — references are expected.
        if rel.startswith("di/"):
            continue
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            continue
        hits = _service_locator_lines(tree)
        if not hits:
            continue
        if rel in _ALLOWLIST:
            continue
        for lineno in hits:
            violations.append(f"{rel}:{lineno}  get_app_injector() call")
    assert not violations, (
        "Service-locator pattern is forbidden outside di/.\n"
        "Use constructor injection (@inject __init__), route-param injection "
        "(Injected(T)), or function-param passing instead.\n"
        "If this is a genuine bootstrap site, add the file to `_ALLOWLIST` "
        "with a one-line justification.\n"
        "Violations:\n  " + "\n  ".join(violations)
    )


@pytest.mark.unit
def test_service_locator_allowlist_entries_still_have_calls() -> None:
    """Stale allowlist entries must be pruned.

    Every allowlisted file must still contain at least one
    ``get_app_injector()`` call; otherwise the entry is dead weight and
    masks future regressions.
    """
    stale: list[str] = []
    for rel in _ALLOWLIST:
        path = _AGENTCLAW_ROOT / rel
        if not path.is_file():
            path = _AGENTCLAW_ROOT / "community" / rel
        if not path.is_file():
            stale.append(f"{rel}  (file missing)")
            continue
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            continue
        if not _service_locator_lines(tree):
            stale.append(f"{rel}  (no get_app_injector() calls remain)")
    assert not stale, (
        "Allowlisted files no longer need the exception. Remove them from "
        "`_ALLOWLIST`:\n  " + "\n  ".join(stale)
    )
