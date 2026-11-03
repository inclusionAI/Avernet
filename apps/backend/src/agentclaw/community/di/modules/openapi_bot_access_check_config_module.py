"""DI provider for ``OpenApiBotAccessCheckConfig``.

Mirrors ``openapi_user_id_bypass_config_module``: one ``@singleton @provider``
that reads the ``openapi`` block of ``user_config`` strictly. Registered in
``di/container.py`` and resolved there unconditionally, so a malformed block
fails the boot rather than the first request.

YAML shape under ``user_config.openapi``::

    openapi:
      bot_access_check:
        allowed_app_names: []   # app_name values allowed to call the check

**Transitional** — see ``di/openapi_bot_access_check_config.py``.
"""
from __future__ import annotations

from typing import Any

from injector import Module, provider, singleton

from agentclaw.community.di.modules import config_module
from agentclaw.community.di.openapi_bot_access_check_config import (
    OpenApiBotAccessCheckConfig,
)

#: The ``user_config`` block this module reads (shared with the user_id bypass).
BLOCK = "openapi"

#: The key under :data:`BLOCK` holding this operation's settings.
KEY = "bot_access_check"

#: The key under :data:`KEY` holding the allowlisted application names.
APP_NAMES = "allowed_app_names"

_ALLOWED_KEYS = frozenset({APP_NAMES})


def _settings() -> dict[str, Any]:
    """Pull ``openapi.bot_access_check`` out of ``user_config`` (``{}`` if missing)."""
    block = config_module.read_user_config().get(BLOCK)
    if block is None:
        return {}
    if not isinstance(block, dict):
        raise ValueError(f"{BLOCK} must be a mapping")
    raw = block.get(KEY)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{BLOCK}.{KEY} must be a mapping")
    return dict(raw)


def _app_names(raw: Any) -> frozenset[str]:
    """Validate the allowlist: a YAML sequence of non-blank strings, or empty.

    Strict for the same reason as the bypass list: a scalar or a non-string
    entry would otherwise read as "nobody" or coerce into an accidental match.
    """
    if raw is None:
        return frozenset()
    if not isinstance(raw, list):
        raise ValueError(f"{BLOCK}.{KEY}.{APP_NAMES} must be a list of app names")
    names: set[str] = set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError(
                f"{BLOCK}.{KEY}.{APP_NAMES}[{index}] must be a non-blank string, "
                f"got {entry!r}"
            )
        names.add(entry)
    return frozenset(names)


class OpenApiBotAccessCheckConfigModule(Module):
    """Bind ``OpenApiBotAccessCheckConfig`` to ``openapi.bot_access_check``."""

    @singleton
    @provider
    def openapi_bot_access_check(self) -> OpenApiBotAccessCheckConfig:
        settings = _settings()
        unknown = sorted(str(key) for key in settings if str(key) not in _ALLOWED_KEYS)
        if unknown:
            raise ValueError(
                f"unknown {BLOCK}.{KEY} key(s) "
                + ", ".join(repr(key) for key in unknown)
            )
        return OpenApiBotAccessCheckConfig(
            app_names=_app_names(settings.get(APP_NAMES))
        )


__all__ = [
    "APP_NAMES",
    "BLOCK",
    "KEY",
    "OpenApiBotAccessCheckConfigModule",
]
