"""Architecture enforcement: detect shotgun surgery patterns.

``dup(licate string constants / enum values defined in 3+ files`` suggest
a shared abstraction is missing.  Common status values like ``"PENDING"``,
``"FAILED"``, ``"ACTIVE"`` are currently duplicated across 5-8 separate
enum classes, making it impossible to change a status without touching
many files.

This test emits ``warnings.warn()`` only (never FAILs).
"""

import ast
import re
import warnings
from collections import Counter
from pathlib import Path

SECBAAS = Path(__file__).resolve().parents[2] / "src" / "secbaas" / "community"

# Minimum duplication threshold
_MIN_DUPLICATE_FILES = 3


def test_no_duplicated_simple_status_values():
    """Detect identical simple string constants in 3+ files.

    Flags status-like values (e.g. "PENDING", "FAILED", "ACTIVE")
    that are defined as top-level constants in multiple modules
    where they should be consolidated into a shared enum.

    Emits warnings only — never FAILs.
    """
    value_to_files: dict[str, set[str]] = {}

    # Pattern for status-like values: all-caps or capitalised identifiers
    status_pattern = re.compile(r"^[A-Z][A-Z_]*$")

    for py_file in sorted(SECBAAS.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        rel = str(py_file.relative_to(SECBAAS))

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and status_pattern.match(target.id):
                    if isinstance(node.value, ast.Constant) and isinstance(
                        node.value.value, str
                    ):
                        val = node.value.value
                        if status_pattern.match(val):
                            value_to_files.setdefault(val, set()).add(rel)

    # Filter to only values duplicated across 3+ files
    widespread = {
        val: files
        for val, files in value_to_files.items()
        if len(files) >= _MIN_DUPLICATE_FILES
    }

    if widespread:
        lines = []
        for val, files in sorted(widespread.items()):
            lines.append(f"  '{val}' -> {len(files)} files: {', '.join(sorted(files))}")
        warnings.warn(
            f"\n{len(widespread)} string value(s) duplicated across "
            f"{_MIN_DUPLICATE_FILES}+ files — consider a shared enum:\n"
            + "\n".join(lines)
        )


def test_no_duplicated_enum_class_values():
    """Detect identical enum member names across 3+ enum classes.

    Multiple enum classes defining the same member names (e.g. every
    status enum having ``FAILED``, ``PENDING``, ``SUCCESS``) strongly
    suggests a shared ``StatusType`` should exist.

    Emits warnings only — never FAILs.
    """
    enum_files: dict[str, set[str]] = {}

    for py_file in sorted(SECBAAS.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        rel = str(py_file.relative_to(SECBAAS))

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue

            # Detect enum-style classes: inherits from str, Enum, or has
            # all-caps assignments
            is_enum = any(
                isinstance(base, ast.Name) and base.id in {"str", "Enum", "IntEnum"}
                for base in node.bases
            )
            if not is_enum:
                continue

            members = set()
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.Assign):
                    for target in child.targets:
                        if isinstance(target, ast.Name) and target.id.isupper():
                            members.add(target.id)

            if members:
                for member in members:
                    enum_files.setdefault(member, set()).add(f"{rel}::{node.name}")

    widespread = {
        member: files
        for member, files in enum_files.items()
        if len(files) >= _MIN_DUPLICATE_FILES
    }

    if widespread:
        lines = []
        for member, files in sorted(widespread.items()):
            lines.append(
                f"  '{member}' -> {len(files)} classes: " + ", ".join(sorted(files))
            )
        warnings.warn(
            f"\n{len(widespread)} enum member(s) defined in "
            f"{_MIN_DUPLICATE_FILES}+ enum classes — consider a shared enum:\n"
            + "\n".join(lines)
        )
