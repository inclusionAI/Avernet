"""Explicit publication policy and missing-config fallback stay independent of APIs."""

from unittest.mock import MagicMock

import pytest
from agentclaw.community.core.bot_knowledge.repository.models import (
    BotKnowledgeBindingModel as Binding,
)
from agentclaw.community.core.service_bot.repository.config_artifact_offload import (
    ConfigArtifactOffloader,
)
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.di.modules.service_bot_module import ServiceBotModule
from agentclaw.community.kernel.knowledge import KnowledgePublicationPolicy
from injector import Injector
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from tests.community.repository.publishing.test_knowledge_selection_lifecycle import (
    publication as publication_fixture,
)
from tests.community.repository.publishing.test_knowledge_selection_restore import (
    restore_case as restore_fixture,
)

publication = publication_fixture
restore_case = restore_fixture


@pytest.mark.parametrize(
    "config,enabled",
    [({}, False), ({"enabled": False}, False), ({"enabled": True}, True)],
)
def test_composition_selects_explicit_policy(monkeypatch, config, enabled):
    monkeypatch.setattr(
        "agentclaw.community.di.modules.service_bot_module.read_user_config",
        lambda: {"bot_knowledge_publication": config},
    )
    injector = Injector([ServiceBotModule()])
    assert injector.get(KnowledgePublicationPolicy).enabled is enabled


@pytest.mark.parametrize(
    "config", [None, [], "true", {"enabled": "false"}, {"enabled": 1}]
)
def test_invalid_policy_never_silently_opts_in_or_out(monkeypatch, config):
    monkeypatch.setattr(
        "agentclaw.community.di.modules.service_bot_module.read_user_config",
        lambda: {"bot_knowledge_publication": config},
    )
    with pytest.raises(ValueError):
        Injector([ServiceBotModule()]).get(KnowledgePublicationPolicy)


def test_missing_configuration_policy_fallback_is_disabled():
    assert KnowledgePublicationPolicy().enabled is False


@pytest.mark.parametrize("enabled", [False, True])
def test_missing_knowledge_schema_only_blocks_opted_in_publication(
    publication, enabled, monkeypatch
):
    db, _repo, record = publication
    engine = db.factory.kw["bind"]
    Binding.__table__.drop(engine)
    from agentclaw.community.core.repository.protocols.publishing import (
        BotPublishRepositoryProtocol,
    )
    from agentclaw.community.plugin_api.database import DatabasePlugin

    monkeypatch.setattr(
        "agentclaw.community.di.modules.service_bot_module.read_user_config",
        lambda: {"bot_knowledge_publication": {"enabled": enabled}},
    )
    injector = Injector([ServiceBotModule()])
    injector.binder.bind(DatabasePlugin, to=db)
    injector.binder.bind(
        ConfigArtifactOffloader, to=ConfigArtifactOffloader(MagicMock())
    )
    repo = injector.get(BotPublishRepositoryProtocol)
    queries = []
    event.listen(
        engine, "before_cursor_execute", lambda _c, _cur, sql, *_: queries.append(sql)
    )
    if enabled:
        with pytest.raises(OperationalError):
            repo.update_status(record.id, PublishStatus.BUILDING, PublishStatus.DRAFT)
        assert repo.get_by_id(record.id).status == PublishStatus.DRAFT
    else:
        built = repo.update_status(
            record.id, PublishStatus.BUILDING, PublishStatus.DRAFT
        )
        assert built.status == PublishStatus.BUILDING
        assert "knowledge_selection_snapshot" not in built.ext
        assert not any("ac_bot_knowledge_binding" in sql for sql in queries)


@pytest.mark.asyncio
async def test_disabled_restore_uses_native_completion_without_knowledge_schema(
    restore_case,
):
    case = restore_case
    case.repo._knowledge_policy = KnowledgePublicationPolicy()
    engine = case.db.factory.kw["bind"]
    Binding.__table__.drop(engine)
    queries = []
    event.listen(
        engine, "before_cursor_execute", lambda _c, _cur, sql, *_: queries.append(sql)
    )

    async def restored(**_):
        return {"status": "success", "restore_type": "local"}

    case.behavior.restore_draft = restored
    result = await case.flow.execute_restore_draft(
        draft_publish_id=case.draft.id, operation_id=case.operation.id, operator="owner"
    )
    assert result["status"] == "success"
    assert case.ledger.get_by_id(case.operation.id).state == "completed"
    assert not any("ac_bot_knowledge_binding" in sql for sql in queries)
