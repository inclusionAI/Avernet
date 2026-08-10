"""Quality domain records."""
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class QualityTaskRecord:
    """Database record for ac_bot_quality_task."""

    id: int
    uuid: str | None
    task_type: str
    biz_type: str
    status: str
    bot_id: str | None
    owner_id: str | None
    ext: dict[str, Any]  # JSON field, parsed as dict
    operator_id: str | None
    env: str | None
    gmt_create: datetime | None
    gmt_modified: datetime | None
