"""Bounded, display-only final model output; never prompts or hidden reasoning."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from avernet_evolution.redaction import redact_text

MAX_RAW_OUTPUT = 64_000


def output_artifact_name(attempt: int) -> str:
    return f"behavior-refinement-output-attempt-{attempt}.json"


class ProposedEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(max_length=1024)
    content: str = Field(max_length=24_000)
    changed: bool


class StrategyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    attempt: int = Field(ge=1)
    outcome: Literal["not_called", "no_changes", "changes_proposed", "invalid_proposal", "cancelled"]
    reason: str = Field(max_length=120)
    rationale: str = Field(default="", max_length=2000)
    edits: list[ProposedEdit] = Field(default_factory=list, max_length=20)
    raw_output: str = Field(default="", max_length=MAX_RAW_OUTPUT)
    raw_output_truncated: bool = False

    def display_bytes(self) -> bytes:
        # Redact strings individually: regexes must not corrupt the JSON envelope.
        raw = redact_text(self.raw_output)
        safe = self.model_copy(
            update={
                "rationale": redact_text(self.rationale)[:2000],
                "raw_output": raw[:MAX_RAW_OUTPUT],
                "raw_output_truncated": self.raw_output_truncated or len(raw) > MAX_RAW_OUTPUT,
                "edits": [
                    edit.model_copy(
                        update={
                            "path": redact_text(edit.path)[:1024],
                            "content": redact_text(edit.content)[:24_000],
                        }
                    )
                    for edit in self.edits
                ],
            }
        )
        return safe.model_dump_json().encode("utf-8")
