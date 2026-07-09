"""Quality module internal data structures.

These are the dataclasses used within the core layer, separate from
HTTP request/response models (which live in adapters/http/quality/schemas.py).
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class QualityTask:
    """Database record for ac_bot_quality_task."""

    id: int
    uuid: str
    task_type: str
    biz_type: str
    status: str
    bot_id: str | None
    owner_id: str | None
    version: str | None  # 版本号
    ext: dict[str, Any]  # JSON field, parsed as dict
    operator_id: str | None
    env: str | None
    gmt_create: datetime | None
    gmt_modified: datetime | None


@dataclass(slots=True)
class QualityTaskCreate:
    """Parameters for creating a quality task."""

    task_type: str
    biz_type: str
    bot_id: str | None = None
    owner_id: str | None = None
    version: str | None = None
    ext: dict[str, Any] | None = None
    operator_id: str | None = None


@dataclass(slots=True)
class QualityTaskUpdate:
    """Parameters for updating a quality task."""

    status: str | None = None
    version: str | None = None
    ext: dict[str, Any] | None = None
