from __future__ import annotations

import ast
from pathlib import Path

import pytest

_THIS_FILE = Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]
_SOURCE_ROOT = _BACKEND_ROOT / "src" / "agentclaw" / "community"
_PROFILE_PATH = "di/profile.py"
_ENV_UTILS_PATH = "utils/env_utils.py"


def _is_singlebox_literal(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.casefold() == "singlebox"
    )


def _enclosing_assignment(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> ast.Assign | None:
    parent = parents.get(node)
    while parent is not None:
        if isinstance(parent, ast.Assign):
            return parent
        parent = parents.get(parent)
    return None


def _is_single_target_assignment(node: ast.Assign, name: str) -> bool:
    return (
        len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == name
    )


def _is_exact_frozenset(value: ast.AST, expected: set[str]) -> bool:
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "frozenset"
        and len(value.args) == 1
        and not value.keywords
        and isinstance(value.args[0], ast.Set)
        and {
            item.value
            for item in value.args[0].elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        }
        == expected
        and len(value.args[0].elts) == len(expected)
    )


def _is_canonical_literal(
    node: ast.AST,
    *,
    rel: str,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    if not isinstance(node, ast.Constant) or node.value != "singlebox":
        return False
    assignment = _enclosing_assignment(node, parents)
    if assignment is None:
        return False
    assignment_parent = parents.get(assignment)
    if (
        rel == _PROFILE_PATH
        and isinstance(assignment_parent, ast.Module)
        and _is_single_target_assignment(assignment, "_RETIRED_SERVER_ENV_VALUES")
        and _is_exact_frozenset(assignment.value, {"singlebox"})
    ):
        return True
    if (
        rel == _ENV_UTILS_PATH
        and isinstance(assignment_parent, ast.Module)
        and _is_single_target_assignment(assignment, "_LOCAL_DEPLOY_PROFILES")
        and _is_exact_frozenset(
            assignment.value, {"test", "singlebox", "corp_test"}
        )
    ):
        return True
    return (
        rel == _PROFILE_PATH
        and _is_single_target_assignment(assignment, "SINGLEBOX")
        and isinstance(assignment_parent, ast.ClassDef)
        and assignment_parent.name == "DeployProfile"
        and isinstance(parents.get(assignment_parent), ast.Module)
        and isinstance(assignment.value, ast.Constant)
        and assignment.value.value == "singlebox"
    )


def _call_name(node: ast.Call) -> str | None:
    return getattr(node.func, "id", None) or getattr(node.func, "attr", None)


def _violations(path: Path, *, source_root: Path = _SOURCE_ROOT) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    rel = path.relative_to(source_root).as_posix()
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    failures: list[str] = []

    for node in ast.walk(tree):
        if _is_singlebox_literal(node) and not _is_canonical_literal(
            node, rel=rel, parents=parents
        ):
            failures.append(
                f"{rel}:{node.lineno} singlebox literal outside canonical profile definitions"
            )
        elif isinstance(node, ast.Call) and _call_name(node) == "is_singlebox":
            failures.append(f"{rel}:{node.lineno} is_singlebox call")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "is_singlebox":
            failures.append(f"{rel}:{node.lineno} is_singlebox definition")
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "is_singlebox":
                    failures.append(f"{rel}:{node.lineno} is_singlebox import")

    return sorted(
        failures,
        key=lambda failure: int(failure.split(":", 2)[1].split(" ", 1)[0]),
    )


def test_singlebox_never_reenters_the_env_axis():
    failures: list[str] = []
    for path in _SOURCE_ROOT.rglob("*.py"):
        failures.extend(_violations(path))

    if failures:
        pytest.fail(
            "singlebox is a DeployProfile, not a runtime/data Env:\n  "
            + "\n  ".join(failures)
        )


def test_profile_contains_only_canonical_singlebox_literals():
    assert not _violations(_SOURCE_ROOT / "di" / "profile.py")
