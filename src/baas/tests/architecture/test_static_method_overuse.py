"""Architecture enforcement: classes with excessive @staticmethod decorators.

A class with more than a handful of ``@staticmethod`` decorators is
often being used as a namespace rather than a proper class.  This is
a code smell that suggests:

- The class's methods don't share any instance state
- The class exists only as an organisational bucket
- The class has no behavioural contract enforced through ``self``

This test scans every ``.py`` file under ``secbaas/`` and emits a
``warnings.warn()`` (never FAILs) for any class declaring more than
3 static methods.
"""

import ast
import warnings
from pathlib import Path

SECBAAS = Path(__file__).resolve().parents[2] / "src" / "secbaas" / "community"

# Number of @staticmethod decorators beyond which a warning is issued
_STATIC_THRESHOLD = 3


def test_no_excessive_static_methods():
    f"""Classes should not be used as mere namespaces.

    Warns when a single class declares more than {_STATIC_THRESHOLD}
    ``@staticmethod`` methods — a signal the class lacks behavioural
    cohesion and may be a candidate for a module with plain functions.

    Emits ``warnings.warn()`` only — never FAILs.
    """
    violations: list[tuple[str, str, int]] = []

    for py_file in sorted(SECBAAS.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue

            static_count = 0
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for decorator in item.decorator_list:
                        if (
                            isinstance(decorator, ast.Name)
                            and decorator.id == "staticmethod"
                        ):
                            static_count += 1
                            break  # count each method once

            if static_count > _STATIC_THRESHOLD:
                rel = py_file.relative_to(SECBAAS).as_posix()
                violations.append((rel, node.name, static_count))

    if not violations:
        return

    violations.sort(key=lambda x: (-x[2], x[0], x[1]))
    lines = [
        f"  {rel} :: {class_name} — {count} static methods"
        for rel, class_name, count in violations
    ]

    warnings.warn(
        f"\n{len(violations)} class(es) with more than {_STATIC_THRESHOLD} "
        f"@staticmethod methods (namespace code smell — consider plain "
        f"functions in a module):\n" + "\n".join(lines)
    )
