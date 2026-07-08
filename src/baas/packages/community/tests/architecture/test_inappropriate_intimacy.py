"""Architecture enforcement: detect inappropriate intimacy (tight coupling).

Classes that have 15+ total foreign method calls across 5+ unique foreign
objects indicate excessive coupling — the class knows too much about too
many other objects.  This is a code smell known as "inappropriate intimacy"
from Martin Fowler's Refactoring catalog.

Detection rule:
  - Walk every ``ast.ClassDef`` body in ``src/secbaas/``
  - Identify foreign method calls: ``obj.method()`` where *obj* is NOT
    ``self``, ``cls``, ``super()``, or a builtin type
  - Group calls by receiver object name
  - Flag classes with ≥15 total foreign calls AND ≥5 distinct receiver names
"""

import ast
import warnings
from collections import defaultdict
from pathlib import Path

SECBAAS = Path(__file__).resolve().parents[2] / "src" / "secbaas"

# ── Receivers that are NOT foreign ──────────────────────────────────────
# Calls on these objects are normal self-use or language-level patterns.
_SELF_RECEIVERS = frozenset({"self", "cls"})

# Builtin callables whose methods are always "internal" — e.g. dict.get(),
# list.append(), str.format().  Any call on a Name whose id is a builtin
# type is excluded because it's not coupling to another application class.
_BUILTIN_NAMES = frozenset(
    {
        "dict",
        "list",
        "set",
        "tuple",
        "str",
        "int",
        "float",
        "bool",
        "bytes",
        "bytearray",
        "frozenset",
        "range",
        "slice",
        "type",
        "object",
        "super",
        "enumerate",
        "zip",
        "map",
        "filter",
        "reversed",
        "sorted",
        "all",
        "any",
        "iter",
        "next",
        "len",
        "abs",
        "min",
        "max",
        "sum",
        "pow",
        "round",
        "hash",
        "id",
        "isinstance",
        "issubclass",
        "hasattr",
        "getattr",
        "setattr",
        "delattr",
        "repr",
        "str",
        "format",
        "print",
        "input",
        "open",
        "__import__",
        # Common third-party / stdlib modules — calls like os.path.join()
        # are not coupling within the application.
        "os",
        "sys",
        "re",
        "json",
        "time",
        "datetime",
        "logging",
        "uuid",
        "copy",
        "collections",
        "itertools",
        "functools",
        "typing",
        "dataclasses",
        "enum",
        "abc",
        "asyncio",
        "pathlib",
        "io",
        "math",
        "random",
        "subprocess",
        "threading",
        "warnings",
        "traceback",
        "inspect",
        "textwrap",
        "shutil",
    }
)

# ―――――――――――――――――――――――――――――――――――――――――――――――――――――――――――――
# Detection thresholds
# ―――――――――――――――――――――――――――――――――――――――――――――――――――――――――――――――
_MIN_FOREIGN_CALLS = 15
_MIN_DISTINCT_RECEIVERS = 5


def _is_super_call(node: ast.Call) -> bool:
    """True if the call is ``super().method()``."""
    return (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Call)
        and isinstance(node.func.value.func, ast.Name)
        and node.func.value.func.id == "super"
    )


def _receiver_name(node: ast.Call) -> str | None:
    """Extract the receiver object name from an ``obj.method()`` call.

    Returns:
        - The ``id`` of a plain ``Name`` receiver, e.g. ``"db"`` for ``db.query()``
        - ``"self"`` for ``self.foo()``
        - ``"super"`` for ``super().foo()``
        - ``None`` for free function calls like ``bar()`` or dotted chains
          like ``a.b().c()`` (too complex to resolve statically)
    """
    if not isinstance(node.func, ast.Attribute):
        return None  # simple function call: foo()

    if isinstance(node.func.value, ast.Name):
        return node.func.value.id  # obj.method()

    if _is_super_call(node):
        return "super"

    # Chains like a.b.c() — skipped (ambiguous receiver)
    return None


def _analyse_class_body(class_node: ast.ClassDef, file_source: str) -> dict[str, int]:
    """Count foreign calls in a class body, keyed by receiver name.

    Returns ``{receiver_name: call_count}`` for each unique non-self,
    non-builtin receiver.
    """
    receiver_counts: dict[str, int] = defaultdict(int)

    for node in ast.walk(class_node):
        if not isinstance(node, ast.Call):
            continue

        recv = _receiver_name(node)
        if recv is None:
            continue
        if recv in _SELF_RECEIVERS:
            continue
        if recv in _BUILTIN_NAMES:
            continue

        receiver_counts[recv] += 1

    return dict(receiver_counts)


def _scan_intimacy() -> list[tuple[str, str, int, int, dict[str, int]]]:
    """Scan all Python files under SECBAAS for inappropriate intimacy.

    Returns a list of ``(file_path, class_name, total_calls,
    distinct_receivers, receiver_counts)`` tuples for flagged classes.
    """
    flagged: list[tuple[str, str, int, int, dict[str, int]]] = []

    for py_file in sorted(SECBAAS.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue

        try:
            source = py_file.read_text()
            tree = ast.parse(source)
        except SyntaxError:
            continue

        rel_path = str(py_file.relative_to(SECBAAS))

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue

            receiver_counts = _analyse_class_body(node, source)
            total = sum(receiver_counts.values())
            distinct = len(receiver_counts)

            if total >= _MIN_FOREIGN_CALLS and distinct >= _MIN_DISTINCT_RECEIVERS:
                flagged.append((rel_path, node.name, total, distinct, receiver_counts))

    return flagged


# ═══════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_no_excessive_tight_coupling():
    """Warn when a class has ≥15 foreign calls across ≥5 distinct receivers.

    Foreign calls are ``obj.method()`` patterns where *obj* is not
    ``self``, ``cls``, ``super()``, or a builtin / stdlib identifier.
    A high foreign-call count spread across many distinct objects suggests
    the class is too intimate with too many collaborators.
    """
    flagged = _scan_intimacy()

    if not flagged:
        return

    lines: list[str] = []
    for rel_path, class_name, total, distinct, recv_counts in flagged:
        top_receivers = sorted(recv_counts.items(), key=lambda x: x[1], reverse=True)
        receiver_summary = ", ".join(f"{name}×{cnt}" for name, cnt in top_receivers[:8])
        if len(top_receivers) > 8:
            receiver_summary += f" … +{len(top_receivers) - 8} more"
        lines.append(
            f"  {rel_path}::{class_name}  "
            f"({total} foreign calls / {distinct} receivers → {receiver_summary})"
        )

    warnings.warn(
        f"\n{len(flagged)} class(es) exhibit inappropriate intimacy "
        f"(≥{_MIN_FOREIGN_CALLS} foreign calls, "
        f"≥{_MIN_DISTINCT_RECEIVERS} distinct receivers):\n"
        + "\n".join(lines)
        + "\n\nConsider extracting collaborator interactions into "
        "dedicated service/facade classes or using dependency injection "
        "to reduce direct coupling."
    )
