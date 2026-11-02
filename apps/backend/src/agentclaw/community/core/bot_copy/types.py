"""Business types for bot-copy: status enum, ledger row, errors.

`ac_bot_copy`'s `status` column is `varchar(24)` with no DB CHECK (per spec
§7.5.3); the values below are the code-enforced vocabulary. Under derive-on-read
the column is written at INSERT only (``INITIAL``) and is NOT CAS-driven — live
status is computed from `ac_bot_config_manifest_apply` + `ac_bots.status` by
`compute_copy_status`. The full enum is kept for spec alignment and for the
retry CAS throttle's terminal set.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Optional

from pydantic import BaseModel, Field


class BotCopyStatus(StrEnum):
    """The `ac_bot_copy.status` vocabulary (spec §7.5.2). Closed set."""

    INITIAL = "INITIAL"
    CREATED = "CREATED"
    AUTH_PENDING = "AUTH_PENDING"
    CONFIG_COPIED = "CONFIG_COPIED"
    FILE_SYNCED = "FILE_SYNCED"
    BOT_STARTED = "BOT_STARTED"
    CAPSET_SYNCED = "CAPSET_SYNCED"
    CONVERGED = "CONVERGED"
    FAILED = "FAILED"

    @classmethod
    def terminal(cls) -> frozenset["BotCopyStatus"]:
        """Statuses that operator retry must not touch."""
        return frozenset({cls.CONVERGED, cls.FAILED})


class CopyOperationNotFound(Exception):
    """Raised when a ``copy_state(operation_id)`` reference names no row."""


class BotCopyRow(BaseModel):
    """One `ac_bot_copy` row — the operation ledger record."""

    id: Optional[int] = Field(default=None, description="Primary key")
    operation_id: str = Field(..., description="FE polling correlation id (UNIQUE per env)")
    source_bot_id: str = Field(..., description="源 ac_bots.bot_id")
    source_owner_id: str = Field(..., description="源 ac_bots.owner_id")
    dst_bot_id: str = Field(..., description="dst ac_bots.bot_id")
    dst_owner_id: str = Field(..., description="dst ac_bots.owner_id")
    operator_id: str = Field(..., description="trigger actor")
    status: BotCopyStatus = Field(default=BotCopyStatus.INITIAL, description="Ledger status (derive-on-read writes INITIAL only)")
    request_detail: Optional[dict[str, Any]] = Field(default=None, description="JSON: request-side params blob (excludes + future params)")
    progress_ext: Optional[dict[str, Any]] = Field(default=None, description="JSON: step intermediate state for resync")
    retries: int = Field(default=0, description="operator retry attempt count")
    last_retry_at: Optional[datetime] = Field(default=None, description="operator retry CAS throttle stamp")
    last_error: Optional[str] = Field(default=None, description="terminal FAILED last exception summary")
    env: str = Field(..., description="prod/pre/dev/singlebox; all queries filter")
    avernet_tenant: str = Field(default="teamclaw", description="data-isolation tenant")
    gmt_create: Optional[datetime] = Field(default=None, description="Row created")
    gmt_modified: Optional[datetime] = Field(default=None, description="Row last modified")
