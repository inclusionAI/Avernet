"""Request schemas for the public service-Bot evaluation API."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import UploadFile
from pydantic import BaseModel, ConfigDict, Field


class ServiceBotEvalSubmissionForm(BaseModel):
    """The evaluation sample and report files submitted for certification."""

    model_config = ConfigDict(
        json_schema_extra={
            "description": (
                "The sample files and result reports submitted for one "
                "service-Bot version's simple certification."
            ),
            "example": {
                "sample_files": ["samples.jsonl"],
                "report_files": ["report.json"],
                "trigger_source": "manual_simple_cert",
            },
        }
    )

    sample_files: list[UploadFile] = Field(
        description=(
            "One or more evaluation sample files. At least one is required; "
            "the two file groups are limited to 100 files, 64 MiB per file, "
            "and 128 MiB in total."
        )
    )
    report_files: list[UploadFile] = Field(
        description=(
            "One or more evaluation result report files. At least one is "
            "required and the same group size limits apply."
        )
    )
    trigger_source: str = Field(
        default="manual_simple_cert",
        min_length=1,
        max_length=64,
        description="The caller's business trigger source for this submission.",
    )


class ServiceBotStandardEvalResultForm(BaseModel):
    """A finished standard-evaluation result to be persisted into the gate."""

    task_id: int = Field(description="QualityTask database ID")
    task_uuid: str = Field(min_length=1, max_length=128, description="QualityTask UUID")
    task_status: Literal["success", "failed"] = Field(description="QualityTask terminal status")
    gate_status: Literal["PASSED", "BLOCKED"] = Field(
        description="Normalized final gate conclusion from the standard evaluation"
    )
    trigger_source: str = Field(
        default="manual_restart_standard_eval",
        min_length=1,
        max_length=64,
        description="The business trigger source for this standard evaluation",
    )
    sample_set_uuid: str = Field(default="", max_length=128, description="MASA sample set UUID")
    sample_set_name: str = Field(default="", max_length=256, description="MASA sample set name")
    sample_count: int = Field(default=0, ge=0, description="Number of samples in the selected set")
    sample_set_url: str = Field(default="", max_length=1024, description="MASA sample set download URL")
    report_url: str = Field(default="", max_length=1024, description="MASA evaluation report URL")
    error_code: str = Field(default="", max_length=64, description="Standard evaluation error code")
    error_message: str = Field(default="", max_length=1024, description="Standard evaluation error message")
    progress: dict[str, Any] = Field(
        default_factory=dict,
        description="The final MASA progress payload, including executed and total counts",
    )


class ServiceBotStandardEvalTaskForm(BaseModel):
    """Create one server-driven standard evaluation task."""

    set_uuid: str = Field(min_length=1, max_length=128, description="MASA sample set UUID")
    goal_type: Literal["json", "semantic"] = Field(
        default="json", description="The evaluation-goal input format reserved for later use"
    )
    goal_json: dict[str, Any] | None = Field(
        default=None, description="Structured evaluation goal; reserved and not consumed now"
    )
    goal_text: str = Field(
        default="", max_length=4096, description="Natural-language evaluation goal; reserved and not consumed now"
    )
    trigger_source: str = Field(
        default="manual_restart_eval",
        min_length=1,
        max_length=64,
        description="The business trigger source for this standard evaluation",
    )


class ServiceBotStandardEvalEnvironmentCreateForm(BaseModel):
    """Create or prepare an isolated evaluation environment."""

    default_tag: str = Field(
        default="eval",
        min_length=1,
        max_length=32,
        description="Eval-zone service Bot tag used to isolate this evaluation environment",
    )
    ttl_minutes: int = Field(
        default=120,
        ge=1,
        le=1440,
        description="Automatic expiry window in minutes for the isolated evaluation environment",
    )
