"""Noop Local Skill security provider for the public deployment profile."""

from __future__ import annotations

from typing import NoReturn

from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugin_api.local_skill_security_provider import (
    LocalSkillSecurityProviderCapabilities,
    LocalSkillSecurityProviderProtocol,
    SecurityArtifactAccessRequest,
    SecurityCheckReport,
    SecurityRemoteArtifact,
    SecuritySkillInfo,
)


@plugin_impl(mode=Mode.LOCAL, flavor=Flavor.NOOP, rationale="no security provider configured")
class UnavailableLocalSkillSecurityProvider(LocalSkillSecurityProviderProtocol):
    """Declare unavailable capabilities and fail closed if invoked."""

    @staticmethod
    def _unavailable() -> NoReturn:
        raise RuntimeError("Local Skill security provider is not configured")

    def capabilities(self) -> LocalSkillSecurityProviderCapabilities:
        return LocalSkillSecurityProviderCapabilities(
            accepts_unchecked_asset_metadata=False,
            ordered_latest_revision_sync=False,
        )

    def expose_artifact(
        self, request: SecurityArtifactAccessRequest
    ) -> SecurityRemoteArtifact:
        self._unavailable()

    def submit_scan(self, info: SecuritySkillInfo) -> SecurityCheckReport:
        self._unavailable()

    def get_scan_report(
        self, *, tenant: str, env: str, check_id: str
    ) -> SecurityCheckReport:
        self._unavailable()

    def sync_skill_info(self, info: SecuritySkillInfo) -> None:
        self._unavailable()


__all__ = ["UnavailableLocalSkillSecurityProvider"]
