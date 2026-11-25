"""Service boundaries for combined Apply projection and ordinary delivery."""

from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
    RuntimeProjectionResult,
)
from agentclaw.community.core.skills_pool.mapping_intent import logical_skill_mappings_from_evidence
from agentclaw.community.core.skills_pool.models import PoolSkillMapping


CapabilityWriteScope = Callable[[], AbstractContextManager[None]]
PROJECTION_RETRY_FIELD = "capability_projection_retry"


@dataclass(frozen=True, slots=True)
class CapabilityProjectionRetry:
    """Private release evidence scoped to its physical device."""

    device_id: str
    retired_mappings: tuple[PoolSkillMapping, ...] = ()
    released_mcp: frozenset[str] = frozenset()


    def to_payload(self) -> dict[str, object]:
        return {
            "device_id": self.device_id,
            "retired_mappings": [mapping.to_dict() for mapping in self.retired_mappings],
            "released_mcp": sorted(self.released_mcp),
        }

    @classmethod
    def from_payload(cls, evidence: object) -> "CapabilityProjectionRetry | None":
        if evidence is None:
            return None
        if not isinstance(evidence, dict):
            raise ValueError("invalid capability retry evidence")
        device_id = evidence.get("device_id")
        mappings = evidence.get("retired_mappings")
        releases = evidence.get("released_mcp", [])
        if (
            not isinstance(device_id, str) or not device_id or not isinstance(mappings, list)
            or not isinstance(releases, list) or any(not isinstance(code, str) for code in releases)
        ):
            raise ValueError("invalid capability retry evidence")
        # Alternatives may share a target. Validate each identity and only
        # send one per target in a retirement request.
        retired = tuple(
            candidate for mapping in mappings
            for candidate in logical_skill_mappings_from_evidence({"retired_mappings": [mapping]})
        )
        return cls(device_id, retired, frozenset(releases))


class DeferredCapabilityProjection(Protocol):
    def write_scope(self) -> AbstractContextManager[None]: ...

    async def finish(
        self, *, initialize: bool,
    ) -> tuple[RuntimeProjectionResult, CapabilityProjectionRetry | None]: ...


class CapabilityProjectionReconcileProtocol(Protocol):
    @property
    def defer_enabled(self) -> bool: ...

    def deferred_apply(
        self, *, bot_id: str, entity_id: str,
        retry: CapabilityProjectionRetry | None,
    ) -> AbstractAsyncContextManager[DeferredCapabilityProjection]: ...


@runtime_checkable
class CapabilityReleaseRecorder(Protocol):
    async def remember_release_candidates(
        self, *, bot_id: str, entity_id: str, codes: frozenset[str],
        candidates: Sequence[PoolSkillMapping] = (),
        candidate_device_id: str | None = None,
    ) -> None: ...


class CapabilityProjectionCompetitionProtocol(CapabilityReleaseRecorder, Protocol):
    @property
    def defer_enabled(self) -> bool: ...

    async def project_competing_change(
        self,
        *,
        bot_id: str,
        owner_id: str,
        entity_id: str | None,
        scope: ProjectionScope,
        retired_mappings: Sequence[PoolSkillMapping],
        legacy: Callable[[], Awaitable[RuntimeProjectionResult]],
        mcp_and_cli_only: bool = False,
        desktop_recovery: bool = False,
    ) -> RuntimeProjectionResult: ...
