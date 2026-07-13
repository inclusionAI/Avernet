from __future__ import annotations

from injector import Module, provider, singleton

from agentclaw.community.api.policy_service import PolicyServiceProtocol
from agentclaw.community.log import get_logger
from agentclaw.community.plugins.local.policy_service import LocalPolicyService

logger = get_logger()


class SingleboxAccessModule(Module):
    """Singlebox-only all-open PolicyService binding."""

    @singleton
    @provider
    def _policy_service_protocol(self) -> PolicyServiceProtocol:
        logger.info(
            "[NEW-ARCH] PolicyServiceProtocol: LocalPolicyService (singlebox)"
        )
        return LocalPolicyService()
