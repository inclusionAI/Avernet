"""Managed Bot engine admission; dependencies resolve after inventory import."""

from collections.abc import Callable
from typing import Any


def _restart_bot_dependency() -> Callable[..., Any]:
    from .managed_bots.router import restart_engine_targets

    return restart_engine_targets


def _status_bot_dependency() -> Callable[..., Any]:
    from .managed_bots.router import status_engine_targets

    return status_engine_targets


ENGINE_BOT_DEPENDENCIES: dict[tuple[str, str], Callable[[], Callable[..., Any]]] = {
    ('POST', '/openapi/v1/bots/restart-for-others'): _restart_bot_dependency,
    ('POST', '/openapi/v1/bots/status-for-others'): _status_bot_dependency,
}
