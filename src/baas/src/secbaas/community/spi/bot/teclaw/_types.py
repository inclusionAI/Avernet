"""Internal SPI dataclass types for TeClaw bot plugin operations.

These dataclasses represent the TeClaw HTTP API response data shapes,
aligned with the emergencyOnline and get endpoint schemas defined in
docs/teClawHttpAPI.md.

Async callback contract:
    Plugins may return :class:`BotAsyncTaskResult` instead of the sync
    result types when a ``DeviceCallbackContext`` is provided to the
    call. ``BotAsyncTaskResult`` carries ``task_id``, ``bot_id``,
    ``operation``, ``status``, and optional ``version`` for correlation
    with the subsequent TeClaw platform callback.
    :class:`DeviceCallbackContext` lives at the API layer
    (``secbaas.community.api.device_manage``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class BotCreateResult:
    """Result of a CREATE operation (POST emergencyOnline)."""

    teclaw_bot_id: str
    status: str
    teclaw_bot_config: dict[str, Any] | None = None


@dataclass(slots=True)
class BotDestroyResult:
    """Result of a DELETE operation (POST emergencyOnline)."""

    teclaw_bot_id: str
    status: str


@dataclass(slots=True)
class BotUpdateResult:
    """Result of an UPDATE operation (POST emergencyOnline)."""

    teclaw_bot_id: str
    status: str
    teclaw_bot_config: dict[str, Any] | None = None


@dataclass(slots=True)
class BotRestartResult:
    """Result of a restart operation (proxied to UPDATE emergencyOnline)."""

    teclaw_bot_id: str
    status: str


@dataclass(slots=True)
class BotInfo:
    """Bot info from a GET /get query."""

    teclaw_bot_id: str
    status: str
    teclaw_bot_config: dict[str, Any] | None = None
    outbound_rule: dict[str, Any] | None = None


@dataclass(slots=True)
class BotAsyncTaskResult:
    """Initial response from async-mode emergencyOnline.

    Carries task_id for correlation; final result arrives via TeClaw callback.
    `operation` mirrors the emergencyOnline request operation (CREATE/UPDATE/DELETE).
    `status` is the in-flight status (e.g., "RUNNING").
    """

    task_id: str
    bot_id: str | None
    operation: str
    status: str
    version: int | None = None
