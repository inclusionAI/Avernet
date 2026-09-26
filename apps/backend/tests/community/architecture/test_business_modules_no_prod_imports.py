"""The ratchet: business DI modules must not import ``agentclaw.corp.plugins.prod``.

B1 structural rule — every DI module is either:

- a **business module** — profile-independent wiring, installed by
  ``build_injector``'s base list for *every* profile. It must not import
  ``plugins.prod`` (or any profile's plugin), so selecting the ``community``
  profile never drags the company-internal import tree in via the base list; or
- an **infra-binding (column) module** — selected by ``modules_for(profile)``,
  imports only its profile's plugin. These are *excluded* from this check:
  ``infrastructure_module.py`` (the ``corp`` column) and ``testing_*.py`` (the
  ``test`` / ``singlebox`` columns) legitimately bind prod / local impls.

This guard fails when a business (base-list) module imports
``agentclaw.corp.plugins.prod``. The allowlist holds the modules still on the
transitional "base + override" shape; **each entry is removed by its owning
B-SDD as that concern decomposes into per-concern column modules** — the
ratchet that drives the ``community`` profile toward zero prod imports.

Detection is AST-based and catches both top-level and function-local imports.
"""
from __future__ import annotations

import ast
import pathlib


_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]                       # .../src/backend
_MODULES_DIR = _BACKEND_ROOT / "src" / "agentclaw" / "community" / "di" / "modules"

_FORBIDDEN_IMPORT_PREFIX = "agentclaw.corp.plugins.prod"


# ---------------------------------------------------------------------------
# Allowlist — business modules that still import ``plugins.prod`` while on the
# transitional base+override shape. Each entry MUST name the B-SDD that
# decomposes it. The allowlist shrinks to empty as B6/B7 land; removing the
# last entry means the community profile imports zero prod via the base list.
# ---------------------------------------------------------------------------
_ALLOWLIST: dict[str, str] = {}


def _is_column_module(name: str) -> bool:
    """Column (infra-binding) modules are excluded — they may bind plugins."""
    return name == "infrastructure_module.py" or name.startswith("testing_")


def _imports_prod(path: pathlib.Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == _FORBIDDEN_IMPORT_PREFIX or mod.startswith(
                _FORBIDDEN_IMPORT_PREFIX + "."
            ):
                return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _FORBIDDEN_IMPORT_PREFIX or alias.name.startswith(
                    _FORBIDDEN_IMPORT_PREFIX + "."
                ):
                    return True
    return False


def test_business_modules_do_not_import_plugins_prod():
    offenders: list[str] = []
    stale_allowlist: list[str] = []

    business_files = sorted(
        p
        for p in _MODULES_DIR.glob("*.py")
        if not _is_column_module(p.name)
    )
    seen = {p.name for p in business_files}

    for path in business_files:
        if _imports_prod(path):
            if path.name not in _ALLOWLIST:
                offenders.append(path.name)

    # Keep the allowlist honest: an entry that no longer imports prod (its
    # B-SDD decomposed it) must be removed so the ratchet actually tightens.
    for name in _ALLOWLIST:
        if name not in seen or not _imports_prod(_MODULES_DIR / name):
            stale_allowlist.append(name)

    assert not offenders, (
        "Business (base-list) DI modules must not import "
        f"`{_FORBIDDEN_IMPORT_PREFIX}` — that pulls the company-internal import "
        "tree into every profile, breaking community isolation. Decompose the "
        "concern into a per-concern column module (modules_for), or — only if "
        "genuinely transitional — add it to _ALLOWLIST with its owning B-SDD.\n"
        "Offenders:\n  " + "\n  ".join(offenders)
    )
    assert not stale_allowlist, (
        "These _ALLOWLIST entries no longer import "
        f"`{_FORBIDDEN_IMPORT_PREFIX}` (or no longer exist) — remove them so "
        "the ratchet tightens:\n  " + "\n  ".join(stale_allowlist)
    )
