"""Publish record repository for baas_publish_record table.

Tracks individual device operations within publish batches.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class PublishRecordExtraConfig:
    """Typed model for baas_publish_record.extra_config.

    Captures device identity metadata at record creation time so the
    device's provider identity is preserved even after the source
    baas_device record is overwritten by subsequent publishes.
    """

    device_uuid: str | None = None
    provider_device_id: str | None = None


@dataclass(slots=True)
class PublishRecordRecord:
    """Database record for baas_publish_record table.

    Columns: id, gmt_create, gmt_modified, tenant, env, domain, is_deleted,
             creator, modifier, device_id, bot_id, publish_id, batch_id,
             event_type, trigger_source, publish_reason, result_status,
             result_message, extra_config
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
    device_id: int | None
    bot_id: int | None
    publish_id: int | None
    batch_id: int | None
    event_type: str
    trigger_source: str | None
    publish_reason: str | None
    result_status: str
    result_message: str | None
    extra_config: dict[str, Any]
    device_uuid: str | None = None
