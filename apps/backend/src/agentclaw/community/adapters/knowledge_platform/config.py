"""Knowledge catalog settings; no service credential configuration."""

from dataclasses import dataclass

from agentclaw.community.kernel.knowledge import KnowledgeError


@dataclass(frozen=True)
class KnowledgeIntegrationConfig:
    enabled: bool = False
    catalog_origin: str = ""

    def require_enabled(self) -> None:
        if not self.enabled:
            raise KnowledgeError("KNOWLEDGE_INTEGRATION_UNAVAILABLE", 503)
