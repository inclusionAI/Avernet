"""Separate normal retrieval defaults from emergency permissions."""

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class RetrievalConfig(BaseModel):
    """Host startup values supply omitted fields before validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    keyword_enabled: StrictBool
    vector_enabled: StrictBool
    rerank_enabled: StrictBool
    empty_result_fallback_enabled: StrictBool
    max_inflight_requests: int = Field(strict=True, ge=1, le=4096)
    max_results: int = Field(strict=True, ge=1, le=100)
    keyword_timeout_ms: float = Field(strict=True, gt=0, le=4000, allow_inf_nan=False)
    embedding_timeout_ms: float = Field(strict=True, gt=0, le=4000, allow_inf_nan=False)
    reranker_timeout_ms: float = Field(strict=True, gt=0, le=4000, allow_inf_nan=False)
    card_metadata_timeout_ms: float = Field(
        strict=True, gt=0, le=4000, allow_inf_nan=False
    )
    embedding_singleflight_enabled: StrictBool


class EmergencySwitches(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    keyword_enabled: StrictBool = True
    vector_enabled: StrictBool = True
    rerank_enabled: StrictBool = True


class EmergencyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    retrieval: EmergencySwitches = Field(default_factory=EmergencySwitches)
