"""Single source of supported Todo template and artifact-gate capabilities."""

from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.task.todos.template_models import (
    TodoParticipantRefSource,
    TodoStageKind,
)


@dataclass(frozen=True)
class TodoArtifactCapability:
    artifact_type: str
    artifact_state: str
    hard_gate_supported: bool


@dataclass(frozen=True)
class TodoTemplateFeatureCapabilities:
    branching: bool
    parallel_stages: bool
    late_binding: bool


@dataclass(frozen=True)
class TodoTemplateCapabilities:
    supported_schema_versions: tuple[str, ...]
    participant_reference_sources: tuple[TodoParticipantRefSource, ...]
    stage_kinds: tuple[TodoStageKind, ...]
    artifacts: tuple[TodoArtifactCapability, ...]
    features: TodoTemplateFeatureCapabilities


class TodoTemplateCapabilityRegistry:
    """Read-only registry shared by validation and capability projection."""

    def __init__(self, capabilities: TodoTemplateCapabilities) -> None:
        self._capabilities = capabilities
        self._hard_gates = frozenset(
            (item.artifact_type, item.artifact_state)
            for item in capabilities.artifacts
            if item.hard_gate_supported
        )

    @property
    def capabilities(self) -> TodoTemplateCapabilities:
        return self._capabilities

    def supports_schema_version(self, schema_version: str) -> bool:
        return schema_version in self._capabilities.supported_schema_versions

    def supports_hard_gate(self, artifact_type: str, artifact_state: str) -> bool:
        return (artifact_type, artifact_state) in self._hard_gates


TODO_TEMPLATE_CAPABILITIES = TodoTemplateCapabilities(
    supported_schema_versions=("1.0",),
    participant_reference_sources=(
        TodoParticipantRefSource.BINDING,
        TodoParticipantRefSource.FIXED,
        TodoParticipantRefSource.TODO_CREATOR,
    ),
    stage_kinds=(TodoStageKind.BOT, TodoStageKind.HUMAN),
    artifacts=(
        TodoArtifactCapability(
            artifact_type="pull_request",
            artifact_state="created",
            hard_gate_supported=True,
        ),
    ),
    features=TodoTemplateFeatureCapabilities(
        branching=False,
        parallel_stages=False,
        late_binding=False,
    ),
)

TODO_TEMPLATE_CAPABILITY_REGISTRY = TodoTemplateCapabilityRegistry(
    TODO_TEMPLATE_CAPABILITIES
)
