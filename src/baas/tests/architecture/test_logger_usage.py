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


# --- Canonical logger name whitelist ---

ALLOWED_LOGGER_NAMES = frozenset(
    {
        "core-service",
        "bcn-converter",  # core/service/sse/**
        "orm",
        "router",
        "router-open-api",
        "router-gateway",
        "router-admin",
        "core-bot-run",
        "core-scheduler",
        "database",
        "plugin-sandbox",
        "plugin-bot-service",
        "plugin-auth",
        "bootstrap",
        "config",
        "webserver",
        "cache",
        "secret",
        # Digest stream deliberately kept out of the shared application logs:
        # monitoring collects ``arca-renew-digest.log`` as a standalone,
        # comma-separated feed of TTL renewal outcomes.
        "arca-renew-digest",
    }
)

CANONICAL_NAME_PATTERN = r"^[a-z][a-z0-9-]*$"


def test_logger_names_are_canonical():
    """All ``get_logger("...")`` calls must use a canonical name from the
    whitelist and match the naming style ``^[a-z][a-z0-9-]*$``.

    Logger names become log file names (``{name}.log``); reusing existing
    canonical names reduces log-file fragmentation.  Adding a new name
    requires updating ``ALLOWED_LOGGER_NAMES`` in this test.
    """
    import re

    name_violations: list[str] = []
    style_violations: list[str] = []

    for py_file in sorted(SECBAAS.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue

        rel_path = str(py_file.relative_to(SECBAAS))

        try:
            source = py_file.read_text()
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # Match get_logger("...")
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "get_logger"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                name = node.args[0].value
                if name not in ALLOWED_LOGGER_NAMES:
                    name_violations.append(
                        f'  {rel_path}:{node.lineno}: get_logger("{name}")'
                    )
                if not re.match(CANONICAL_NAME_PATTERN, name):
                    style_violations.append(
                        f'  {rel_path}:{node.lineno}: get_logger("{name}") '
                        f"does not match {CANONICAL_NAME_PATTERN}"
                    )

    messages = []
    if name_violations:
        messages.append(
            f"\n{len(name_violations)} call(s) use non-canonical logger names:\n"
            + "\n".join(name_violations)
            + "\n\nAllowed names: "
            + ", ".join(sorted(ALLOWED_LOGGER_NAMES))
        )
    if style_violations:
        messages.append(
            f"\n{len(style_violations)} call(s) violate the naming style:\n"
            + "\n".join(style_violations)
        )
    if messages:
        raise AssertionError("\n".join(messages))
