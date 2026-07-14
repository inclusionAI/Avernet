"""
Device repository for baas_device table.

Implements ZDAS sync SQL pattern with soft-delete support per D-04.
Includes list_by_bot_id with JOIN to baas_bot_device_rel table.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class DeviceRecord:
    """Database record for baas_device table.

    Columns per DDL schema:
    id, gmt_create, gmt_modified, device_uuid, tenant, env, domain, is_deleted,
    creator, modifier, status, provider_type, provider_device_id,
    provider_device_props, extra_config, err_msg
    """

    id: int
    gmt_create: datetime
    gmt_modified: datetime
    device_uuid: str
    tenant: str
    env: str
    domain: str
    is_deleted: int
    creator: str
    modifier: str
    status: str
    provider_type: str | None
    provider_device_id: str | None
    provider_device_props: dict[str, Any]
    extra_config: dict[str, Any]
    err_msg: str | None = None
