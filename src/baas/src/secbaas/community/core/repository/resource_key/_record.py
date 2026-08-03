"""Resource key records — canonical definitions live in api.api_gateway."""

from dataclasses import dataclass
from datetime import datetime

from secbaas.community.api.api_gateway import ResourceKeyRecord

__all__ = ["ResourceKeyRecord", "ResourceKeyBotMappingRecord"]


@dataclass(slots=True)
class ResourceKeyBotMappingRecord:
    """baas_resource_key_bot_mapping table record."""

    id: int
    gmt_create: datetime
    gmt_modified: datetime
    resource_key_id: int
    bot_id: str
