"""BotCommonConfigRecord → API view shaping (adapter-side serialization).

The core service returns the domain record; the wire representation — field
set, ISO-8601 dates, and a JSON-decoded ``config_value`` with a raw fallback
for legacy non-JSON rows — is decided here, not in core.
"""
from __future__ import annotations

import json
from typing import Any

from agentclaw.community.core.common_config.models import BotCommonConfigRecord


def record_to_dict(record: BotCommonConfigRecord) -> dict[str, Any]:
    try:
        value: Any = json.loads(record.config_value)
    except (json.JSONDecodeError, TypeError):
        value = record.config_value
    return {
        "id": record.id,
        "bot_id": record.bot_id,
        "entity_id": record.entity_id,
        "env": record.env,
        "config_key": record.config_key,
        "config_value": value,
        "is_delete": record.is_delete,
        "gmt_create": record.gmt_create.isoformat() if record.gmt_create else None,
        "gmt_modified": record.gmt_modified.isoformat()
        if record.gmt_modified
        else None,
    }
