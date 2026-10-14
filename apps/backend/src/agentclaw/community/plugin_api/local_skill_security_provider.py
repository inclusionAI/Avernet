"""Version 1 Plugin API: Core calls the Local Skill security provider.

The provider owns transport authentication and temporary access to the frozen
snapshot. The consumer supplies a verified immutable object reference, never
mutable runtime paths, ZIP bytes, or credentials.
No adapter may be enabled until it proves support for unchecked-asset metadata
sync and ordered latest-revision updates. These are capabilities to verify with
the vendor, not guarantees inferred from a successful HTTP response.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


CONTRACT_VERSION = 1


@dataclass(frozen=True)
class LocalSkillSecurityProviderCapabilities:
    accepts_unchecked_asset_metadata: bool
    ordered_latest_revision_sync: bool


@dataclass(frozen=True)
class SecurityArtifactAccessRequest:
    tenant: str
    env: str
    provider_skill_id: str
    observation_id: str
    version: str
    sha256: str
    snapshot_ref: str


@dataclass(frozen=True)
class SecurityRemoteArtifact:
    format: str
    download_url: str
    checksum: str
    hash_alg: str


@dataclass(frozen=True)
class SecuritySkillInfo:
    tenant: str
    env: str
    provider_skill_id: str
    asset_skill_id: int | None
    observation_id: str
    snapshot_ref: str
    upload_order: int
    name: str
    description: str
    version: str
    owner_id: str
    creator_id: str | None
    modifier_id: str
    status: str
    platform_del_flag: int
    artifact: SecurityRemoteArtifact | None


@dataclass(frozen=True)
class SecurityCheckReport:
    check_id: str
    risk_status: str
    raw_report: dict[str, Any]
    detail_url: str | None


class SecuritySubmissionUnknown(Exception):
    """The submit request may have been accepted, but its ID was not received."""


class SecuritySubmissionRejected(Exception):
    """The provider definitively rejected the request before starting a check."""


@runtime_checkable
class LocalSkillSecurityProviderProtocol(Plugin, Protocol):
    def capabilities(self) -> LocalSkillSecurityProviderCapabilities:
        """Declare vendor-confirmed V1 abilities needed by observation mode."""
        ...

    def expose_artifact(
        self, request: SecurityArtifactAccessRequest
    ) -> SecurityRemoteArtifact:
        """Expose the verified immutable snapshot without copying or overwriting it.

        The returned URL can be short lived. ``SecuritySkillInfo.snapshot_ref``
        lets the provider issue a fresh URL for later scan or metadata calls.
        """
        ...

    def submit_scan(self, info: SecuritySkillInfo) -> SecurityCheckReport:
        """Submit once; raise SecuritySubmissionUnknown on an ambiguous result."""
        ...

    def get_scan_report(
        self, *, tenant: str, env: str, check_id: str
    ) -> SecurityCheckReport:
        """Fetch the report for one known provider check ID."""
        ...

    def sync_skill_info(self, info: SecuritySkillInfo) -> None:
        """Synchronize a formal asset even before scan PASS or after REJECT.

        For a given ``provider_skill_id``, a lower ``upload_order`` must never
        overwrite newer metadata. This requires a provider-side compare or
        serialized ordered delivery proven by the enterprise adapter. A local
        pre-call current check alone does not establish this guarantee.
        """
        ...


__all__ = [
    "LocalSkillSecurityProviderProtocol",
    "LocalSkillSecurityProviderCapabilities",
    "CONTRACT_VERSION",
    "SecurityArtifactAccessRequest",
    "SecurityCheckReport",
    "SecurityRemoteArtifact",
    "SecuritySkillInfo",
    "SecuritySubmissionUnknown",
    "SecuritySubmissionRejected",
]
