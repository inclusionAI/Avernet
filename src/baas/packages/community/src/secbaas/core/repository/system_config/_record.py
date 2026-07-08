from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class SystemConfigRecord:
    """Database record for baas_system_config table.

    Columns per DDL schema:
    id, gmt_create, gmt_modified, conf_key, conf_value, env, name, description,
    creator, modifier

    Note: NO is_deleted column (D-01) - hard delete only.
    """

    id: int
    gmt_create: datetime
    gmt_modified: datetime
    conf_key: str
    conf_value: str | None
    env: str
    name: str
    description: str | None
    creator: str
    modifier: str
