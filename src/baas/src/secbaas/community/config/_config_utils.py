"""Config utility functions — cached access, dot-path resolution, typed enum.

Provides a Singleton wrapper around ``ConfigLoader`` and a ``ConfigPath`` enum
with environment-specific service host names.  Prod code should only access
config via these enum members so that call sites are discoverable and greppable.

Usage::

    from secbaas.community.config import get_config, get_config_by_path, ConfigPath

    cfg = get_config()
    host = get_config_by_path(cfg, ConfigPath.AGENTCLAW_PROXY_HOST_DEV)
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from secbaas.community.logger import get_logger

from ._config_loader import ConfigLoader
from ._models import Config

logger = get_logger("config_utils")

# ---------------------------------------------------------------------------
# Singleton config holder
# ---------------------------------------------------------------------------

_config: Config | None = None


def get_config(*, reload: bool = False) -> Config:
    """Return the singleton ``Config``, loading it on first call.

    Pass ``reload=True`` to force a fresh load from disk (e.g. after changing
    an overlay environment variable in tests).
    """
    global _config  # noqa: PLW0603
    if _config is None or reload:
        _config = ConfigLoader.load()
        logger.info(
            "Config loaded: app_name=%s, workers=%d",
            _config.app_name,
            _config.workers,
        )
    return _config


def reset_config() -> None:
    """Reset the cached config (useful in tests)."""
    global _config  # noqa: PLW0603
    _config = None


# ---------------------------------------------------------------------------
# Typed config-path enum
# ---------------------------------------------------------------------------


class ConfigPath(StrEnum):
    """Well-known config paths consumed by prod code.

    Each value is a dot-separated path that can be passed to
    ``get_config_by_path()``.  Add new members here when a new config
    key is consumed outside the config package.
    """

    # user_config → agentclawproxy (host map by env)
    AGENTCLAW_PROXY_HOST_DEV = "user_config.agentclawproxy.host.dev"
    AGENTCLAW_PROXY_HOST_PRE = "user_config.agentclawproxy.host.pre"
    AGENTCLAW_PROXY_HOST_PROD = "user_config.agentclawproxy.host.prod"

    # user_config → secbaas (callback server map by env)
    SECBAAS_CALLBACK_HOST_DEV = "user_config.secbaas.callback.host.dev"
    SECBAAS_CALLBACK_HOST_PRE = "user_config.secbaas.callback.host.pre"
    SECBAAS_CALLBACK_HOST_PROD = "user_config.secbaas.callback.host.prod"

    # user_config → api_gateway
    API_GATEWAY_ADMIN_OPERATORS = "user_config.api_gateway.admin_operators"

    # user_config → env (deployment env detection)
    DEPLOY_ENV_VAR = "user_config.env.deploy_env_var"

    # Physical workspace partition selected by the active deployment profile.
    WORKSPACE_ENV_FOLDER = "user_config.workspace.env_folder"


# ---------------------------------------------------------------------------
# Dot-path resolution
# ---------------------------------------------------------------------------


def get_config_by_path(
    config: Config,
    path: ConfigPath | str,
    default: Any = None,
) -> Any:
    """Resolve *path* against *config* and return the value.

    The path is split on ``"."`` and each segment is looked up first as an
    attribute, then as a dict key (for ``extra`` fields on ``UserConfig``).

    Parameters
    ----------
    config:
        A ``Config`` instance (or any object with attribute access).
    path:
        A ``ConfigPath`` enum member or an arbitrary dot-separated string
        (e.g. ``"user_config.some.custom.key"``).
    default:
        Value returned when any segment along the path is missing.

    Returns
    -------
    The resolved value, or *default* if the path cannot be fully resolved.
    """
    path_str = path.value if isinstance(path, ConfigPath) else path

    if not path_str:
        return default

    segments = path_str.split(".")
    current: Any = config

    for segment in segments:
        try:
            # 1. Attribute access (works for pydantic BaseModel fields)
            current = getattr(current, segment)
        except AttributeError:
            try:
                # 2. Dict-like access (works for UserConfig "extra" fields)
                current = current[segment]
            except (KeyError, TypeError):
                logger.debug("Path %r not found at segment %r", path_str, segment)
                return default

    return current
