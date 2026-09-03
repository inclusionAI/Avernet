#!/usr/bin/env python3
"""Protect selected Task module class structure during pre-push."""

from __future__ import annotations

import argparse
import ast
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, NamedTuple, Sequence


OWNER_EMAIL = "regrecall@gmail.com"
DEFAULT_MANIFEST = "scripts/ci/task_design_guard.json"
BACKEND_SOURCE_ROOT = Path("src/backend/src")


class GuardFailure(RuntimeError):
    """The guard could not establish a reliable comparison."""


class ProtectedClass(NamedTuple):
    package: str
    qualified_name: str
    module: str
    name: str
    methods: tuple[str, ...]
    source_path: str


class Violation(NamedTuple):
    rule: str
    symbol: str
    detail: str


class Comparison(NamedTuple):
    violations: tuple[Violation, ...]
    warnings: tuple[str, ...]


def _clean_git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_PREFIX", "GIT_INDEX_FILE"):
        environment.pop(name, None)
    return environment


def _git(
    repository: Path, arguments: Sequence[str], *, required: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        env=_clean_git_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if required and result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
        raise GuardFailure(f"git {' '.join(arguments)} failed: {message}")
    return result


def find_repository_root(start: Path) -> Path:
    result = _git(start, ["rev-parse", "--show-toplevel"])
    return Path(result.stdout.strip()).resolve()


def configured_email(repository: Path) -> str | None:
    result = _git(repository, ["config", "--get", "user.email"], required=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _read_blob(repository: Path, revision: str, path: str) -> str | None:
    result = _git(repository, ["show", f"{revision}:{path}"], required=False)
    if result.returncode == 0:
        return result.stdout
    missing_markers = (
        "does not exist in",
        "exists on disk, but not in",
        "path '",
    )
    if any(marker in result.stderr for marker in missing_markers):
        return None
    message = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
    raise GuardFailure(f"could not read {path} at {revision}: {message}")


def _expect_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise GuardFailure(f"manifest field {field} must be a non-empty string")
    return value


def _expect_list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise GuardFailure(f"manifest field {field} must be a list")
    return value


def load_manifest(text: str) -> tuple[ProtectedClass, ...]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise GuardFailure(f"manifest is not valid JSON: {error}") from error
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise GuardFailure("manifest version must be 1")

    protected: list[ProtectedClass] = []
    seen_classes: set[str] = set()
    for package_index, package_entry in enumerate(
        _expect_list(payload.get("packages"), "packages")
    ):
        if not isinstance(package_entry, dict):
            raise GuardFailure(f"packages[{package_index}] must be an object")
        package = _expect_string(
            package_entry.get("package"), f"packages[{package_index}].package"
        )
        classes = _expect_list(
            package_entry.get("classes"), f"packages[{package_index}].classes"
        )
        for class_index, class_entry in enumerate(classes):
            prefix = f"packages[{package_index}].classes[{class_index}]"
            if not isinstance(class_entry, dict):
                raise GuardFailure(f"{prefix} must be an object")
            qualified_name = _expect_string(class_entry.get("class"), f"{prefix}.class")
            if qualified_name in seen_classes:
                raise GuardFailure(f"duplicate protected class: {qualified_name}")
            module, separator, class_name = qualified_name.rpartition(".")
            if not separator or not module.startswith(package):
                raise GuardFailure(
                    f"protected class {qualified_name} is outside package {package}"
                )
            raw_methods = _expect_list(class_entry.get("methods"), f"{prefix}.methods")
            methods = tuple(
                _expect_string(method, f"{prefix}.methods[{index}]")
                for index, method in enumerate(raw_methods)
            )
            if len(set(methods)) != len(methods):
                raise GuardFailure(f"duplicate protected method in {qualified_name}")
            source_path = str(
                (BACKEND_SOURCE_ROOT / Path(*module.split("."))).with_suffix(".py")
            )
            protected.append(
                ProtectedClass(
                    package=package,
                    qualified_name=qualified_name,
                    module=module,
                    name=class_name,
                    methods=methods,
                    source_path=source_path,
                )
            )
            seen_classes.add(qualified_name)
    if not protected:
        raise GuardFailure("manifest must protect at least one class")
    return tuple(protected)


def _parse(source: str, label: str) -> ast.Module:
    try:
        return ast.parse(source, filename=label)
    except SyntaxError as error:
        raise GuardFailure(f"could not parse {label}: {error}") from error


def _find_class(tree: ast.Module, name: str) -> ast.ClassDef | None:
    return next(
        (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name),
        None,
    )


def _methods(node: ast.ClassDef) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        child
        for child in node.body
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _find_method(
    node: ast.ClassDef, name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    return next((method for method in _methods(node) if method.name == name), None)


def _dump(node: ast.AST | None) -> str | None:
    return None if node is None else ast.dump(node, include_attributes=False)


def _dump_many(nodes: Sequence[ast.AST]) -> tuple[str, ...]:
    return tuple(ast.dump(node, include_attributes=False) for node in nodes)


def _class_bases(node: ast.ClassDef) -> tuple[tuple[str, ...], tuple[str, ...]]:
    bases = _dump_many(node.bases) + _method_type_parameters(node)
    keywords = tuple(
        f"{keyword.arg}={ast.dump(keyword.value, include_attributes=False)}"
        for keyword in node.keywords
    )
    return bases, keywords


def _class_fields(node: ast.ClassDef) -> tuple[str, ...]:
    fields: list[str] = []
    for child in node.body:
        if isinstance(child, ast.Assign):
            targets = ",".join(
                ast.dump(target, include_attributes=False) for target in child.targets
            )
            value = ast.dump(child.value, include_attributes=False)
            fields.append(f"assign:{targets}={value}:type_comment={child.type_comment!r}")
        elif isinstance(child, ast.AnnAssign):
            target = ast.dump(child.target, include_attributes=False)
            annotation = ast.dump(child.annotation, include_attributes=False)
            value = _dump(child.value)
            fields.append(
                f"annotated:{target}:{annotation}={value}:simple={child.simple}"
            )
    return tuple(sorted(fields))


def _method_kind(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    return "async" if isinstance(node, ast.AsyncFunctionDef) else "sync"


def _method_type_parameters(node: ast.AST) -> tuple[str, ...]:
    return _dump_many(getattr(node, "type_params", ()))


def _short(value: object, limit: int = 360) -> str:
    rendered = repr(value)
    return rendered if len(rendered) <= limit else rendered[: limit - 3] + "..."


def compare_class_sources(
    base_source: str,
    head_source: str,
    protected: ProtectedClass,
) -> Comparison:
    base_tree = _parse(base_source, f"base:{protected.source_path}")
    head_tree = _parse(head_source, f"head:{protected.source_path}")
    base_class = _find_class(base_tree, protected.name)
    head_class = _find_class(head_tree, protected.name)

    if base_class is None:
        return Comparison(
            violations=(),
            warnings=(
                f"protected class {protected.qualified_name} is absent from the base revision; guard skipped",
            ),
        )
    if head_class is None:
        return Comparison(
            violations=(
                Violation(
                    "TRG001",
                    protected.qualified_name,
                    "protected class was removed or renamed",
                ),
            ),
            warnings=(),
        )

    violations: list[Violation] = []
    warnings: list[str] = []
    base_bases = _class_bases(base_class)
    head_bases = _class_bases(head_class)
    if base_bases != head_bases:
        violations.append(
            Violation(
                "TRG002",
                protected.qualified_name,
                f"base classes changed: {_short(base_bases)} -> {_short(head_bases)}",
            )
        )

    base_decorators = _dump_many(base_class.decorator_list)
    head_decorators = _dump_many(head_class.decorator_list)
    if base_decorators != head_decorators:
        violations.append(
            Violation(
                "TRG003",
                protected.qualified_name,
                "class decorators changed: "
                f"{_short(base_decorators)} -> {_short(head_decorators)}",
            )
        )

    base_fields = _class_fields(base_class)
    head_fields = _class_fields(head_class)
    if base_fields != head_fields:
        violations.append(
            Violation(
                "TRG004",
                protected.qualified_name,
                f"class fields changed: {_short(base_fields)} -> {_short(head_fields)}",
            )
        )

    base_method_counts = Counter(method.name for method in _methods(base_class))
    head_method_counts = Counter(method.name for method in _methods(head_class))
    for method_name in sorted(head_method_counts):
        added = head_method_counts[method_name] - base_method_counts[method_name]
        if added > 0:
            violations.append(
                Violation(
                    "TRG005",
                    f"{protected.qualified_name}.{method_name}",
                    "method was added to the protected class",
                )
            )

    for method_name in protected.methods:
        symbol = f"{protected.qualified_name}.{method_name}"
        base_method = _find_method(base_class, method_name)
        head_method = _find_method(head_class, method_name)
        if base_method is None:
            warnings.append(
                f"protected method {symbol} is absent from the base revision; method guard skipped"
            )
            continue
        if head_method is None:
            violations.append(
                Violation(
                    "TRG101", symbol, "protected method was removed or renamed"
                )
            )
            continue

        base_kind = _method_kind(base_method)
        head_kind = _method_kind(head_method)
        if base_kind != head_kind:
            violations.append(
                Violation(
                    "TRG102",
                    symbol,
                    f"method kind changed: {base_kind} -> {head_kind}",
                )
            )

        base_method_decorators = _dump_many(base_method.decorator_list)
        head_method_decorators = _dump_many(head_method.decorator_list)
        if base_method_decorators != head_method_decorators:
            violations.append(
                Violation(
                    "TRG103",
                    symbol,
                    "method decorators changed: "
                    f"{_short(base_method_decorators)} -> {_short(head_method_decorators)}",
                )
            )

        base_arguments = (_dump(base_method.args), base_method.type_comment)
        head_arguments = (_dump(head_method.args), head_method.type_comment)
        base_type_parameters = _method_type_parameters(base_method)
        head_type_parameters = _method_type_parameters(head_method)
        if (base_arguments, base_type_parameters) != (
            head_arguments,
            head_type_parameters,
        ):
            violations.append(
                Violation(
                    "TRG104",
                    symbol,
                    "parameter structure changed: "
                    f"{_short((base_arguments, base_type_parameters))} -> "
                    f"{_short((head_arguments, head_type_parameters))}",
                )
            )

        base_return = _dump(base_method.returns)
        head_return = _dump(head_method.returns)
        if base_return != head_return:
            violations.append(
                Violation(
                    "TRG105",
                    symbol,
                    f"return annotation changed: {_short(base_return)} -> {_short(head_return)}",
                )
            )

    return Comparison(tuple(violations), tuple(warnings))


def changed_files(repository: Path, base: str, head: str) -> set[str]:
    result = _git(repository, ["diff", "--name-only", base, head, "--"])
    return {line for line in result.stdout.splitlines() if line}


def evaluate(
    repository: Path,
    base: str,
    head: str,
    manifest_path: str = DEFAULT_MANIFEST,
) -> tuple[Comparison, bool]:
    manifest_text = _read_blob(repository, head, manifest_path)
    if manifest_text is None:
        raise GuardFailure(f"manifest {manifest_path} is absent from {head}")
    protected_classes = load_manifest(manifest_text)
    changed = changed_files(repository, base, head)
    relevant_paths = {manifest_path, *(item.source_path for item in protected_classes)}
    if changed.isdisjoint(relevant_paths):
        return Comparison((), ()), False

    violations: list[Violation] = []
    warnings: list[str] = []
    for protected in protected_classes:
        if protected.source_path not in changed and manifest_path not in changed:
            continue
        base_source = _read_blob(repository, base, protected.source_path)
        if base_source is None:
            warnings.append(
                f"protected source {protected.source_path} is absent from the base revision; guard skipped"
            )
            continue
        head_source = _read_blob(repository, head, protected.source_path)
        if head_source is None:
            violations.append(
                Violation(
                    "TRG001",
                    protected.qualified_name,
                    f"protected source {protected.source_path} was removed",
                )
            )
            continue
        result = compare_class_sources(base_source, head_source, protected)
        violations.extend(result.violations)
        warnings.extend(result.warnings)
    return Comparison(tuple(violations), tuple(warnings)), True


def _print_violations(violations: Sequence[Violation]) -> None:
    print("TaskRunner design guard failed", file=sys.stderr)
    for violation in violations:
        print(
            f"{violation.rule} {violation.symbol}\n  {violation.detail}",
            file=sys.stderr,
        )
    print(
        "TaskRunner is a protected design surface; revert the structural change before pushing.",
        file=sys.stderr,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    arguments = parser.parse_args(argv)

    try:
        repository = find_repository_root(Path.cwd())
        if configured_email(repository) == OWNER_EMAIL:
            return 0
        comparison, relevant = evaluate(
            repository,
            arguments.base,
            arguments.head,
            arguments.manifest,
        )
        for warning in comparison.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        if comparison.violations:
            _print_violations(comparison.violations)
            return 1
        if relevant:
            print("TaskRunner design guard passed: protected structure is unchanged")
        return 0
    except Exception as error:  # Fail open by explicit Phase 1 policy.
        print(f"warning: TaskRunner design guard skipped: {error}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
