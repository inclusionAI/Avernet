"""Unified logger factory for agentclaw — a profile-driven registry.

Usage:
    from agentclaw.community.log import get_logger
    logger = get_logger()

``get_logger`` returns whatever the currently-installed *logger factory*
produces. The default factory is :func:`logging.getLogger` (standard library),
which **is** the community / test / singlebox behavior — those profiles never
override it. Logs propagate to the root logger (the community entrypoint wires
it via ``logging.basicConfig`` → ``StreamHandler`` → stderr).

The corp/prod profile installs a different factory at its composition root via
:func:`set_logger_factory` (a company logger with trace-ID injection, file
rotation, and separate error/fatal files). That installation lives in corp
code — this module names no corp/internal package, so a community distribution
imports it with nothing extra installed.

Selection is therefore profile-driven, not a runtime import probe: the default
is the community decision, and exactly one profile (corp) opts out of it.
"""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable

# The active factory. Default = stdlib; corp overrides via set_logger_factory.
_factory: Callable[[str], logging.Logger] = logging.getLogger


def set_logger_factory(factory: Callable[[str], logging.Logger]) -> None:
    """Install the logger factory ``get_logger`` delegates to.

    Called once, at the composition root, by profiles that need a non-stdlib
    logger (today: corp, which installs the company logger). Community / test /
    singlebox leave the stdlib default in place.
    """
    global _factory
    _factory = factory


def get_logger(name: str = "start") -> logging.Logger:
    """Return a logger from the currently-installed factory."""
    return _factory(name)


_TASK_LOGGER_PREFIXES = (
    "agentclaw.community.core.task",
    "task.",
)


class _TaskExecutionFilter(logging.Filter):
    """Keep task execution records in the dedicated task log."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith(_TASK_LOGGER_PREFIXES)


def configure_task_file_logging(
    path: str | Path,
    *,
    max_bytes: int = 20 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Handler:
    """Route task-module records to a rotating file while keeping root output intact.

    The handler is attached to the root logger so both ``task.*`` loggers and
    ``agentclaw.community.core.task.*`` loggers are captured, including records
    emitted by task integration adapters. A marker prevents duplicate handlers
    when local bootstrap is invoked more than once in a process.
    """
    root = logging.getLogger()
    target = str(Path(path).expanduser().resolve())
    for handler in root.handlers:
        if getattr(handler, "_agentclaw_task_log_path", None) == target:
            return handler

    log_path = Path(target)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    handler._agentclaw_task_log_path = target  # type: ignore[attr-defined]
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s"
            " - [%(process)d] - [%(processName)s] - %(message)s"
        )
    )
    handler.addFilter(_TaskExecutionFilter())
    root.addHandler(handler)
    root.info("Task execution file logging enabled: %s", target)
    return handler
