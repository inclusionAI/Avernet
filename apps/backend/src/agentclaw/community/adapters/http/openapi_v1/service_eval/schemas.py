"""Request schemas for the public service-Bot evaluation API."""

from __future__ import annotations

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
