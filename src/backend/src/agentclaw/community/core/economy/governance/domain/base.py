"""governance domain 共享基础工具。

领域层各实体(ticket/notification/whitelist/record)共用的纯工具,不含实体定义。
"""
from __future__ import annotations

from datetime import datetime


def _iso(value: datetime | None) -> str | None:
    """Serialize a datetime to ISO 8601 for API responses (None passes through)."""
    return value.isoformat() if value is not None else None