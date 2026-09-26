"""Contract test: forbid run_in_threadpool over async functions.

run_in_threadpool dispatches a callable to a worker thread and awaits its
return value. If the callable is `async def`, the worker invokes it but
gets back an un-awaited coroutine — the function body never runs.

See docs/superpowers/plans/2026-05-18-fix-skills-api-broken-activate-deactivate.md
for the bugs this rule prevents.

Detection strategy:
1. Parse every .py file under src/backend/src/agentclaw/ with `ast`.
2. Find call sites of the form
   ``run_in_threadpool(<callable_expr>, ...)`` or
   ``await run_in_threadpool(<callable_expr>, ...)``.
3. Resolve <callable_expr> to a function/method name; check if that name
   matches any `async def` defined in the project.
4. If it does, fail.

False positives (sync function sharing a name with an async one) are
expected to be rare; suppress with the line comment
``# allow-run-in-threadpool`` if you've manually verified the call target
is the synchronous twin. Do NOT use ``# noqa`` — that's a generic lint
suppression that also disables this contract check unintentionally.

False negatives (lambda/partial wrappers) are acceptable.
"""
from __future__ import annotations

import ast
from pathlib import Path


BACKEND_SRC = Path(__file__).resolve().parents[3] / "src" / "agentclaw"


def _iter_py_files(root: Path):
    for p in root.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p


def _collect_async_names(tree: ast.AST) -> set[str]:
    """Return the set of `async def` names within `tree` (top-level + methods)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            names.add(node.name)
    return names


def _collect_async_names_across_project() -> set[str]:
    universe: set[str] = set()
    for p in _iter_py_files(BACKEND_SRC):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        universe |= _collect_async_names(tree)
    return universe


def _first_arg_name(call: ast.Call) -> str | None:
    """Return the simple name of the first positional arg, or None."""
    if not call.args:
        return None
    a = call.args[0]
    if isinstance(a, ast.Name):
        return a.id
    if isinstance(a, ast.Attribute):
        return a.attr  # e.g. `service.activate_skill` → "activate_skill"
    return None


def _check_file(p: Path, async_names: set[str]) -> list[str]:
    violations: list[str] = []
    try:
        source = p.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except SyntaxError:
        return violations

    lines = source.splitlines()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "run_in_threadpool":
            target = _first_arg_name(node)
        elif isinstance(func, ast.Attribute) and func.attr == "run_in_threadpool":
            target = _first_arg_name(node)
        else:
            continue

        if target is None:
            continue
        if target in async_names:
            # Suppression marker: this contract uses its own marker rather
            # than `# noqa`, so generic lint suppressions don't accidentally
            # disable the check. Adding `# allow-run-in-threadpool` should
            # only happen after you've manually verified that the call
            # target is a synchronous function sharing a name with an
            # unrelated async definition elsewhere in the project.
            line_text = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
            if "# allow-run-in-threadpool" in line_text:
                continue
            violations.append(
                f"{p}:{node.lineno}: run_in_threadpool({target}, ...) — "
                f"`{target}` is defined as async elsewhere in this project; "
                "directly `await` it instead."
            )
    return violations


def test_no_run_in_threadpool_on_async_functions():
    async_names = _collect_async_names_across_project()
    assert async_names, "expected to find some async def in project"

    all_violations: list[str] = []
    for p in _iter_py_files(BACKEND_SRC):
        all_violations.extend(_check_file(p, async_names))

    assert not all_violations, (
        "run_in_threadpool over async functions detected (silent no-op):\n"
        + "\n".join(all_violations)
    )
