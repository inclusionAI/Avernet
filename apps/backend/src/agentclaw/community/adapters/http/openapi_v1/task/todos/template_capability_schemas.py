"""HTTP response schemas for Todo template capability discovery."""

from __future__ import annotations

from pydantic import BaseModel, Field

from agentclaw.community.core.task.todos.template_capabilities import (
    TodoTemplateCapabilities,
)


class TodoTemplateArtifactCapabilityResponse(BaseModel):
    """One normalized semantic artifact fact exposed to template editors."""

    artifact_type: str = Field(description="Normalized semantic artifact type.")
    artifact_state: str = Field(description="Normalized semantic artifact state.")
    hard_gate_supported: bool = Field(
        description="Whether this artifact fact may be a deterministic hard gate."
    )


class TodoTemplateFeatureCapabilitiesResponse(BaseModel):
    """V1 template graph and participant-binding feature switches."""

    branching: bool = Field(
        description="Whether conditional stage branching is supported."
    )
    parallel_stages: bool = Field(
        description="Whether multiple stages may execute in parallel."
    )
    late_binding: bool = Field(
        description="Whether required participants may be bound after Todo creation."
    )


class TodoTemplateCapabilitiesResponse(BaseModel):
    """Complete Todo template definition capability contract for this server."""

    supported_schema_versions: list[str] = Field(
        description="Template definition schema versions accepted for validation."
    )
    participant_reference_sources: list[str] = Field(
        description="Supported executor and owner reference source discriminators."
    )
    stage_kinds: list[str] = Field(
        description="User-visible product stage kinds supported by the platform."
    )
    artifacts: list[TodoTemplateArtifactCapabilityResponse] = Field(
        description="Registered semantic artifact facts and their gate support."
    )
    features: TodoTemplateFeatureCapabilitiesResponse = Field(
        description="V1 graph and participant-binding feature switches."
    )

    @classmethod
    def from_capabilities(
        cls,
        capabilities: TodoTemplateCapabilities,
    ) -> "TodoTemplateCapabilitiesResponse":
        return cls(
            supported_schema_versions=list(capabilities.supported_schema_versions),
            participant_reference_sources=[
                source.value for source in capabilities.participant_reference_sources
            ],
            stage_kinds=[kind.value for kind in capabilities.stage_kinds],
            artifacts=[
                TodoTemplateArtifactCapabilityResponse(
                    artifact_type=item.artifact_type,
                    artifact_state=item.artifact_state,
                    hard_gate_supported=item.hard_gate_supported,
                )
                for item in capabilities.artifacts
            ],
            features=TodoTemplateFeatureCapabilitiesResponse(
                branching=capabilities.features.branching,
                parallel_stages=capabilities.features.parallel_stages,
                late_binding=capabilities.features.late_binding,
            ),
        )
