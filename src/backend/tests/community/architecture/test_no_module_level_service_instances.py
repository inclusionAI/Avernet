"""Forbid module-level instantiation of service / repository / client / plugin
classes.

The DI refactor moved every collaborator into the injector. A line like

    foo_service = FooService()

at module top-level re-creates a hidden process-wide singleton that
bypasses the injector — exactly the smell the refactor exists to prevent.
Bind the service in a DI module instead.

Detection (AST, top-level statements only):

  Flag any module-level ``Assign`` where the RHS is a ``Call(Name|Attribute)``
  AND either:
    (a) the target name ends with ``_service``, ``_repository``, or ``_repo``
        (case insensitive), OR
    (b) the callee's simple name ends with ``Service``, ``Repository``,
        ``Client``, or ``Plugin``.

Lazy *proxy* objects (e.g. a ``_LazyBotRepository()`` that forwards via
``__getattr__``) are NOT services — they're allowlisted by file path.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]                # .../src/backend
_AGENTCLAW_ROOT = _BACKEND_ROOT / "src" / "agentclaw"

_SCAN_ROOTS = ("core", "plugins")

_SUFFIX_TARGET = re.compile(r".*(_service|_repository|_repo)$", re.IGNORECASE)
_SUFFIX_CALLEE = re.compile(r".*(Service|Repository|Client|Plugin)$")


# ---------------------------------------------------------------------------
# Allowlist — paths under src/agentclaw/ that may keep a flagged top-level
# assignment. Each entry MUST justify itself.
# ---------------------------------------------------------------------------
_ALLOWLIST: dict[str, str] = {
    # ----- pre-existing module-level service instances (legacy debt) -----
    # `file_service = FileService()` — legacy global still imported by old code.
    "core/resources/services/file_service.py":
        "Pre-existing module-level FileService singleton; legacy.",
    # `market_sync_service = MarketSyncService()` — legacy global singleton.
    "core/skill_center/services/market_sync.py":
        "Pre-existing module-level MarketSyncService singleton; legacy.",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rel(path: pathlib.Path) -> str:
    return path.relative_to(_AGENTCLAW_ROOT).as_posix()


def _norm(rel: str) -> str:
    # B11: layers migrate under ``agentclaw/community/<layer>``. The allowlist
    # keys stay layer-relative ("core/..."), so strip the ``community/`` prefix
    # before matching whichever side of the move a file is on.
    return rel[len("community/"):] if rel.startswith("community/") else rel


def _callee_simple_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _flag_top_level_assigns(tree: ast.Module) -> list[tuple[int, str]]:
    """Return (lineno, message) for every offending top-level Assign."""
    findings: list[tuple[int, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        callee = _callee_simple_name(node.value)
        if callee is None:
            continue
        for tgt in node.targets:
            if not isinstance(tgt, ast.Name):
                continue
            name = tgt.id
            if _SUFFIX_TARGET.match(name):
                findings.append((node.lineno, f"`{name} = {callee}(...)` (target-name suffix)"))
            elif _SUFFIX_CALLEE.match(callee):
                findings.append((node.lineno, f"`{name} = {callee}(...)` (callee-name suffix)"))
    return findings


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_no_module_level_service_instances() -> None:
    """Fail on new module-level service/repository/client/plugin instantiations."""
    violations: list[str] = []
    for root in _SCAN_ROOTS:
        # B11: core/ and plugins/ now live under BOTH community/ and corp/ (and
        # possibly the legacy top-level mid-migration). UNION every location —
        # Rule 14 is universal, so a module-level singleton in corp/ code must be
        # caught too. (A fallback that stopped at the first existing dir would
        # silently skip corp/ once the legacy path was gone.)
        for scan_root in (
            _AGENTCLAW_ROOT / root,
            _AGENTCLAW_ROOT / "community" / root,
            _AGENTCLAW_ROOT / "corp" / root,
        ):
            if not scan_root.is_dir():
                continue
            for path in scan_root.rglob("*.py"):
                if not path.is_file():
                    continue
                rel = _rel(path)
                if _norm(rel) in _ALLOWLIST:
                    continue
                try:
                    tree = ast.parse(path.read_text(), filename=str(path))
                except SyntaxError:
                    continue
                for lineno, msg in _flag_top_level_assigns(tree):
                    violations.append(f"{rel}:{lineno}  {msg}")
    assert not violations, (
        "Module-level service/repository/client/plugin instances are forbidden — "
        "bind the dependency in a DI module instead.\n"
        "If the case is genuinely a lazy proxy or intentional legacy debt, add it "
        "to `_ALLOWLIST` in this test with a one-line justification.\n"
        "Violations found:\n  " + "\n  ".join(violations)
    )


@pytest.mark.unit
def test_module_level_service_allowlist_entries_still_exist() -> None:
    """Stale allowlist entries must be pruned."""
    missing = [
        rel for rel in _ALLOWLIST
        if not (_AGENTCLAW_ROOT / rel).is_file()
        and not (_AGENTCLAW_ROOT / "community" / rel).is_file()
    ]
    assert not missing, (
        "Allowlisted files no longer exist. Remove stale entries from "
        "`_ALLOWLIST`:\n  " + "\n  ".join(missing)
    )
