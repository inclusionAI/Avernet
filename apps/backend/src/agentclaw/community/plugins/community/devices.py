"""Community device impls — the BaaS-only create-time rollout policy.

The corp ``ArcaBotCreateBaasRolloutPolicy`` is a DRM-driven ARCA→BaaS *migration*
gate: with no DRM center (community) it always decides ``arca``. Feeding that to
a router that registers only the ``baas`` provider would make every implicit-
provider create raise ``unknown create provider 'arca'``. The community build has
no ARCA at all, so creation is unconditionally BaaS.
"""
from __future__ import annotations

from agentclaw.community.core.devices.services.arca_bot_create_baas_rollout_policy import (
    ArcaBotCreateBaasRolloutDecision,
    ArcaBotCreateBaasRolloutPolicy,
)
from agentclaw.community.core.devices.services.device_service import BAAS_DEVICE_PROVIDER


class CommunityAllBaasRolloutPolicy(ArcaBotCreateBaasRolloutPolicy):
    """Every create-time provider decision is BaaS (no ARCA in community)."""

    def __init__(self) -> None:
        # No config provider / DRM needed — decide() is unconditional.
        pass

    def decide(
        self,
        *,
        user_id: str,
        bot_type: str,
        engine_type: str,
        template_type: str,
    ) -> ArcaBotCreateBaasRolloutDecision:
        return ArcaBotCreateBaasRolloutDecision(
            target_provider=BAAS_DEVICE_PROVIDER,
            reason="community_baas_only",
        )
