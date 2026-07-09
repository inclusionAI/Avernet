"""Architecture enforcement: no private modules with __main__ entry points.

Private modules (``_*.py``) should not contain ``if __name__ == "__main__":``
blocks.  Only ``main.py`` should be executable as a script.

Scripts that need a main entry point should be placed in ``scripts/``
or as console_scripts in ``pyproject.toml``.
"""

import ast
from pathlib import Path

SECBAAS = Path(__file__).resolve().parents[2] / "src" / "secbaas"

_ALLOWED_MAIN_FILES = {"main.py"}


def test_no_private_module_has_main():
    """Private modules must not have __main__ entry points."""
    violations: list[str] = []

    for py_file in sorted(SECBAAS.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        if py_file.name in _ALLOWED_MAIN_FILES:
            continue
        try:
            source = py_file.read_text()
        except OSError:
            continue

        if (
            "if __name__ == '__main__':" not in source
            and 'if __name__ == "__main__":' not in source
        ):
            continue

        rel = str(py_file.relative_to(SECBAAS))
        violations.append(f"  {rel}")

    if violations:
        raise AssertionError(
            f"\n{len(violations)} file(s) with __main__ entry point "
            f"(only main.py should be executable):\n"
            + "\n".join(violations)
            + "\n\nMove these entry points to scripts/ or pyproject.toml "
            "console_scripts."
        )
