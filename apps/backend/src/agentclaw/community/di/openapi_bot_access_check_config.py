"""Deployment config for the transitional public Bot access check.

Holds ``OpenApiBotAccessCheckConfig`` out of ``di/config.py`` (zero headroom
under the 1000-line cap). The provider lives in
``di/modules/openapi_bot_access_check_config_module.py``; the consumer is
``adapters/http/openapi_v1/bot_access_check/router.py``.

**Transitional.** The operation it gates was built for one integration's
interim needs and is expected to be removed; delete this block with it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OpenApiBotAccessCheckConfig:
    """``openapi.bot_access_check`` — applications allowed to call the check.

    Matched exactly (case-sensitive) against the calling application's
    ``app_name`` on the verified principal. Empty (the default) admits nobody.
    """

    app_names: frozenset[str] = frozenset()


__all__ = ["OpenApiBotAccessCheckConfig"]
