"""Managed Bot engine admission; dependencies resolve after inventory import."""

from collections.abc import Callable
from typing import Any

from .admission_modes import AdmissionMode


def _restart_bot_dependency() -> Callable[..., Any]:
    from .managed_bots.router import restart_engine_targets

    return restart_engine_targets


def _status_bot_dependency() -> Callable[..., Any]:
    from .managed_bots.router import status_engine_targets

    return status_engine_targets


_CODING_ENGINES = frozenset({'claude_code', 'aicoding'})

MANAGED_BOT_ADMISSION = {
    ('POST', '/openapi/v1/bots/restart-for-others'): AdmissionMode.ENGINE(
        engines=_CODING_ENGINES,
        bot_dependency=_restart_bot_dependency,
    ),
    ('POST', '/openapi/v1/bots/status-for-others'): AdmissionMode.ENGINE(
        engines=_CODING_ENGINES,
        bot_dependency=_status_bot_dependency,
    ),
}
