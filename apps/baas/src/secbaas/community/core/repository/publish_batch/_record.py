"""Publish batch repository for baas_publish_batch table.

Manages batches within a publish workflow.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class PublishBatchRecord:
    """Database record for baas_publish_batch table.

    Columns: id, gmt_create, gmt_modified, tenant, env, domain, is_deleted,
             creator, modifier, publish_id, bot_id, batch_index, batch_capacity,
             status, gmt_start, gmt_complete, error_message, extra_config
    """

    id: int
    gmt_create: datetime
    gmt_modified: datetime
    tenant: str
    env: str
    domain: str
    is_deleted: int
    creator: str
    modifier: str
    publish_id: int
    bot_id: int
    batch_index: int
    batch_capacity: int
    status: str
    gmt_start: datetime | None
    gmt_complete: datetime | None
    error_message: str | None
    extra_config: dict[str, Any]

    @property
    def stage(self) -> str:
        """Get pipeline stage from extra_config."""
        return str(self.extra_config.get("stage", "UNKNOWN"))

    @property
    def cooldown_seconds(self) -> int:
        """Get cooldown seconds from extra_config."""
        return int(self.extra_config.get("cooldown_seconds", 0))
