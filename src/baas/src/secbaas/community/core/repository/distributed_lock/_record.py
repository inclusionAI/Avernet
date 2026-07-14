from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class LockRecord:
    """Database record for distributed lock."""

    id: int
    lock_name: str
    lock_holder: str
    expire_time: datetime | None
    env: str | None
    gmt_create: datetime | None
    gmt_modified: datetime | None
