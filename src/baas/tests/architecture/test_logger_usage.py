"""Architecture enforcement: logging must go through secbaas.logger.

All application code SHOULD use ``community.logger.get_logger()`` for
logging, not ``logging.getLogger()`` directly.  The standard library
``logging.getLogger`` is reserved for:

- ``spi/logger/_protocols.py`` — the LoggerPlugin Protocol definition
- ``plugins/logger/*`` — the logging plugin implementations
- ``infra/`` — legacy code not yet migrated to the plugin system

Using ``community.logger.get_logger()`` ensures:
  - Runtime-swappable logger backends via the plugin system
  - Consistent log formatting and routing across the application
  - Testability — log output can be captured via plugin injection
"""

import ast
import warnings
from pathlib import Path

SECBAAS = Path(__file__).resolve().parents[2] / "src" / "secbaas" / "community"


def test_no_direct_logging_getlogger_outside_legacy_layers():
    """``logging.getLogger()`` must not be used outside the logger SPI,
    logger plugin implementations, and infra/ legacy code.

    Using ``community.logger.get_logger()`` keeps logging swappable.
    """
    allowed_prefixes = {
        "spi/logger",
        "plugins/logger",
        "infra",
        "core/utils",  # env_utils uses logging.getLogger() directly
    }

    violations: list[str] = []

    for py_file in sorted(SECBAAS.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue

        rel_path = str(py_file.relative_to(SECBAAS))
        # Check if this file is in an allowed prefix
        is_allowed = any(rel_path.startswith(p) for p in allowed_prefixes)
        if is_allowed:
            continue

        try:
            source = py_file.read_text()
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # Check for logging.getLogger(...)
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "getLogger"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "logging"
            ):
                violations.append(f"  {rel_path}:{node.lineno}: logging.getLogger()")

    if violations:
        raise AssertionError(
            f"\n{len(violations)} file(s) use logging.getLogger() "
            f"directly instead of secbaas.logger.get_logger():\n"
            + "\n".join(violations)
            + "\n\nUse secbaas.logger.get_logger() instead of "
            "logging.getLogger() to keep logger backends swappable."
        )
