"""Publish repository for baas_publish table.

Implements ZDAS sync SQL pattern with soft-delete support.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class PublishRecord:
    """Database record for baas_publish table.

    Columns: id, gmt_create, gmt_modified, tenant, env, domain, is_deleted,
             creator, modifier, bot_id, publish_type, name, description,
             publisher, replica_desired, batch_capacity, batch_number,
             cooldown_seconds, config_version, status, last_publish_id,
             changelog, extra_config
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
    publish_type: str
    name: str | None
    description: str | None
    publisher: str | None
    replica_desired: int | None
    batch_capacity: int | None
    batch_number: int | None
    cooldown_seconds: int | None
    config_version: str | None
    status: str
    last_publish_id: int | None
    changelog: str | None
    extra_config: dict[str, Any]
