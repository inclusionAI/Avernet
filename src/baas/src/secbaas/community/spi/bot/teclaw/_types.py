"""Internal SPI dataclass types for TeClaw bot plugin operations.

These dataclasses represent the TeClaw HTTP API response data shapes,
aligned with the emergencyOnline and get endpoint schemas defined in
docs/teClawHttpAPI.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class _BotCreateResult:
    """Result of a CREATE operation (POST emergencyOnline)."""

    teclaw_bot_id: str
    status: str
    teclaw_bot_config: dict[str, Any] | None = None


@dataclass(slots=True)
class _BotDestroyResult:
    """Result of a DELETE operation (POST emergencyOnline)."""

    teclaw_bot_id: str
    status: str


@dataclass(slots=True)
class _BotUpdateResult:
    """Result of an UPDATE operation (POST emergencyOnline)."""

    teclaw_bot_id: str
    status: str
    teclaw_bot_config: dict[str, Any] | None = None


@dataclass(slots=True)
class _BotRestartResult:
    """Result of a restart operation (proxied to UPDATE emergencyOnline)."""

    teclaw_bot_id: str
    status: str


@dataclass(slots=True)
class _BotInfo:
    """Bot info from a GET /get query."""

    teclaw_bot_id: str
    status: str
    teclaw_bot_config: dict[str, Any] | None = None
    outbound_rule: dict[str, Any] | None = None
