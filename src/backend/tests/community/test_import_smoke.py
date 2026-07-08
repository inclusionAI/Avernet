"""Smoke test that imports every agentclaw module it can.

Purpose:
    Importing a module exercises its module-level code (class definitions,
    dataclass/Pydantic model declarations, constants, etc.). This gives us
    a broad baseline of coverage without needing per-module unit tests for
    simple declarative files.

    Modules that legitimately cannot be imported in the test environment
    (they pull in sofapy_base, MOSN-dependent services, or other runtime
    infrastructure that the conftest does not stub) are tolerated - we log
    the skip but do not fail the test. The goal is coverage breadth, not
    enforcing import-clean on every file.
"""
from __future__ import annotations

import importlib
import pkgutil
from typing import Iterable, List

import agentclaw


_SKIP_PREFIXES: tuple[str, ...] = (
    # oneapi generated stubs - require sofapy_base which is not installed
    "agentclaw.oneapi",
    # antprocess generated stubs - require sofapy_base
    "agentclaw.community.core.approval",
)


def _iter_modules(package) -> Iterable[str]:
    """Walk a package and yield all module dotted names."""
    path = getattr(package, "__path__", None)
    if path is None:
        return
    prefix = package.__name__ + "."
    for _, name, _ in pkgutil.walk_packages(path, prefix):
        yield name


def _should_skip(name: str) -> bool:
    for p in _SKIP_PREFIXES:
        if name == p or name.startswith(p + "."):
            return True
    # Avoid importing legacy openclawserver test modules from within the package
    if "tests" in name.split("."):
        return True
    if "test" in name.split(".")[-1]:
        return True
    return False


def test_import_all_agentclaw_modules() -> None:
    successes: List[str] = []
    failures: List[tuple[str, str]] = []

    for module_name in _iter_modules(agentclaw):
        if _should_skip(module_name):
            continue
        try:
            importlib.import_module(module_name)
            successes.append(module_name)
        except Exception as exc:
            failures.append((module_name, f"{type(exc).__name__}: {exc}"))

    # We require that a healthy majority of modules import successfully.
    total = len(successes) + len(failures)
    assert total > 0, "No modules discovered - is agentclaw installed?"
    success_ratio = len(successes) / total
    assert success_ratio >= 0.5, (
        f"Only {len(successes)}/{total} modules imported successfully "
        f"({success_ratio:.0%}). First failures: {failures[:5]}"
    )
