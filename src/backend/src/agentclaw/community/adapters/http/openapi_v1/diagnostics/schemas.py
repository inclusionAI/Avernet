"""Public contracts for Bot health diagnosis."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


HealthDiagnosisStatus = Literal[
    "not_run",
    "scanning",
    "scan_completed",
    "patching",
    "completed",
    "failed",
]


class HealthCheckItem(BaseModel):
    """Progress or result for one diagnosed configuration area."""

    name: str = Field(description="Configuration area or diagnostic rule name.")
    status: str = Field(description="Current state of this diagnostic item.")
    result: str | None = Field(
        default=None,
        description="Result such as pass, warning, fail, or error when available.",
    )
    score: int | None = Field(
        default=None, ge=0, le=100, description="Item score from 0 to 100."
    )
    duration_ms: int | None = Field(
        default=None, ge=0, description="Time spent on this item in milliseconds."
    )


class HealthFindingDetail(BaseModel):
    """One issue discovered by the health diagnosis."""

    rule_id: str = Field(description="Stable diagnostic rule identifier.")
    name: str = Field(description="Diagnostic rule name.")
    message: str = Field(description="Description of the issue and suggested action.")
    risk_level: str = Field(description="Finding severity level.")
    result: str = Field(description="Finding result such as warning or fail.")
    score: int = Field(ge=0, le=100, description="Finding score from 0 to 100.")


class HealthFindingGroup(BaseModel):
    """Findings grouped by the configuration area that was checked."""

    check_item: str = Field(description="Configuration area that was checked.")
    findings: list[HealthFindingDetail] = Field(
        description="Issues found in this configuration area."
    )


class BotHealth(BaseModel):
    """Latest or explicitly requested Bot health diagnosis state."""

    found: bool = Field(description="Whether a matching diagnosis exists.")
    bot_id: str = Field(description="Stable Bot identifier.")
    scan_id: int | None = Field(
        default=None, description="Diagnosis identifier used for polling."
    )
    status: HealthDiagnosisStatus = Field(description="Diagnosis lifecycle state.")
    health_score: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Overall health score from 0 to 100 when completed.",
    )
    grade: str | None = Field(
        default=None,
        description="Overall grade: excellent, good, warning, or critical.",
    )
    summary: dict[str, int] = Field(
        default_factory=dict,
        description="Aggregated finding counts by result category.",
    )
    check_items: list[HealthCheckItem] = Field(
        default_factory=list,
        description="Progress and result for each diagnosed configuration area.",
    )
    findings: list[HealthFindingGroup] = Field(
        default_factory=list,
        description="Detailed findings grouped by configuration area.",
    )
    failed_reason: str | None = Field(
        default=None, description="Public failure reason when the diagnosis failed."
    )
    duration_ms: int | None = Field(
        default=None,
        ge=0,
        description="Total diagnosis duration in milliseconds when available.",
    )
    created_at: datetime | None = Field(
        default=None, description="Creation timestamp of the diagnosis."
    )


class HealthCheckAccepted(BaseModel):
    """Acknowledgement for an asynchronously started health diagnosis."""

    bot_id: str = Field(description="Stable Bot identifier.")
    scan_id: int = Field(description="Diagnosis identifier used for polling.")
    status: Literal["scanning"] = Field(description="Initial diagnosis state.")


__all__ = [
    "BotHealth",
    "HealthCheckAccepted",
    "HealthCheckItem",
    "HealthDiagnosisStatus",
    "HealthFindingDetail",
    "HealthFindingGroup",
]
