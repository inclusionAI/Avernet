"""DI provider for ``OpenApiUserIdBypassConfig``.

Lives OUT of ``di/modules/config_module.py`` (which sits at zero headroom under
the 1000-line architecture cap). Mirrors the ``DesktopSkillRecoveryConfigModule``
pattern: a small ``Module`` with one ``@singleton @provider`` that reads the
``openapi`` block of ``user_config`` (via ``config_module.read_user_config``),
validates it strictly, and returns an ``OpenApiUserIdBypassConfig``. Registered
in ``di/container.py`` alongside ``ConfigModule`` and resolved by the pre/prod
eager binding check, so a bad value fails the rollout rather than the first
request.

YAML shape under ``user_config.openapi``::

    openapi:
      user_id_bypass: []   # user_id values accepted without the caller comparison

**The list must be empty in ``pre`` and ``prod``.** Each listed id lets *any*
authenticated person act as that user on every user-scoped public operation, so
the provider refuses to build the config there (``ValueError``) instead of
installing it. The gate reads the same normalized environment the composition
root uses for the strict signing-key check (``get_current_env()``: ``prepub``
normalizes to ``pre``, ``gray`` to ``prod``), so the two fail a deployment on the
same axis.
"""
from __future__ import annotations

from typing import Any

from injector import Module, provider, singleton

from agentclaw.community.di.modules import config_module
from agentclaw.community.di.openapi_user_id_bypass_config import (
    OpenApiUserIdBypassConfig,
)
from agentclaw.community.utils.env_utils import get_current_env

#: The ``user_config`` block this module reads.
BLOCK = "openapi"

#: The key under :data:`BLOCK` holding the bypass list.
KEY = "user_id_bypass"

#: The normalized environments in which the list must be empty.
PROTECTED_ENVS = frozenset({"pre", "prod"})

_ALLOWED_KEYS = frozenset({KEY})


def _block() -> dict[str, Any]:
    """Pull the ``openapi`` block out of ``user_config`` (``{}`` if missing)."""
    raw = config_module.read_user_config().get(BLOCK)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{BLOCK} must be a mapping")
    return dict(raw)


def _user_ids(raw: Any) -> frozenset[str]:
    """Validate the bypass list: a YAML sequence of non-blank strings, or empty.

    Strict on purpose — a scalar, a mapping, a blank entry or a non-string id
    would otherwise either be silently ignored (the bypass reads as empty and
    the operator wonders why it does nothing) or, worse, coerce to a value that
    matches a real user by accident.
    """
    if raw is None:
        return frozenset()
    if not isinstance(raw, list):
        raise ValueError(f"{BLOCK}.{KEY} must be a list of user ids")
    ids: set[str] = set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError(
                f"{BLOCK}.{KEY}[{index}] must be a non-blank string, got {entry!r}"
            )
        ids.add(entry)
    return frozenset(ids)


def validate_for_environment(
    config: OpenApiUserIdBypassConfig, env: str
) -> OpenApiUserIdBypassConfig:
    """Return ``config`` unchanged, or raise when ``env`` may not carry a bypass.

    Split out of the provider so the rule is testable without an injector: the
    provider calls it with the live ``get_current_env()``; tests call it with
    each environment spelling directly.
    """
    if config.user_ids and env in PROTECTED_ENVS:
        raise ValueError(
            f"{BLOCK}.{KEY} must be empty in {env!r}: it lists "
            f"{len(config.user_ids)} user id(s) that any authenticated caller "
            "could act as on every user-scoped /openapi/v1 operation. Remove "
            "the entries from this environment's configuration."
        )
    return config


class OpenApiUserIdBypassConfigModule(Module):
    """Bind ``OpenApiUserIdBypassConfig`` to the ``openapi`` deployment block."""

    @singleton
    @provider
    def openapi_user_id_bypass(self) -> OpenApiUserIdBypassConfig:
        block = _block()
        unknown = sorted(str(key) for key in block if str(key) not in _ALLOWED_KEYS)
        if unknown:
            raise ValueError(
                f"unknown {BLOCK} key(s) " + ", ".join(repr(key) for key in unknown)
            )
        config = OpenApiUserIdBypassConfig(user_ids=_user_ids(block.get(KEY)))
        return validate_for_environment(config, get_current_env())


__all__ = [
    "BLOCK",
    "KEY",
    "PROTECTED_ENVS",
    "OpenApiUserIdBypassConfigModule",
    "validate_for_environment",
]
