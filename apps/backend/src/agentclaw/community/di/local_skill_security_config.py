"""Deployment opt-in for post-upload Local Skill security observation."""

from dataclasses import dataclass

from agentclaw.community.plugin_api.local_skill_security_provider import (
    LocalSkillSecurityProviderProtocol,
)


@dataclass(frozen=True)
class LocalSkillSecurityObservationConfig:
    enabled: bool = False


def require_observation_capabilities(
    provider: LocalSkillSecurityProviderProtocol,
) -> None:
    capabilities = provider.capabilities()
    if not capabilities.accepts_unchecked_asset_metadata:
        raise ValueError(
            "Local Skill security provider must accept unchecked asset metadata"
        )
    if not capabilities.ordered_latest_revision_sync:
        raise ValueError(
            "Local Skill security provider must enforce latest revision ordering"
        )


__all__ = [
    "LocalSkillSecurityObservationConfig",
    "require_observation_capabilities",
]
