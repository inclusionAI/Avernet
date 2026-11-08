"""The production build provider consumes the same scoped identity repository."""
from agentclaw.community.api.bot_build_service import BotBuildServiceProtocol
from agentclaw.community.core.repository.protocols.identity import UserListRepositoryProtocol
from agentclaw.community.core.user_list.models import EntityUserListModel
from agentclaw.community.core.service_bot.services.runtime_artifact_policy import resolve_runtime_artifacts
from agentclaw.community.plugin_api.database import DatabasePlugin


def test_build_provider_wires_repository_and_database_scope(test_injector):
    service = test_injector.get(BotBuildServiceProtocol)
    repository = test_injector.get(UserListRepositoryProtocol)
    assert service._user_list_repository is repository
    with test_injector.get(DatabasePlugin).orm_session() as session:
        EntityUserListModel.__table__.create(session.get_bind(), checkfirst=True)
    assert resolve_runtime_artifacts(repository, bot_id="b", entity_id="owner", env="prod") is False
    with test_injector.get(DatabasePlugin).orm_session() as session:
        session.add(EntityUserListModel(entity_id="*", user_list_type="service_bot_runtime_artifact", env="pre"))
    assert resolve_runtime_artifacts(repository, bot_id="b", entity_id="owner", env="prod") is False
    assert resolve_runtime_artifacts(repository, bot_id="b", entity_id="owner", env="pre") is True
