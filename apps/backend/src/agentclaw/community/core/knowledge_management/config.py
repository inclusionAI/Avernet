"""Typed management surface configuration; provider connectivity stays external."""

from dataclasses import dataclass

from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError


@dataclass(frozen=True)
class KnowledgeManagementConfig:
    enabled: bool = False
    space_tenant: str = ""

    def __post_init__(self):
        if type(self.enabled) is not bool or not isinstance(self.space_tenant, str):
            raise ValueError("invalid knowledge management configuration")
        if self.enabled and not self.space_tenant:
            raise ValueError("enabled knowledge management requires a space tenant")

    def require_enabled(self) -> None:
        if not self.enabled:
            raise KnowledgeManagementError("KNOWLEDGE_INTEGRATION_UNAVAILABLE")
