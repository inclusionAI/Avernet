"""
Device template repository for baas_device_template table.

Implements ZDAS sync SQL pattern with soft-delete support per D-04.
Note: Has tenant column but no env/domain (different from bot/device tables).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class DeviceTemplateRecord:
    """Database record for baas_device_template table.

    Columns per DDL schema:
    id, gmt_create, gmt_modified, template_uuid, tenant, is_deleted, creator, modifier,
    status, name, description, config, template_id, type

    Note: Has tenant column but NO env/domain columns.
    """

    id: int
    gmt_create: datetime
    gmt_modified: datetime
    template_uuid: str
    tenant: str
    is_deleted: int
    creator: str
    modifier: str
    status: str
    name: str
    description: str | None
    config: dict[str, Any]
    template_id: int
    type: str
