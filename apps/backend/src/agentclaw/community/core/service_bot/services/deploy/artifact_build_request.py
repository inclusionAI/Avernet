"""Typed input and observed runtime layout for one artifact build.

The runtime probe owns discovery of engine-specific filesystem paths.  Service
artifact producers consume the normalized observation below instead of reading
stale Skills-Pool state or interpreting the probe transport payload themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping

from agentclaw.community.core.config_compose.models import ComposeOccasion
from agentclaw.community.core.skill_center.runtime_layout_probe_service_protocol import (
    RuntimeLayoutProbeResult,
    RuntimeLayoutProbeStatus,
)
from agentclaw.community.core.skill_center.services.runtime_layout_probe import (
    LAYOUT_CONTRACT_VERSION,
)


class ServiceArtifactBuildErrorCode(str, Enum):
    """Stable internal classifications for Service Artifact build failures."""

    LAYOUT_EVIDENCE_UNAVAILABLE = "SERVICE_ARTIFACT_LAYOUT_EVIDENCE_UNAVAILABLE"
    LAYOUT_EVIDENCE_INVALID = "SERVICE_ARTIFACT_LAYOUT_EVIDENCE_INVALID"
    CENTER_STORE_NOT_READY = "SERVICE_ARTIFACT_CENTER_STORE_NOT_READY"
    SNAPSHOT_INVALID = "SERVICE_ARTIFACT_SNAPSHOT_INVALID"
    CAPABILITY_CHANGED = "SERVICE_ARTIFACT_CAPABILITY_CHANGED"


class ServiceArtifactBuildError(RuntimeError):
    """Safe classified failure persisted by the publication build stage."""

    def __init__(
        self,
        code: ServiceArtifactBuildErrorCode,
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ServiceArtifactResolvedLayout:
    """Canonical filesystem roots observed from the current Engine runtime."""

    engine: str
    layout_contract_version: str
    active_root: str
    local_root: str
    repo_root: str
    center_root: str


@dataclass(frozen=True, slots=True)
class ServiceArtifactLayoutObservation:
    """Normalized, build-local result of the current runtime layout probe."""

    status: RuntimeLayoutProbeStatus
    engine: str
    layout_contract_version: str
    resolved_layout: ServiceArtifactResolvedLayout | None
    supported_mapping_contract_versions: frozenset[str]
    center_mount_status: str | None
    reason: str | None

    @classmethod
    def from_probe(
        cls,
        probe: RuntimeLayoutProbeResult,
        *,
        expected_engine: str,
    ) -> ServiceArtifactLayoutObservation:
        """Parse one probe without leaking its transport-shaped evidence onward.

        Non-READY outcomes remain observations: a Legacy artifact with no Center
        Skills may legally fall back to its historical static build plan.  A
        malformed READY outcome is normalized to INVALID so the producer can
        apply that same requirement matrix after it captures the active assets.
        """

        evidence = probe.evidence if isinstance(probe.evidence, dict) else {}
        reason = evidence.get("reason")
        normalized_reason = reason if isinstance(reason, str) and reason else None
        if (
            probe.status is not RuntimeLayoutProbeStatus.READY
            or probe.engine != expected_engine
            or probe.layout_contract_version != LAYOUT_CONTRACT_VERSION
        ):
            status = probe.status
            if (
                probe.status is RuntimeLayoutProbeStatus.READY
                or probe.engine != expected_engine
                or probe.layout_contract_version != LAYOUT_CONTRACT_VERSION
            ):
                status = RuntimeLayoutProbeStatus.INVALID
                normalized_reason = "runtime_layout_probe_identity_mismatch"
            return cls(
                status=status,
                engine=expected_engine,
                layout_contract_version=LAYOUT_CONTRACT_VERSION,
                resolved_layout=None,
                supported_mapping_contract_versions=frozenset(),
                center_mount_status=None,
                reason=normalized_reason,
            )

        try:
            resolved = cls._parse_resolved_layout(
                evidence.get("resolved_layout"), expected_engine=expected_engine
            )
            supported = cls._parse_supported_mapping_contracts(evidence)
            center_mount_status = cls._parse_center_mount_status(evidence)
        except (TypeError, ValueError):
            return cls(
                status=RuntimeLayoutProbeStatus.INVALID,
                engine=expected_engine,
                layout_contract_version=LAYOUT_CONTRACT_VERSION,
                resolved_layout=None,
                supported_mapping_contract_versions=frozenset(),
                center_mount_status=None,
                reason="invalid_runtime_layout_probe_evidence",
            )

        return cls(
            status=RuntimeLayoutProbeStatus.READY,
            engine=expected_engine,
            layout_contract_version=LAYOUT_CONTRACT_VERSION,
            resolved_layout=resolved,
            supported_mapping_contract_versions=supported,
            center_mount_status=center_mount_status,
            reason=None,
        )

    @staticmethod
    def _parse_resolved_layout(
        value: object,
        *,
        expected_engine: str,
    ) -> ServiceArtifactResolvedLayout:
        required = {
            "engine",
            "layout_contract_version",
            "active_root",
            "local_root",
            "repo_root",
            "pool_center",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise ValueError("invalid resolved layout")
        if (
            value.get("engine") != expected_engine
            or value.get("layout_contract_version") != LAYOUT_CONTRACT_VERSION
        ):
            raise ValueError("resolved layout identity mismatch")

        paths: dict[str, PurePosixPath] = {}
        for field in ("active_root", "local_root", "repo_root", "pool_center"):
            raw = value.get(field)
            if not isinstance(raw, str) or not raw:
                raise ValueError("missing resolved layout path")
            path = PurePosixPath(raw)
            if (
                not path.is_absolute()
                or path.as_posix() != raw
                or raw == "/"
                or raw.startswith("//")
                or any(part in {"", ".", ".."} for part in path.parts)
            ):
                raise ValueError("non-canonical resolved layout path")
            paths[field] = path

        local = paths["local_root"]
        repo = paths["repo_root"]
        center = paths["pool_center"]
        if (
            local.parent != repo.parent
            or repo.parent != center.parent
            or local.name != "skills-local"
            or repo.name != "skills-repo"
            or center.name != "skill-center"
        ):
            raise ValueError("invalid shared Skills-Pool roots")
        active = paths["active_root"]
        for corpus in (local, repo, center):
            try:
                active.relative_to(corpus)
            except ValueError:
                continue
            raise ValueError("active root cannot be inside a shared corpus")

        return ServiceArtifactResolvedLayout(
            engine=expected_engine,
            layout_contract_version=LAYOUT_CONTRACT_VERSION,
            active_root=active.as_posix(),
            local_root=local.as_posix(),
            repo_root=repo.as_posix(),
            center_root=center.as_posix(),
        )

    @staticmethod
    def _parse_supported_mapping_contracts(
        evidence: dict[str, Any],
    ) -> frozenset[str]:
        result: set[str] = set()
        legacy = evidence.get("mapping_contract_version")
        if isinstance(legacy, str) and legacy:
            result.add(legacy)
        supported = evidence.get("supported_mapping_contract_versions")
        if supported is not None:
            if not isinstance(supported, list) or any(
                not isinstance(item, str) or not item for item in supported
            ):
                raise ValueError("invalid mapping contracts")
            result.update(supported)
        return frozenset(result)

    @staticmethod
    def _parse_center_mount_status(evidence: dict[str, Any]) -> str | None:
        center_mount = evidence.get("center_mount")
        if center_mount is None:
            return None
        if not isinstance(center_mount, dict):
            raise ValueError("invalid Center mount evidence")
        status = center_mount.get("status")
        if status not in {"READY", "NOT_READY", "UNAVAILABLE"}:
            raise ValueError("invalid Center mount status")
        return str(status)


@dataclass(frozen=True, slots=True)
class ArtifactBuildRequest:
    """One immutable artifact-build command shared by all producer strategies."""

    bot: Mapping[str, Any]
    version: int | None
    layout_observation: ServiceArtifactLayoutObservation | None = None
    compose_occasion: ComposeOccasion = ComposeOccasion.RUNTIME
    """What a compose-backed build is for (W8): a publish build is a runtime
    compose; eager teclaw provisioning names ``PROVISION`` so the first
    artifact of a bot that carries a manifest says the platform owns every
    category. Snapshot producers ignore it."""

    @classmethod
    def create(
        cls,
        *,
        bot: Mapping[str, Any],
        version: int | None,
        layout_observation: ServiceArtifactLayoutObservation | None = None,
        compose_occasion: ComposeOccasion = ComposeOccasion.RUNTIME,
    ) -> ArtifactBuildRequest:
        return cls(
            bot=MappingProxyType(dict(bot)),
            version=version,
            layout_observation=layout_observation,
            compose_occasion=compose_occasion,
        )


__all__ = [
    "ArtifactBuildRequest",
    "ServiceArtifactBuildError",
    "ServiceArtifactBuildErrorCode",
    "ServiceArtifactLayoutObservation",
    "ServiceArtifactResolvedLayout",
]
