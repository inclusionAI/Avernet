"""Channel domain records."""
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class ChannelRecord:
    """Database record for ac_channel_config."""

    id: int
    type: str
    description: str | None
    identity_id: str
    bind_bot_id: str
    config: dict[str, Any]
    status: str
    deleted: int
    gmt_create: datetime | None
    gmt_modified: datetime | None
    env: str
    stage: str | None
