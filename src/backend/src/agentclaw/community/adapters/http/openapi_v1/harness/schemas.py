"""Public harness surface DTOs.

These are intentionally stricter than the internal /api/harness DTOs:
- entity_type and entity_id are required (no default "staff").
- bot_id lives on the URL path and is not repeated in the body.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class HarnessDiagnoseRequest(BaseModel):
    entity_type: str = Field(..., description="Entity type, e.g. staff")
    entity_id: str = Field(..., description="Entity ID (owner/workNo)")
    scan_type: str = Field(default="full", description="full / verify")
    layer: str = Field(default="L1", description="L1 / L2 / L3")
    bot_publish_id: str | None = Field(default=None, description="Bot publish ID")


class HarnessPreviewRequest(BaseModel):
    entity_type: str = Field(..., description="Entity type")
    entity_id: str = Field(..., description="Entity ID")
    scan_id: int | None = Field(default=None, description="Associated scan record ID")
    file_type: str | None = Field(default=None, description="Target file type")
    patch_id_list: list[int] = Field(..., description="Patch template IDs to preview")


class HarnessApplyRequest(BaseModel):
    entity_type: str = Field(..., description="Entity type")
    entity_id: str = Field(..., description="Entity ID")
    patch_id_list: list[int] = Field(
        default_factory=list, description="ac_harness_patch IDs to apply"
    )
    record_id: int | None = Field(default=None, description="ac_harness_patch_record ID")
    bot_publish_id: str | None = Field(default=None, description="Bot publish ID")


class HarnessRollbackRequest(BaseModel):
    entity_type: str = Field(..., description="Entity type")
    entity_id: str = Field(..., description="Entity ID")
    patch_id: int = Field(..., description="Patch ID from ac_harness_patch")
    file_type: str | None = Field(default=None, description="Target file type")
    bot_publish_id: str | None = Field(default=None, description="Bot publish ID")


class HarnessDimReportRequest(BaseModel):
    bot_publish_id: str | None = Field(default=None, description="Bot publish ID")


class HarnessDimHistoryRequest(BaseModel):
    scan_dim: str | None = Field(default=None, description="Filter by scan dimension")
    bot_publish_id: str | None = Field(default=None, description="Bot publish ID")
    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=100)
