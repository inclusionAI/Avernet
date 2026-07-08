"""Architecture enforcement: detect feature envy via excessive foreign method calls.

``Feature envy`` occurs when a class calls an excessive number of methods on
the same foreign object, indicating that the behaviour likely belongs on that
object rather than in the current class.

This test scans all ``.py`` files under ``src/secbaas/`` and, for each class,
counts how many attribute-access method calls (``obj.method()``) target each
unique receiver name.  If any single receiver receives 5+ method calls from
the same class and the receiver is not ``self`` or ``cls``, a warning is
emitted.

Emitted via ``warnings.warn()`` — never FAILs.
"""

import ast
import warnings
from collections import defaultdict
from pathlib import Path

SECBAAS = Path(__file__).resolve().parents[2] / "src" / "secbaas"

# ── Threshold ────────────────────────────────────────────────────────────────
_MIN_FOREIGN_CALLS = 5  # calls to the same foreign receiver → feature envy

# ── Dunder / special names that are never "foreign" ──────────────────────────
_DUNDER_METHODS = frozenset(
    name for name in dir(object) if name.startswith("__") and name.endswith("__")
)
# Also exclude common special methods not on plain object
_DUNDER_METHODS |= {
    "__post_init__",
    "__post__",
    "__init_subclass__",
    "__class_getitem__",
    "__aenter__",
    "__aexit__",
    "__await__",
    "__aiter__",
    "__anext__",
}


def test_no_excessive_foreign_method_calls():
    """Warn when any class calls 5+ methods on the same foreign object.

    A *foreign object* is any receiver that is not ``self`` or ``cls``.
    Method calls are detected via the AST pattern::

        ast.Call(
            func=ast.Attribute(
                value=ast.Name(id=receiver_name),
                attr=method_name,
            ),
            ...
        )

    Only calls on ``ast.Name`` receivers are counted — attribute chains
    (e.g. ``self.obj.method()``) are intentionally ignored because those
    represent access through an instance attribute rather than a directly
    visible local / parameter reference.

    Test files (anything under ``tests/``) and ``__pycache__`` are skipped.
    """
    envy_violations: list[str] = []

    for py_file in sorted(SECBAAS.rglob("*.py")):
        # ── Skip cache directories ──────────────────────────────────────
        if "__pycache__" in str(py_file):
            continue

        # ── Determine relative path and skip test files ─────────────────
        try:
            rel_path = py_file.relative_to(SECBAAS)
        except ValueError:
            continue  # file not under SECBAAS (shouldn't happen)

        rel_str = str(rel_path)
        if rel_str.startswith("tests" + "/") or rel_str == "tests":
            continue

        # ── Parse the file ──────────────────────────────────────────────
        try:
            source = py_file.read_text()
            tree = ast.parse(source)
        except SyntaxError:
            continue

        # ── Walk top-level and nested class bodies ──────────────────────
        for class_node in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            receiver_counts: dict[str, int] = defaultdict(int)

            for node in ast.walk(class_node):
                # We only care about method calls *directly inside* this
                # class definition — nested classes will be visited by
                # their own iteration above.
                if not isinstance(node, ast.Call):
                    continue

                # Pattern: obj.method()
                if not isinstance(node.func, ast.Attribute):
                    continue
                if not isinstance(node.func.value, ast.Name):
                    continue

                receiver = node.func.value.id
                method = node.func.attr

                # Skip self / cls calls
                if receiver in ("self", "cls"):
                    continue

                # Skip dunder method calls (not feature envy)
                if method in _DUNDER_METHODS:
                    continue
                if method.startswith("__") and method.endswith("__"):
                    continue

                receiver_counts[receiver] += 1

            # ── Report receivers that exceed the threshold ──────────────
            for receiver, count in sorted(receiver_counts.items()):
                if count >= _MIN_FOREIGN_CALLS:
                    envy_violations.append(
                        f"  {rel_str}::{class_node.name} -> {receiver} ({count} calls)"
                    )

    if envy_violations:
        warnings.warn(
            "\nFeature envy detected — classes calling 5+ methods on "
            "the same foreign object:\n"
            + "\n".join(sorted(envy_violations))
            + "\n"
            + "\nThese classes may benefit from moving the functionality "
            "onto the object they depend on, or introducing a service / "
            "mediator abstraction."
        )
