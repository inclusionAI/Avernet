"""Enforce Rule 25 — every Plugin Protocol has a conformance test suite.

Discovers Plugin Protocols (same walk as ``test_plugin_pairing``) and
requires a matching suite file under ``tests/contracts/test_<module>.py``.
The suite must exist and contain at least one ``test_*`` function.

``EXEMPT_PROTOCOLS`` is the migration safety valve — a set of plugin
module names whose suite is not yet authored. Each commit drains it.
At G7 it becomes ``frozenset()`` and the test enforces strictly.

Stale exemptions (entries that don't match a real Plugin module) also
fail — catches typos and removals.

Rule 25's "conformance" here means *consumer ↔ Protocol* conformance:
the local impl is the executable spec, and the suite exercises an
upper-layer consumer with the local impl injected via the ``world``
fixture. See ``docs/arch/protocol-contract-tests.md``.
"""
from __future__ import annotations

import ast
import importlib
import pathlib
import pkgutil

import pytest

from agentclaw.community.plugin_api.base import Plugin


_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]              # .../src/backend
# B11: the contract suites split into the community and corp test trees.
_CONTRACTS_DIRS = (
    _BACKEND_ROOT / "tests" / "community" / "contracts",
    _BACKEND_ROOT / "tests" / "corp" / "contracts",
)


# Plugin module names with no conformance suite yet. Drained per group.
# - engine_ext_client: consumer-level contract test needs TeclawComposeProducer
#   (Task 11); the suite lands in Task 16, which removes this exemption.
EXEMPT_PROTOCOLS: frozenset[str] = frozenset({"engine_ext_client"})

# B11: protocols whose conformance suite is **corp-resident** (the meaningful impl is
# corp; community binds a noop). Their suite lives only in ``tests/corp/contracts``.
# When corp is absent (extracted community repo / dist-builder staged run) the corp
# suite isn't present, so these are treated as covered corp-side rather than flagged
# missing. In the monorepo (corp present) they resolve normally via ``_CONTRACTS_DIRS``.
_CORP_RESIDENT_PROTOCOLS: frozenset[str] = frozenset({"sandbox_runtime"})


def _discover_plugin_modules() -> set[str]:
    """Return the set of ``agentclaw.community.plugin_api`` submodule names that
    declare at least one Plugin Protocol.

    Mirrors the discovery in ``test_plugin_pairing`` so the two tests
    can't drift on what counts as a Plugin Protocol.
    """
    import agentclaw.community.plugin_api as pkg
    modules: set[str] = set()
    for mod_info in pkgutil.iter_modules(pkg.__path__):
        name = mod_info.name
        if name.startswith("_") or name in {"base", "impl_registry", "models"}:
            continue
        mod = importlib.import_module(f"agentclaw.community.plugin_api.{name}")
        for attr in dir(mod):
            obj = getattr(mod, attr)
            if not isinstance(obj, type) or obj is Plugin:
                continue
            if getattr(obj, "__module__", None) != mod.__name__:
                continue
            if Plugin in getattr(obj, "__mro__", ()):
                modules.add(name)
                break
    return modules


def _suite_has_test(path: pathlib.Path) -> bool:
    """True iff the suite file defines at least one ``test_*`` function."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, OSError):
        return False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name.startswith("test_"):
            return True
    return False


def test_every_plugin_protocol_has_a_contract_suite():
    failures: list[str] = []
    corp_contracts_present = (_BACKEND_ROOT / "tests" / "corp" / "contracts").is_dir()
    plugin_modules = _discover_plugin_modules()
    for name in sorted(plugin_modules):
        if name in EXEMPT_PROTOCOLS:
            continue
        # Corp-resident contract suites don't exist in a corp-absent tree; treat them
        # as covered corp-side there (they resolve normally in the monorepo).
        if name in _CORP_RESIDENT_PROTOCOLS and not corp_contracts_present:
            continue
        candidates = [d / f"test_{name}.py" for d in _CONTRACTS_DIRS]
        suite = next((c for c in candidates if c.is_file()), None)
        if suite is None:
            failures.append(
                f"{name}: missing conformance suite at "
                f"{candidates[0].relative_to(_BACKEND_ROOT)} (or the corp tree)"
            )
            continue
        if not _suite_has_test(suite):
            failures.append(
                f"{name}: suite {suite.relative_to(_BACKEND_ROOT)} has no test_* function"
            )
    if failures:
        pytest.fail("\n".join(failures))


def test_no_stale_exemptions():
    plugin_modules = _discover_plugin_modules()
    stale = sorted(EXEMPT_PROTOCOLS - plugin_modules)
    if stale:
        pytest.fail(
            "EXEMPT_PROTOCOLS contains entries that aren't real plugin modules — "
            "typo or removed Protocol:\n  " + "\n  ".join(stale)
        )
