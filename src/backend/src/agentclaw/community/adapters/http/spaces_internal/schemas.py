"""Schemas for internal Space maintenance endpoints."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agentclaw.community.core.spaces.models import SpaceScTeamRepairStatus


class PersonalSpaceBatchQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: list[str] = Field(min_length=1)

    @field_validator("user_id")
    @classmethod
    def normalize_user_ids(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for user_id in values:
            value = user_id.strip()
            if not value:
                raise ValueError("user_id must not contain blank values")
            if value not in seen:
                seen.add(value)
                normalized.append(value)
        if len(normalized) > 500:
            raise ValueError("user_id must contain at most 500 unique values")
        return normalized


class PersonalSpaceBatchQueryItem(BaseModel):
    user_id: str
    space_id: int | None
    found: bool


class PersonalSpaceBatchQueryResult(BaseModel):
    list: list[PersonalSpaceBatchQueryItem]


class SpaceScTeamRepairResultResponse(BaseModel):
    """Result of one idempotent historical SC Team binding repair."""

    space_id: int
    status: SpaceScTeamRepairStatus
    sc_team_id: str
