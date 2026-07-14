from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class BotDeviceRelRecord:
    """Database record for baas_bot_device_rel table.

    Columns per DDL schema:
    id, gmt_create, gmt_modified, tenant, env, domain, is_deleted,
    creator, modifier, bot_id, device_uuid
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
    bot_id: int
    device_uuid: str
