"""HTTP DTOs for Bot teams backed by existing Todo template storage."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentclaw.community.core.task.todos.bot_team_models import (
    BotTeamDefinition,
    BotTeamDetail,
    BotTeamNode,
    BotTeamRecord,
    BotTeamRevisionRecord,
    BotTeamStatus,
)



class _StrictSchema(BaseModel):
    """Reject fields outside the product-facing Bot team contract."""

    model_config = ConfigDict(extra="forbid")


class BotTeamNodeSchema(_StrictSchema):
    """One ordered Bot-owned node in a V1 linear Bot team."""

    code: str = Field(
        min_length=1,
        max_length=64,
        description="Revision-local stable node code.",
    )
    name: str = Field(
        min_length=1,
        max_length=200,
        description="Human-readable node name.",
    )
    bot_ids: list[str] = Field(
        min_length=1,
        max_length=20,
        description="Ordered Bots eligible to execute this node.",
    )
    approval_required: bool = Field(
        default=False,
        description="Whether the Todo owner must approve immediately after this node.",
    )
    completion_gate: str = Field(
        default="",
        max_length=4000,
        description="Free-text condition describing when this node is complete.",
    )

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_node_shape(cls, value):
        """Keep old single-Bot/structured-gate callers readable during rollout."""

        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        legacy_bot_id = normalized.pop("bot_id", None)
        if "bot_ids" not in normalized and legacy_bot_id is not None:
            normalized["bot_ids"] = [legacy_bot_id]
        gate = normalized.get("completion_gate")
        if isinstance(gate, dict):
            conditions: list[str] = []
            for artifact in gate.get("required_artifacts", []):
                if not isinstance(artifact, dict):
                    continue
                artifact_type = artifact.get("artifact_type", artifact.get("type", "artifact"))
                artifact_state = artifact.get("artifact_state", artifact.get("state", "created"))
                min_count = artifact.get("min_count", 1)
                conditions.append(
                    f"artifact {artifact_type} must reach state {artifact_state} "
                    f"with min_count {min_count}"
                )
            normalized["completion_gate"] = "; ".join(conditions)
        return normalized

    def to_domain(self) -> BotTeamNode:
        return BotTeamNode(
            code=self.code,
            name=self.name,
            bot_ids=tuple(self.bot_ids),
            approval_required=self.approval_required,
            completion_gate=self.completion_gate,
        )

    @classmethod
    def from_domain(cls, value: BotTeamNode) -> "BotTeamNodeSchema":
        return cls(
            code=value.code,
            name=value.name,
            bot_ids=list(value.bot_ids),
            approval_required=value.approval_required,
            completion_gate=value.completion_gate,
        )


class _BotTeamDefinitionRequest(_StrictSchema):
    """Shared product fields used to publish a Bot team revision."""

    name: str = Field(
        min_length=1,
        max_length=200,
        description="Bot team display name.",
    )
    description: str = Field(
        max_length=4000,
        description="Reusable description of the Bot team's responsibility.",
    )
    nodes: list[BotTeamNodeSchema] = Field(
        min_length=1,
        description="Ordered V1 linear workflow nodes.",
    )

    def to_definition(self) -> BotTeamDefinition:
        return BotTeamDefinition(
            name=self.name,
            description=self.description,
            nodes=tuple(node.to_domain() for node in self.nodes),
        )


class CreateBotTeamRequest(_BotTeamDefinitionRequest):
    """Create a Bot team and publish its first immutable revision."""

    space_id: int = Field(
        gt=0,
        description="Product Space that owns and authorizes the Bot team.",
    )


class UpdateBotTeamRequest(_BotTeamDefinitionRequest):
    """Publish a new immutable revision for an existing Bot team."""


class BotTeamRecordResponse(BaseModel):
    """Stable Bot team metadata within one Space."""

    bot_team_id: str = Field(description="Stable Bot team identifier.")
    space_id: int = Field(description="Owning product Space identifier.")
    name: str = Field(description="Current Bot team display name.")
    description: str = Field(description="Current Bot team description.")
    status: BotTeamStatus = Field(description="Bot team lifecycle status.")
    published_revision_id: str | None = Field(
        description="Current published revision identifier, if any."
    )
    created_by: str = Field(description="Human who created the Bot team.")
    updated_by: str = Field(description="Human who last changed the Bot team.")
    created_at: datetime = Field(description="UTC creation timestamp.")
    updated_at: datetime = Field(description="UTC last-modified timestamp.")

    @classmethod
    def from_record(cls, value: BotTeamRecord) -> "BotTeamRecordResponse":
        return cls(**value.__dict__)


class BotTeamRevisionResponse(BaseModel):
    """One published immutable Bot team revision."""

    bot_team_revision_id: str = Field(description="Stable revision identifier.")
    bot_team_id: str = Field(description="Owning stable Bot team identifier.")
    revision_no: int = Field(description="Monotonic revision number.")
    name: str = Field(description="Revision display name snapshot.")
    description: str = Field(description="Revision description snapshot.")
    nodes: list[BotTeamNodeSchema] = Field(
        description="Ordered linear nodes in this revision."
    )
    definition_hash: str = Field(description="Canonical definition SHA-256 digest.")
    product_profile_id: int | None = Field(
        description="Immutable ProductProfile bound to this revision."
    )
    created_by: str = Field(description="Human who created this revision.")
    published_by: str = Field(description="Human who published this revision.")
    published_at: datetime = Field(description="UTC publication timestamp.")
    created_at: datetime = Field(description="UTC creation timestamp.")
    updated_at: datetime = Field(description="UTC last-modified timestamp.")

    @classmethod
    def from_record(
        cls, value: BotTeamRevisionRecord
    ) -> "BotTeamRevisionResponse":
        return cls(
            bot_team_revision_id=value.bot_team_revision_id,
            bot_team_id=value.bot_team_id,
            revision_no=value.revision_no,
            name=value.name,
            description=value.description,
            nodes=[BotTeamNodeSchema.from_domain(node) for node in value.nodes],
            definition_hash=value.definition_hash,
            product_profile_id=value.product_profile_id,
            created_by=value.created_by,
            published_by=value.published_by,
            published_at=value.published_at,
            created_at=value.created_at,
            updated_at=value.updated_at,
        )


class BotTeamDetailResponse(BaseModel):
    """Bot team metadata together with published revision history."""

    bot_team: BotTeamRecordResponse = Field(description="Stable Bot team metadata.")
    revisions: list[BotTeamRevisionResponse] = Field(
        description="Published revisions ordered by ascending revision number."
    )

    @classmethod
    def from_detail(cls, value: BotTeamDetail) -> "BotTeamDetailResponse":
        return cls(
            bot_team=BotTeamRecordResponse.from_record(value.bot_team),
            revisions=[
                BotTeamRevisionResponse.from_record(revision)
                for revision in value.revisions
            ],
        )
