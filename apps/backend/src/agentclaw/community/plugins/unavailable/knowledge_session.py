"""Unconfigured session authentication fails closed."""

from agentclaw.community.kernel.knowledge import KnowledgeError
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugin_api.knowledge_session import KnowledgeSessionPlugin


@plugin_impl(
    mode=Mode.LOCAL, flavor=Flavor.STUB, rationale="No deployment session interpreter"
)
class UnavailableKnowledgeSession(KnowledgeSessionPlugin):
    def headers(self, *, actor_id, session):
        raise KnowledgeError("KNOWLEDGE_CONTENT_UNAVAILABLE", 503)


@plugin_impl(
    mode=Mode.PROD, rationale="No credentials until deployment supplies authentication"
)
class DisabledKnowledgeSession(UnavailableKnowledgeSession):
    pass
