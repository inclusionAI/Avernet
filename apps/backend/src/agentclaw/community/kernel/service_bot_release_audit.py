"""Frozen submission facts passed to the transactional publication audit Plugin."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

_DATE_FIELDS = frozenset({"bot_created_at", "change_created_at", "submitted_at", "finished_at"})
_EXT_FIELDS = frozenset({"schema_version", "operation_type", "request_id", "operation_ids", "error_code", "error_message"})


def _utc(value: datetime) -> datetime:
    # Audit event timestamps are produced in UTC by the publication transaction.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class ReleaseAuditSnapshot:
    """Frozen facts for one accepted stage change; terminal events are complete."""

    change_id: str
    source_bot_pk: int
    bot_id: str
    entity_id: str
    env: str
    tenant_id: str
    creator_id: str | None
    operator_id: str
    bot_created_at: datetime | None
    change_created_at: datetime
    submitted_at: datetime | None
    before_stage: str | None
    desired_stage: str
    before_status: str | None
    publish_id: int
    version: int | None
    finished_at: datetime | None
    finish_status: str
    final_publish_status: str | None
    ext_json: str = "{}"

    def __post_init__(self) -> None:
        if not all((self.change_id, self.bot_id, self.entity_id, self.env, self.tenant_id, self.operator_id)):
            raise ValueError("audit identity fields are required")
        if self.desired_stage not in {"draft", "verify", "online"}:
            raise ValueError("invalid desired stage")
        if self.before_stage not in {None, "draft", "verify", "online"}:
            raise ValueError("invalid before stage")
        if self.finish_status not in {"pending", "succeeded", "failed", "cancelled"}:
            raise ValueError("invalid audit finish status")
        if (self.finish_status == "pending") != (self.finished_at is None):
            raise ValueError("finished_at must identify completed audit rows")
        ext = json.loads(self.ext_json)
        if not isinstance(ext, dict) or set(ext) - _EXT_FIELDS:
            raise ValueError("unsupported audit extension fields")
        for key, value in ext.items():
            if key == "operation_ids":
                if not isinstance(value, list) or any(type(item) is not int for item in value):
                    raise ValueError("invalid audit extension operation_ids")
            elif value is not None and not isinstance(value, (str, int, bool)):
                raise ValueError("invalid audit extension value")
        object.__setattr__(self, "ext_json", json.dumps(ext, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        for name in _DATE_FIELDS:
            value = getattr(self, name)
            if value is not None and name != "bot_created_at":
                object.__setattr__(self, name, _utc(value))
