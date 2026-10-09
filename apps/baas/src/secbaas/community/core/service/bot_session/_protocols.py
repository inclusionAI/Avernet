"""
Session Service Protocol definition.

Defines the SPI interface for bot session lifecycle management.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from ._models import PaginatedResult


@runtime_checkable
class SessionService(Protocol):
    """Protocol for bot session lifecycle management."""

    def create_session(
        self,
        *,
        bot_uuid: str,
        invoker: str,
        req: dict[str, Any],
        device_uuid: str,
        tenant: str,
        trace_id: str | None = None,
    ) -> str: ...

    def mark_running(
        self,
        session_id: str,
        context: dict[str, Any] | None = None,
    ) -> None: ...

    def mark_completed(
        self,
        session_id: str,
        result: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        err_msg: str | None = None,
    ) -> None: ...

    def mark_failed(
        self,
        session_id: str,
        err_msg: str | None = None,
        result: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None: ...

    def get_by_session_id(self, session_id: str) -> Any | None: ...

    def list_by_bot(
        self,
        bot_uuid: str,
        page: int = 1,
        page_size: int = 50,
    ) -> PaginatedResult: ...

    def list_by_time_range(
        self,
        start_time: datetime,
        end_time: datetime,
        bot_uuid: str | None = None,
    ) -> list[Any]: ...

    def list_by_bot_device_invoker(
        self,
        bot_uuid: str,
        invoker: str,
        start_time: datetime,
        end_time: datetime,
        device_uuid: str | None = None,
    ) -> list[Any]: ...

    def update_context(
        self,
        session_id: str,
        context: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        err_msg: str | None = None,
    ) -> None: ...
