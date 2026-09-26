from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class TenantRecord:
    """Database record for baas_tenant table.

    Columns per DDL schema:
    id, gmt_create, gmt_modified, is_deleted, creator, modifier,
    name, description, extra_config, env

    Note: baas_tenant has env column for environment isolation.
    Note: tenant_id and type fields removed - now use name as business key.
    """

    id: int
    gmt_create: datetime
    gmt_modified: datetime
    is_deleted: int
    creator: str
    modifier: str
    name: str
    description: str | None
    extra_config: dict[str, Any]
    env: str
