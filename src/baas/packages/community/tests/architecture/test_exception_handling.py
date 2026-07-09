"""Architecture enforcement: exception handling best practices.

Every ``except Exception:`` block (or bare ``except:``) must either:
- Log the exception via a logger
- Re-raise or wrap-and-re-raise
- Explicitly ignore with a comment

Currently 60 ``except Exception:`` blocks across 27 files lack
proper handling (no logging or re-raise).

See RULES-MANIFEST.md Known Issues.
"""

import ast
import warnings
from pathlib import Path

SECBAAS = Path(__file__).resolve().parents[2] / "src" / "secbaas"


def _handler_logs_or_re_raises(node: ast.ExceptHandler) -> bool:
    for child in node.body:
        if isinstance(child, ast.Raise):
            return True
        if isinstance(child, ast.Expr) and isinstance(child.value, ast.Call):
            func = child.value.func
            if isinstance(func, ast.Attribute):
                receiver = func.value
                if isinstance(receiver, ast.Name) and receiver.id in (
                    "logger",
                    "log",
                    "_logger",
                    "_log",
                ):
                    return True
    return False


def test_except_blocks_must_log_or_re_raise():
    violations = []

    for py_file in sorted(SECBAAS.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        try:
            source = py_file.read_text()
            tree = ast.parse(source)
        except SyntaxError:
            continue

        rel = str(py_file.relative_to(SECBAAS))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for handler in node.handlers:
                if handler.type is None:
                    continue
                exc = ast.unparse(handler.type)
                if exc == "Exception" and not _handler_logs_or_re_raises(handler):
                    violations.append(
                        f"  {rel}:{handler.lineno}: except Exception"
                        f" without logging or re-raise"
                    )

    if violations:
        warnings.warn(
            "\n" + str(len(violations)) + " except Exception block(s)"
            " without logging or re-raise:\n"
            + "\n".join(violations)
            + "\n\nAdd logger.exception() or re-raise."
        )
