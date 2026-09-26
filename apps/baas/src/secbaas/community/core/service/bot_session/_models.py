"""
Bot session domain models.

Defines shared data structures for session management.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PaginatedResult:
    """Paginated query result container.

    Attributes:
        total: Total number of matching records
        page: Current page number (1-based)
        page_size: Records per page
        items: List of BotSessionRecord for this page
    """

    total: int
    page: int
    page_size: int
    items: list[Any]


@dataclass(slots=True)
class BotSession:
    """Bot session information exposed through the API layer.

    A lightweight view of session state for protocol consumers.
    Does not expose internal database record details.
    """

    session_id: str
    bot_uuid: str
    invoker: str
    status: str
    device_uuid: str
    tenant: str
    req: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    err_msg: str | None = None
    context: dict[str, Any] | None = None
