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
