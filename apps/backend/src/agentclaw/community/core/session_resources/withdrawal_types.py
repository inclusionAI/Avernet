"""Transport-independent snapshots of durable withdrawal delivery state."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ResourceWithdrawal:
    event_id: str
    res_id: str
    tenant: str
    status: str
    attempts: int
    retry_count: int
    available_at: datetime
    created_at: datetime
    updated_at: datetime
    lease_token: str | None
    lease_until: datetime | None
    accepted_at: datetime | None
    replay_actor: str
    replay_reason: str
    last_error: str
