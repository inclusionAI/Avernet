from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class TtlRenewalScheduleRecord:
    """Database record for baas_bot_ttl_renewal_schedule table.

    Columns per DDL schema (design doc §7.2):
    id, gmt_create, gmt_modified, sandbox_id, source_table, source_id,
    next_renew_at, renew_fail_count, status, last_renewed_at, env
    """

    id: int
    gmt_create: datetime
    gmt_modified: datetime
    sandbox_id: str
    source_table: str
    source_id: int
    next_renew_at: datetime
    renew_fail_count: int
    status: str
    last_renewed_at: datetime | None
    env: str
