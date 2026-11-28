"""L4 bounded collaboration context; independent from work and method state.

Identity, scope and lifecycle remain independent of Work and upper-layer
composition. New records include description/audit metadata; metadata-less
representations remain readable without inventing their missing audit history.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from hashlib import sha256

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.legacy_artifact import (
    LegacyArtifact,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet_metadata import (
    COLLET_METADATA_KEYS,
    ColletMetadata,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.resource_binding import (
    ResourceBinding,
)


def _required_str(data: dict, key: str, owner: str) -> str:
    """从 dict 中取出必填字符串字段;缺失或空串抛校验错误。"""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"{owner}.{key} must be a non-empty string, got: {value!r}"
        )
    return value


def _required_dict(data: dict, key: str, owner: str) -> dict:
    """从 dict 中取出必填对象字段;缺失或非 dict 抛校验错误。"""
    value = data.get(key)
    if not isinstance(value, dict):
        raise ContextCollabValidationError(
            f"{owner}.{key} must be an object, got: {value!r}"
        )
    return value


class ColletState(str, Enum):
    """Collet 大写机器状态词汇(spec §4)。

    与独立 ``Task.status`` 的 ``TaskStatus`` 小写词汇是两套闭合
    词汇:前者服务 G4 ``collet_lifecycle`` 状态机(见
    ``g4_contracts_consistency.context_state_machines``),后者仍是
    独立 Task 的状态。两套词汇不互相渗透/schema 不互认。
    """

    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"


# Known fields are closed for both legacy and current representations.
_COLLET_KEYS = (
    frozenset(
        {
            "collet_id",
            "initiator_ref",
            "state",
            "scope",
            "version",
            "artifacts",
            "resources",
        }
    )
    | COLLET_METADATA_KEYS
    | {"etag"}
)
_COLLET_STATES = frozenset({state.value for state in ColletState})


@dataclass
class LegacyCollet:
    """L4 bounded context, not a Task or a product execution snapshot.

    scope is an optional legacy L3 governance anchor, NOT the union of
    accessible Spaces, Resources or Grants. version is the aggregate revision.
    Missing metadata denotes a legacy representation only; every repository
    creation writes complete metadata.
    The existing authenticated-actor initiator convention remains unchanged.
    artifacts contains L4 Artifact values and defaults to empty; runtime reporting
    and Task-to-Collet aggregation do not populate it yet. Empty artifacts are
    omitted from the representation to preserve existing shapes and ETags.
    resources contains L4 ResourceBinding values, not resource bodies or Grants.
    It defaults to empty and is omitted when empty. Explicit Collet creation can
    supply it; existing event-based binding facts do not populate it automatically.
    """

    collet_id: str
    initiator_ref: PrincipalRef
    state: ColletState
    scope: SpaceRef | None
    version: int
    metadata: ColletMetadata | None = None
    artifacts: tuple[LegacyArtifact, ...] = ()
    resources: tuple[ResourceBinding, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.artifacts, tuple) or any(
            not isinstance(artifact, LegacyArtifact) for artifact in self.artifacts
        ):
            raise ContextCollabValidationError(
                "Collet.artifacts must be a tuple of L4 Artifact values"
            )
        if not isinstance(self.resources, tuple) or any(
            not isinstance(resource, ResourceBinding) for resource in self.resources
        ):
            raise ContextCollabValidationError(
                "Collet.resources must be a tuple of L4 ResourceBinding values"
            )

    @property
    def revision(self) -> int:
        """Common-contract spelling; version remains the compatible wire field."""
        return self.version

    @property
    def title(self) -> str | None:
        return self.metadata.title if self.metadata else None

    @property
    def description(self) -> str | None:
        return self.metadata.description if self.metadata else None

    @property
    def created_at(self) -> datetime | None:
        return self.metadata.created_at if self.metadata else None

    @property
    def created_by(self) -> PrincipalRef | None:
        return self.metadata.created_by if self.metadata else None

    @property
    def updated_at(self) -> datetime | None:
        return self.metadata.updated_at if self.metadata else None

    @property
    def updated_by(self) -> PrincipalRef | None:
        return self.metadata.updated_by if self.metadata else None

    @property
    def etag(self) -> str:
        """Derived strong tag for this representation, not another stored revision."""
        canonical = json.dumps(
            self._representation(),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return '"' + sha256(canonical.encode("utf-8")).hexdigest() + '"'

    @classmethod
    def from_dict(cls, data: dict) -> "LegacyCollet":
        """Decode legacy or complete current metadata; reject partial/unknown fields."""
        if not isinstance(data, dict):
            raise ContextCollabValidationError(
                f"LegacyCollet must be an object, got: {data!r}"
            )
        unknown = set(data) - _COLLET_KEYS
        if unknown:
            raise ContextCollabValidationError(
                f"LegacyCollet has unknown top-level keys: {sorted(unknown)}; "
                f"allowed keys: {sorted(_COLLET_KEYS)}"
            )
        raw_state = data.get("state")
        if not isinstance(raw_state, str) or raw_state not in _COLLET_STATES:
            raise ContextCollabValidationError(f"unknown state value: {raw_state!r}")
        raw_version = data.get("version")
        if (
            isinstance(raw_version, bool)
            or not isinstance(raw_version, int)
            or raw_version < 1
        ):
            raise ContextCollabValidationError(
                f"LegacyCollet.version must be an integer >= 1, got: {raw_version!r}"
            )
        metadata_fields = set(data) & COLLET_METADATA_KEYS
        metadata = (
            ColletMetadata.from_dict({key: data[key] for key in metadata_fields})
            if metadata_fields
            else None
        )
        if "etag" in data and metadata is None:
            raise ContextCollabValidationError("Collet.etag requires complete metadata")
        artifacts = data.get("artifacts", [])
        if not isinstance(artifacts, list) or any(
            not isinstance(artifact, dict) for artifact in artifacts
        ):
            raise ContextCollabValidationError(
                "Collet artifacts must be a list of objects"
            )
        resources = data.get("resources", [])
        if not isinstance(resources, list) or any(
            not isinstance(resource, dict) for resource in resources
        ):
            raise ContextCollabValidationError(
                "Collet resources must be a list of objects"
            )
        collet = cls(
            collet_id=_required_str(data, "collet_id", "Collet"),
            initiator_ref=PrincipalRef.from_dict(
                _required_dict(data, "initiator_ref", "Collet")
            ),
            state=ColletState(raw_state),
            scope=(
                SpaceRef.from_dict(data["scope"])
                if data.get("scope") is not None
                else None
            ),
            version=raw_version,
            metadata=metadata,
            artifacts=tuple(
                LegacyArtifact.from_dict(artifact) for artifact in artifacts
            ),
            resources=tuple(
                ResourceBinding.from_dict(resource) for resource in resources
            ),
        )
        if "etag" in data and data["etag"] != collet.etag:
            raise ContextCollabValidationError(
                "Collet.etag does not match representation"
            )
        return collet

    def _representation(self) -> dict:
        return {
            "collet_id": self.collet_id,
            "initiator_ref": self.initiator_ref.to_dict(),
            "state": self.state.value,
            "scope": self.scope.to_dict() if self.scope is not None else None,
            "version": self.version,
            **(
                {"artifacts": [artifact.to_dict() for artifact in self.artifacts]}
                if self.artifacts
                else {}
            ),
            **(
                {"resources": [resource.to_dict() for resource in self.resources]}
                if self.resources
                else {}
            ),
            **(self.metadata.to_dict() if self.metadata is not None else {}),
        }

    def to_dict(self) -> dict:
        data = self._representation()
        # Old representations preserve their exact shape; no fabricated audit.
        if self.metadata is not None:
            data["etag"] = self.etag
        return data
