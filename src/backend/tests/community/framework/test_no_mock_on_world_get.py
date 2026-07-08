"""Forbid mutating instances returned by ``world.get(...)`` with mocks.

The endpoint-test framework hands out a freshly-built injector per case
via ``world.get(T)``. Mutating an attribute on that instance — e.g.
``world.get(BotRepository).list_by_conditions = MagicMock(...)`` —
bypasses the entire point of seeding: the request no longer exercises
the real repo, so the test passes regardless of what the production
code does. The ``seed`` hook exists so authors can *write data through
the real components*, not to swap their methods out.

Detected patterns in ``tests/endpoints/`` (both forms of the hack):

1. **Direct** — ``world.get(T).method = <something>``
2. **Indirect** — ``repo = world.get(T); repo.method = <something>``

If a case genuinely needs a different implementation, bind it through
the injector (a testing module), don't reach in and overwrite methods
on a singleton.

Detection is AST-only.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]                # .../src/backend
# B11: endpoint tests split into the community and corp test trees; the
# no-mock-on-world.get rule is universal, so scan both.
_ENDPOINTS_ROOTS = (
    _BACKEND_ROOT / "tests" / "community" / "endpoints",
    _BACKEND_ROOT / "tests" / "corp" / "endpoints",
)


def _is_world_get_call(node: ast.AST) -> bool:
    """True iff ``node`` is ``world.get(...)``."""
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    return (
        isinstance(fn, ast.Attribute)
        and fn.attr == "get"
        and isinstance(fn.value, ast.Name)
        and fn.value.id == "world"
    )


def _collect_world_handle_names(func: ast.FunctionDef) -> set[str]:
    """Names locally assigned from ``world.get(...)`` inside ``func``."""
    names: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign):
            continue
        if not _is_world_get_call(node.value):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _scan_function(func: ast.FunctionDef) -> list[tuple[int, str]]:
    """Return (lineno, message) for each mutation hit inside ``func``."""
    hits: list[tuple[int, str]] = []
    handles = _collect_world_handle_names(func)
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Attribute):
                continue
            base = target.value
            # Indirect: <handle>.<attr> = ...
            if isinstance(base, ast.Name) and base.id in handles:
                hits.append((
                    target.lineno,
                    f"{base.id}.{target.attr} = ...  "
                    f"(mutates instance returned by world.get(...))",
                ))
                continue
            # Direct: world.get(T).<attr> = ...
            if _is_world_get_call(base):
                hits.append((
                    target.lineno,
                    f"world.get(...).{target.attr} = ...  "
                    f"(mutates instance returned by world.get(...))",
                ))
    return hits


def _scan_file(path: pathlib.Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError:
        return []
    rel = path.relative_to(_BACKEND_ROOT).as_posix()
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for lineno, msg in _scan_function(node):
                violations.append(f"{rel}:{lineno}  {msg}")
    return violations


@pytest.mark.unit
def test_no_mock_on_world_get_instance_in_endpoint_tests() -> None:
    """No endpoint test may overwrite attributes on a ``world.get(...)`` handle.

    Seed real data through the component (``repo.insert(...)``,
    ``svc.upsert_user(...)``); do not replace its methods with mocks.
    If a case truly needs a substitute implementation, register it via
    a testing DI module so the injector hands the real handler the
    substitute — keeping the framework's "no lies about coverage"
    invariant intact.
    """
    roots = [r for r in _ENDPOINTS_ROOTS if r.is_dir()]
    assert roots, "no endpoints/ test dir found under tests/community or tests/corp"
    violations: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("test_*.py")):
            violations.extend(_scan_file(path))
    assert not violations, (
        "Forbidden mock-on-DI-handle pattern in endpoint tests.\n"
        "The seed hook is for writing data through the real components, "
        "not for replacing their methods. Seed via repo.insert(...) / "
        "service.upsert(...) instead, or bind a substitute through the "
        "injector if you really need one.\n"
        "Violations:\n  " + "\n  ".join(violations)
    )
