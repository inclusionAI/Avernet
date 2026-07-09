from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class BotRecord:
    """Database record for baas_bot table.

    Columns per DDL schema:
    id, gmt_create, gmt_modified, bot_uuid, tenant, env, domain, is_deleted,
    creator, modifier, status, name, description, template_uuid, replica_desired,
    replica_minimum, replica_maximum, auto_scaling_enabled, sla_grade, extra_config
    """

    id: int
    gmt_create: datetime
    gmt_modified: datetime
    bot_uuid: str
    tenant: str
    env: str
    domain: str
    is_deleted: int
    creator: str
    modifier: str
    status: str
    name: str
    description: str | None
    template_uuid: str | None
    replica_desired: int
    replica_minimum: int
    replica_maximum: int
    auto_scaling_enabled: int
    sla_grade: str
    extra_config: dict[str, Any]
