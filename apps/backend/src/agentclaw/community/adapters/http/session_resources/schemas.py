"""HTTP schemas for session resources."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator


class UploadIntentFile(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int | None = Field(default=None, ge=0)
    content_hash: str | None = Field(default=None, max_length=128)


class UploadIntentRequest(BaseModel):
    bot_id: str
    session_key: str
    scope_type: str
    engine_type: str
    target_entity_id: str | None = Field(default=None, max_length=128)
    binding_id: int | None = Field(default=None, ge=1)
    files: list[UploadIntentFile] = Field(min_length=1, max_length=20)

    @field_validator("target_entity_id")
    @classmethod
    def normalize_target_entity_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("binding_id", mode="before")
    @classmethod
    def reject_boolean_binding_id(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("binding_id must be a positive integer")
        return value


class UploadCompleteRequest(BaseModel):
    bot_id: str
    session_key: str
    resource_id: str
    transfer_id: str


class ReferenceRequest(BaseModel):
    bot_id: str
    session_key: str
    insert_id: str


class MaterializedCallbackRequest(BaseModel):
    transfer_id: str
    task_id: str
    task_version: int = Field(ge=1)
    ready: bool
    canonical_bot_absolute_path: str | None = None
    relative_path: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    content_hash: str | None = None
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_result(self) -> "MaterializedCallbackRequest":
        if self.ready and (
            not self.canonical_bot_absolute_path
            or not self.relative_path
            or self.size_bytes is None
            or not self.content_hash
        ):
            raise ValueError("ready callback requires complete materialized metadata")
        if not self.ready and not self.error_code:
            raise ValueError("failed callback requires error_code")
        return self
