"""HTTP DTOs for versioned Todo template management."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.core.task.todos.template_management_models import (
    TodoTemplateDetail,
    TodoTemplateRecord,
    TodoTemplateStatus,
    TodoTemplateVersionRecord,
    TodoTemplateVersionStatus,
)
from agentclaw.community.core.task.todos.template_models import (
    TodoCompletionGate,
    TodoParticipantDefinition,
    TodoParticipantReference,
    TodoParticipantRefSource,
    TodoParticipantRequiredAt,
    TodoParticipantSubjectType,
    TodoRequiredArtifactGate,
    TodoStageDefinition,
    TodoStageKind,
    TodoTemplateDefinition,
)


class _StrictSchema(BaseModel):
    """Reject fields not declared by the public template contract."""

    model_config = ConfigDict(extra="forbid")


class TodoParticipantDefinitionSchema(_StrictSchema):
    """One participant role a Todo creator must bind."""

    key: str = Field(
        min_length=1,
        max_length=64,
        description="Version-local stable participant role key.",
    )
    label: str = Field(
        min_length=1,
        max_length=200,
        description="Human-readable participant role label.",
    )
    subject_type: TodoParticipantSubjectType = Field(
        description="Identity type accepted for this participant role."
    )
    required_at: TodoParticipantRequiredAt = Field(
        description="Lifecycle point at which this participant must be bound."
    )

    def to_domain(self) -> TodoParticipantDefinition:
        return TodoParticipantDefinition(
            key=self.key,
            label=self.label,
            subject_type=self.subject_type,
            required_at=self.required_at,
        )

    @classmethod
    def from_domain(
        cls, value: TodoParticipantDefinition
    ) -> "TodoParticipantDefinitionSchema":
        return cls(
            key=value.key,
            label=value.label,
            subject_type=value.subject_type,
            required_at=value.required_at,
        )


class TodoParticipantReferenceSchema(_StrictSchema):
    """Reference to a bound, fixed, or Todo-creator participant."""

    source: TodoParticipantRefSource = Field(
        description="Discriminator controlling how the participant is resolved."
    )
    key: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Referenced participant role key when source is binding.",
    )
    subject_type: TodoParticipantSubjectType | None = Field(
        default=None,
        description="Fixed participant identity type when source is fixed.",
    )
    subject_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="Fixed participant identity when source is fixed.",
    )

    def to_domain(self) -> TodoParticipantReference:
        return TodoParticipantReference(
            source=self.source,
            key=self.key,
            subject_type=self.subject_type,
            subject_id=self.subject_id,
        )

    @classmethod
    def from_domain(
        cls, value: TodoParticipantReference
    ) -> "TodoParticipantReferenceSchema":
        return cls(
            source=value.source,
            key=value.key,
            subject_type=value.subject_type,
            subject_id=value.subject_id,
        )


class TodoRequiredArtifactGateSchema(_StrictSchema):
    """One deterministic semantic artifact fact required for completion."""

    artifact_type: str = Field(
        min_length=1,
        max_length=64,
        description="Registered semantic artifact type, for example pull_request.",
    )
    artifact_state: str = Field(
        min_length=1,
        max_length=64,
        description="Registered semantic artifact state, for example created.",
    )
    min_count: int = Field(
        default=1,
        ge=1,
        description="Minimum matching artifact facts required for completion.",
    )

    def to_domain(self) -> TodoRequiredArtifactGate:
        return TodoRequiredArtifactGate(
            artifact_type=self.artifact_type,
            artifact_state=self.artifact_state,
            min_count=self.min_count,
        )

    @classmethod
    def from_domain(
        cls, value: TodoRequiredArtifactGate
    ) -> "TodoRequiredArtifactGateSchema":
        return cls(
            artifact_type=value.artifact_type,
            artifact_state=value.artifact_state,
            min_count=value.min_count,
        )


class TodoCompletionGateSchema(_StrictSchema):
    """Deterministic hard gates applied before a stage can complete."""

    required_artifacts: list[TodoRequiredArtifactGateSchema] = Field(
        default_factory=list,
        description="Semantic artifact facts that must all be satisfied.",
    )

    def to_domain(self) -> TodoCompletionGate:
        return TodoCompletionGate(
            required_artifacts=tuple(
                item.to_domain() for item in self.required_artifacts
            )
        )

    @classmethod
    def from_domain(cls, value: TodoCompletionGate) -> "TodoCompletionGateSchema":
        return cls(
            required_artifacts=[
                TodoRequiredArtifactGateSchema.from_domain(item)
                for item in value.required_artifacts
            ]
        )


class TodoStageDefinitionSchema(_StrictSchema):
    """One ordered Bot or Human stage in a template definition."""

    key: str = Field(
        min_length=1,
        max_length=64,
        description="Version-local stable stage key.",
    )
    name: str = Field(
        min_length=1,
        max_length=200,
        description="Human-readable stage name.",
    )
    purpose: str = Field(
        min_length=1,
        max_length=4000,
        description="Reusable product-stage purpose, not the concrete Todo task.",
    )
    stage_kind: TodoStageKind = Field(
        description="Whether the stage is executed by a Bot or a Human."
    )
    executor_ref: TodoParticipantReferenceSchema = Field(
        description="Reference resolving the stage executor."
    )
    owner_ref: TodoParticipantReferenceSchema | None = Field(
        default=None,
        description=(
            "Human owner reference; required for Bot stages and defaults to the "
            "executor for Human stages."
        ),
    )
    completion_gate: TodoCompletionGateSchema = Field(
        default_factory=TodoCompletionGateSchema,
        description="Optional deterministic hard artifact gates.",
    )

    def to_domain(self) -> TodoStageDefinition:
        return TodoStageDefinition(
            key=self.key,
            name=self.name,
            purpose=self.purpose,
            stage_kind=self.stage_kind,
            executor_ref=self.executor_ref.to_domain(),
            owner_ref=self.owner_ref.to_domain()
            if self.owner_ref is not None
            else None,
            completion_gate=self.completion_gate.to_domain(),
        )

    @classmethod
    def from_domain(cls, value: TodoStageDefinition) -> "TodoStageDefinitionSchema":
        return cls(
            key=value.key,
            name=value.name,
            purpose=value.purpose,
            stage_kind=value.stage_kind,
            executor_ref=TodoParticipantReferenceSchema.from_domain(value.executor_ref),
            owner_ref=(
                TodoParticipantReferenceSchema.from_domain(value.owner_ref)
                if value.owner_ref is not None
                else None
            ),
            completion_gate=TodoCompletionGateSchema.from_domain(value.completion_gate),
        )


class TodoTemplateDefinitionRequest(_StrictSchema):
    """Complete Todo product-flow definition stored in one version."""

    schema_version: str = Field(
        min_length=1,
        max_length=32,
        description="Template definition schema version accepted by capabilities.",
    )
    name: str = Field(
        min_length=1,
        max_length=200,
        description="Template name shown in the Todo workbench.",
    )
    description: str = Field(
        max_length=4000,
        description="Reusable description of the product flow.",
    )
    binding_schema: list[TodoParticipantDefinitionSchema] = Field(
        description="Participant roles that must be resolved when a Todo is created."
    )
    stages: list[TodoStageDefinitionSchema] = Field(
        min_length=1,
        description="Ordered, linear product stages executed by the Todo.",
    )

    def to_domain(self) -> TodoTemplateDefinition:
        return TodoTemplateDefinition(
            schema_version=self.schema_version,
            name=self.name,
            description=self.description,
            binding_schema=tuple(item.to_domain() for item in self.binding_schema),
            stages=tuple(item.to_domain() for item in self.stages),
        )

    @classmethod
    def from_domain(
        cls, value: TodoTemplateDefinition
    ) -> "TodoTemplateDefinitionRequest":
        return cls(
            schema_version=value.schema_version,
            name=value.name,
            description=value.description,
            binding_schema=[
                TodoParticipantDefinitionSchema.from_domain(item)
                for item in value.binding_schema
            ],
            stages=[
                TodoStageDefinitionSchema.from_domain(item) for item in value.stages
            ],
        )


class CreateTodoTemplateRequest(_StrictSchema):
    """Create a stable template identity and its first editable draft."""

    space_id: int = Field(
        gt=0,
        description="Product Space that owns and authorizes the template.",
    )
    definition: TodoTemplateDefinitionRequest = Field(
        description="Validated initial draft definition."
    )


class UpdateTodoTemplateDraftRequest(_StrictSchema):
    """Replace the complete definition of one editable draft version."""

    definition: TodoTemplateDefinitionRequest = Field(
        description="Complete replacement definition for the draft version."
    )


class TodoTemplateRecordResponse(BaseModel):
    """Stable Todo template metadata within one Space."""

    template_id: str = Field(description="Stable Todo template identifier.")
    space_id: int = Field(description="Owning product Space identifier.")
    name: str = Field(description="Current template display name.")
    description: str = Field(description="Current template description.")
    status: TodoTemplateStatus = Field(description="Template lifecycle status.")
    published_version_id: str | None = Field(
        description="Current published version identifier, if any."
    )
    created_by: str = Field(description="Human who created the template.")
    updated_by: str = Field(description="Human who last changed template metadata.")
    created_at: datetime = Field(description="UTC creation timestamp.")
    updated_at: datetime = Field(description="UTC last-modified timestamp.")

    @classmethod
    def from_record(cls, value: TodoTemplateRecord) -> "TodoTemplateRecordResponse":
        return cls(**value.__dict__)


class TodoTemplateVersionResponse(BaseModel):
    """One draft or immutable published Todo template version."""

    template_version_id: str = Field(description="Stable template version identifier.")
    template_id: str = Field(description="Owning stable template identifier.")
    version_no: int = Field(description="Monotonic version number within the template.")
    status: TodoTemplateVersionStatus = Field(
        description="Whether this version is editable or published."
    )
    schema_version: str = Field(description="Definition schema version.")
    definition: TodoTemplateDefinitionRequest = Field(
        description="Complete versioned product-flow definition."
    )
    definition_hash: str = Field(
        description="SHA-256 digest of the canonical definition JSON."
    )
    created_by: str = Field(description="Human who created this version.")
    published_by: str | None = Field(
        description="Human who published this version, if published."
    )
    published_at: datetime | None = Field(
        description="UTC publication timestamp, if published."
    )
    created_at: datetime = Field(description="UTC creation timestamp.")
    updated_at: datetime = Field(description="UTC last-modified timestamp.")

    @classmethod
    def from_record(
        cls, value: TodoTemplateVersionRecord
    ) -> "TodoTemplateVersionResponse":
        return cls(
            template_version_id=value.template_version_id,
            template_id=value.template_id,
            version_no=value.version_no,
            status=value.status,
            schema_version=value.schema_version,
            definition=TodoTemplateDefinitionRequest.from_domain(value.definition),
            definition_hash=value.definition_hash,
            created_by=value.created_by,
            published_by=value.published_by,
            published_at=value.published_at,
            created_at=value.created_at,
            updated_at=value.updated_at,
        )


class TodoTemplateDetailResponse(BaseModel):
    """Template metadata together with its ordered version history."""

    template: TodoTemplateRecordResponse = Field(
        description="Stable template metadata."
    )
    versions: list[TodoTemplateVersionResponse] = Field(
        description="All versions ordered by ascending version number."
    )

    @classmethod
    def from_detail(cls, value: TodoTemplateDetail) -> "TodoTemplateDetailResponse":
        return cls(
            template=TodoTemplateRecordResponse.from_record(value.template),
            versions=[
                TodoTemplateVersionResponse.from_record(item) for item in value.versions
            ],
        )
