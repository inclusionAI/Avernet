"""Resource key records."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class ResourceKeyRecord:
    """baas_resource_key table record."""

    id: int
    gmt_create: datetime
    gmt_modified: datetime
    tenant: str
    resource_key: str
    app: str


@dataclass(slots=True)
class ResourceKeyBotMappingRecord:
    """baas_resource_key_bot_mapping table record."""

    id: int
    gmt_create: datetime
    gmt_modified: datetime
    resource_key_id: int
    bot_id: str