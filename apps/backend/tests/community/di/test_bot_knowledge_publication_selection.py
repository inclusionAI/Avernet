"""The normal DI chain resolves optional observations without startup I/O."""

from unittest.mock import Mock

from agentclaw.community.api.bot_knowledge_service import BotKnowledgePublicationSelectionProtocol
from agentclaw.community.core.bot_knowledge.publication_selection import BotKnowledgePublicationSelection
from agentclaw.community.core.repository.protocols.config import BotCommonConfigRepositoryProtocol
from agentclaw.community.core.service_bot.services.publish_flow.tasks import PublishTaskLifecycle
from agentclaw.community.plugin_api.database import DatabasePlugin


def test_real_binding_resolves_without_optional_remote_credentials(test_injector, monkeypatch):
    db = test_injector.get(DatabasePlugin)
    session = Mock(side_effect=AssertionError("no startup I/O"))
    monkeypatch.setattr(db, "orm_session", session)
    service = test_injector.get(BotKnowledgePublicationSelectionProtocol)
    assert isinstance(service, BotKnowledgePublicationSelection)
    assert service.db is db
    assert service.configs is test_injector.get(BotCommonConfigRepositoryProtocol)
    session.assert_not_called()
    # The actual implementation, not an endpoint seed replacement, is resolved.
    assert not service.record_build_selection(publication_id=101)
    session.assert_called_once()


def test_lifecycle_uses_the_configured_lazy_service_provider(test_injector):
    lifecycle = test_injector.get(PublishTaskLifecycle)
    assert lifecycle._selection_provider() is test_injector.get(BotKnowledgePublicationSelectionProtocol)
