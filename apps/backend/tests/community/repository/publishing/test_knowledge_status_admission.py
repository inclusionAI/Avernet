"""Legacy handler compatibility over real service/repository persistence.

The permission wrapper is tested separately; these are not full HTTP/SSO tests.
"""

from types import SimpleNamespace

import pytest

from agentclaw.community.adapters.http.service_bot.router_publish import update_publish_status
from agentclaw.community.adapters.http.service_bot.schemas_publish import UpdatePublishStatusRequest
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.service_bot.services.bot_publish_service import BotPublishService
from agentclaw.community.kernel.knowledge import KnowledgeError, KnowledgePublicationPolicy
from tests.community.core.bot_knowledge.summary_fixtures import seed_online
from tests.community.repository.publishing.test_knowledge_selection_lifecycle import (
    Binding, _online, _start_online, publication as publication_fixture,
)

publication = publication_fixture


def service(repo):
    value = object.__new__(BotPublishService)
    value._repo = repo
    return value


@pytest.mark.asyncio
async def test_legacy_status_cannot_synthesize_online_success(publication):
    db, repo, record = publication
    seed_online(db, record, ['A'])
    with db.orm_session() as session:
        session.query(Binding).filter_by(kb_id='A').update({'online_enabled': True})
    online = _start_online(repo, record)
    result = await update_publish_status.__wrapped__(publish_id=record.id,
        request=UpdatePublishStatusRequest(source_status='online_pub', target_status='success', failed_status='online_pub'),
        user=SimpleNamespace(staffId='owner'), publish_service=service(repo))
    assert result.success is False
    assert result.error_code == 409
    assert 'KNOWLEDGE_OBSERVATION_REQUIRED' in result.message
    assert repo.get_by_id(record.id).status == PublishStatus.ONLINE_PUB
    assert repo.get_by_id(record.id).ext == online.ext
    assert _online(db) == {'A'}


@pytest.mark.asyncio
async def test_legacy_build_admission_freezes_and_observed_completion_applies(publication):
    db, repo, record = publication
    result = await update_publish_status.__wrapped__(publish_id=record.id,
        request=UpdatePublishStatusRequest(source_status='draft', target_status='building', failed_status='draft'),
        user=SimpleNamespace(staffId='owner'), publish_service=service(repo))
    assert result.success is True
    building = repo.get_by_id(record.id)
    assert building.ext['knowledge_selection_snapshot']['enabled_kb_ids'] == ['B']
    online = repo.update_status_with_ext(record.id, PublishStatus.ONLINE_PUB,
        {**building.ext, 'publish': {'online': 88}}, PublishStatus.BUILDING)
    done = service(repo).update_publish_status_with_ext(publish_id=record.id,
        source_status=PublishStatus.ONLINE_PUB, target_status=PublishStatus.SUCCESS,
        expected_ext=online.ext, ext=online.ext, observed_workflow_id=88)
    assert done.ext['knowledge_selection_result']['status'] == 'applied'
    assert _online(db) == {'B'}


@pytest.mark.parametrize('method', ['status', 'status_ext', 'cas', 'built'])
def test_shared_writers_refuse_unobserved_success_before_native_mutation(publication, method):
    _db, repo, record = publication
    online = _start_online(repo, record)
    with pytest.raises(KnowledgeError, match='KNOWLEDGE_OBSERVATION_REQUIRED'):
        if method == 'status':
            repo.update_status(record.id, PublishStatus.SUCCESS, PublishStatus.ONLINE_PUB)
        elif method == 'status_ext':
            repo.update_status_with_ext(record.id, PublishStatus.SUCCESS, online.ext,
                PublishStatus.ONLINE_PUB)
        else:
            args = dict(publish_id=record.id, source_status=PublishStatus.ONLINE_PUB,
                target_status=PublishStatus.SUCCESS, expected_ext=online.ext, ext=online.ext)
            if method == 'built':
                repo.compare_and_set_built_with_ext(**args, center_skill_uuids=(), env='dev')
            else:
                repo.compare_and_set_status_with_ext(**args)
    assert repo.get_by_id(record.id).status == PublishStatus.ONLINE_PUB
    assert repo.get_by_id(record.id).ext == online.ext


def test_disabled_integration_retains_native_status_writes(publication):
    _db, repo, record = publication
    repo._knowledge_policy = KnowledgePublicationPolicy(enabled=False)
    result = repo.update_status(record.id, PublishStatus.SUCCESS, PublishStatus.DRAFT)
    assert result.status == PublishStatus.SUCCESS
    assert 'knowledge_selection_result' not in result.ext


@pytest.mark.asyncio
async def test_status_handler_uses_normally_configured_service_and_repository(
    publication,
):
    from injector import Module, singleton
    from agentclaw.community.api.bot_publish_service import BotPublishServiceProtocol
    from agentclaw.community.core.repository.implementations.publishing.bot_publish import BotPublishRepository
    from agentclaw.community.core.repository.protocols.publishing import BotPublishRepositoryProtocol
    from agentclaw.community.di import DeployProfile, build_injector
    from agentclaw.community.plugin_api.database import DatabasePlugin

    db, _fixture_repo, record = publication

    class DatabaseBoundary(Module):
        def configure(self, binder):
            binder.bind(DatabasePlugin, to=db, scope=singleton)

    injector = build_injector(profile=DeployProfile.TEST, extra_modules=[DatabaseBoundary()])
    configured = injector.get(BotPublishServiceProtocol)
    repo = injector.get(BotPublishRepositoryProtocol)
    assert configured is injector.get(BotPublishService)
    assert configured._repo is repo
    assert isinstance(repo, BotPublishRepository)
    assert repo._knowledge_policy.enabled is True
    assert repo.get_by_id(record.id) is not None
    online = _start_online(repo, record)
    response = await update_publish_status.__wrapped__(publish_id=record.id,
        request=UpdatePublishStatusRequest(source_status='online_pub', target_status='success',
            failed_status='online_pub'),
        user=SimpleNamespace(staffId='owner'), publish_service=configured)
    assert response.success is False
    assert response.error_code == 409
    assert 'KNOWLEDGE_OBSERVATION_REQUIRED' in response.message
    assert repo.get_by_id(record.id).ext == online.ext
    assert online.ext['knowledge_selection_snapshot']['enabled_kb_ids'] == ['B']
    done = configured.update_publish_status_with_ext(
        publish_id=record.id, source_status=PublishStatus.ONLINE_PUB,
        target_status=PublishStatus.SUCCESS, expected_ext=online.ext,
        ext=online.ext, observed_workflow_id=77,
    )
    assert done.status == PublishStatus.SUCCESS
    assert done.ext['knowledge_selection_result']['status'] == 'applied'
    assert _online(db) == {'B'}
