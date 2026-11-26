"""Named immutable-resource evidence values, shared without importing L5/L6."""

from dataclasses import dataclass
from collections.abc import Iterable
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.artifact import (
    Artifact,
)


@dataclass(frozen=True)
class ArtifactPortBinding:
    name: str
    artifacts: tuple[Artifact, ...]

    def __post_init__(self) -> None:
        if (
            type(self.name) is not str
            or not self.name.strip()
            or type(self.artifacts) is not tuple
            or not 1 <= len(self.artifacts) <= 64
        ):
            raise ContextCollabValidationError("invalid artifact port binding")
        for artifact in self.artifacts:
            if not isinstance(artifact, Artifact):
                raise ContextCollabValidationError("port requires typed artifacts")
            Artifact.from_dict(artifact.to_dict())
            resource = artifact.resource_binding.resource_ref
            if not resource.revision or resource.revision.lower() in {
                "latest",
                "head",
                "main",
                "master",
            }:
                raise ContextCollabValidationError(
                    "workflow artifacts require exact immutable resource revisions"
                )

    def to_dict(self) -> dict:
        return {"name": self.name, "artifacts": [a.to_dict() for a in self.artifacts]}

    @classmethod
    def from_dict(cls, data: dict) -> "ArtifactPortBinding":
        if (
            type(data) is not dict
            or set(data) != {"name", "artifacts"}
            or type(data["artifacts"]) is not list
        ):
            raise ContextCollabValidationError("invalid artifact port fields")
        try:
            result = cls(
                data["name"], tuple(Artifact.from_dict(a) for a in data["artifacts"])
            )
            if result.to_dict() != data:
                raise ContextCollabValidationError("noncanonical artifact manifest")
            return result
        except (TypeError, AttributeError) as exc:
            raise ContextCollabValidationError("invalid artifact manifest") from exc


def bindings_from_dict(data: list) -> tuple[ArtifactPortBinding, ...]:
    if type(data) is not list or len(data) > 64:
        raise ContextCollabValidationError("invalid named artifact bindings")
    result = tuple(ArtifactPortBinding.from_dict(item) for item in data)
    if len({p.name for p in result}) != len(result):
        raise ContextCollabValidationError("duplicate artifact ports")
    return result


def artifact_refs(bindings: Iterable[ArtifactPortBinding]) -> tuple[str, ...]:
    """Ordered deduplicated projection (input aliases may reference one artifact)."""
    return tuple(dict.fromkeys(a.artifact_id for b in bindings for a in b.artifacts))


def manifest_envelope(
    stage_ref: str, allocation_ref: str, bindings: tuple[ArtifactPortBinding, ...]
) -> dict:
    return {
        "schema_version": 1,
        "stage_ref": stage_ref,
        "allocation_ref": allocation_ref,
        "outputs": [b.to_dict() for b in bindings],
    }
