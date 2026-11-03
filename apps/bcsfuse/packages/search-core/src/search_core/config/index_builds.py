"""Immutable index generation configuration; independent of any business source."""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IndexBuildConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    build_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    embedding_model: str = Field(min_length=1, max_length=128)
    embedding_dimension: int = Field(strict=True, ge=1, le=8192)
    conversion_version: str = Field(min_length=1, max_length=256)
    filter_fields: tuple[str, ...] = ()

    @field_validator("filter_fields")
    @classmethod
    def canonical_filters(cls, value):
        for name in value:
            if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*", name) or name in {
                "index_key",
                "source_id",
                "source_version",
                "search_text",
                "rerank_text",
            }:
                raise ValueError("unsupported filter field")
        return tuple(sorted(set(value)))

    @field_validator("embedding_model")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("model must not be blank")
        return value.strip()

    @property
    def embedding_key(self):
        return f"{self.embedding_model}/{self.embedding_dimension}"

    @property
    def index_key(self):
        return self.build_id


class IndexVersionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    active_index: str = Field(
        default="", pattern=r"^(?:[A-Za-z0-9][A-Za-z0-9_.-]{0,63})?$"
    )
