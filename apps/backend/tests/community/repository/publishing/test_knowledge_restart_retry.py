"""Real restart failure observation and retry preserve production knowledge."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.repository.implementations.publishing.publish_operation import (
    OrmPublishOperationRepository,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishModel, PublishOperationModel, PublishStatus,
)
from agentclaw.community.core.service_bot.services.publish_flow.errors import PublishFlowServiceError
from agentclaw.community.kernel.knowledge import KnowledgeError, KnowledgePublicationPolicy
from agentclaw.community.plugin_api.models import BotCommonConfig
from tests.community.repository.publishing.test_knowledge_selection_lifecycle import (
    Binding, _real_flow, _start_online, publication as publication_fixture,
)
from tests.community.repository.publishing.test_knowledge_short_transaction import finish

publication = publication_fixture


def failed_restart(publication, *, configured=True, recreated=False):
    db, repo, draft = publication
    if not configured:
        with db.orm_session() as session:
            session.query(Binding).delete()
    live = finish(repo, _start_online(repo, draft))
    assert live.ext['knowledge_selection_result']['status'] == 'applied'
    ledger = OrmPublishOperationRepository(db)
    ledger.insert(dict(
        publish_id=live.id, stage='online', operation_kind='restart', attempt=1,
        state='abandoned' if recreated else 'completed', request_id='restart-300',
        bot_uuid='device', baas_publish_id=None if recreated else 300, env='dev',
    ))
    if recreated:
        ledger.insert(dict(
            publish_id=live.id, stage='online', operation_kind='first_release',
            attempt=1, state='completed', request_id='recreate-300',
            bot_uuid='new-device', baas_publish_id=300, env='dev',
        ))
    repo.compare_and_set_ext(
        publish_id=live.id, expected_ext=live.ext,
        ext={**live.ext, 'restart': {'online': 300, 'restarting': True}},
    )
    flow, queue = _real_flow(repo)
    flow._publish_operation_repo = ledger
    flow.get_baas_publish_progress = MagicMock(return_value={
        'status': 'FAILED', 'failed_devices': ['device'],
    })
    assert flow.sync_restart_progress(live.id).status == PublishStatus.FAILED
    failed = repo.get_by_id(live.id)
    assert failed.ext['source_status'] == 'success'
    # Only NEW remote restart issuance is doubled; native retry remains real.
    flow.execute_restart = AsyncMock(return_value={'success': True})
    return SimpleNamespace(db=db, repo=repo, record=failed, flow=flow, queue=queue)


def knowledge_state(db):
    with db.orm_session() as session:
        return (
            session.query(BotCommonConfig.config_value).all(),
            [(r.kb_id, r.enabled, r.online_enabled, r.revision, r.updated_by)
             for r in session.query(Binding).order_by(Binding.id).all()],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize('configured', [False, True])
@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('recreated', [False, True])
async def test_real_failed_online_restart_remains_retryable(
    publication, configured, enabled, recreated,
):
    case = failed_restart(publication, configured=configured, recreated=recreated)
    before = knowledge_state(case.db)
    case.repo._knowledge_policy = KnowledgePublicationPolicy(enabled=enabled)
    result = await case.flow.retry(case.record.id, operator='owner')
    assert result.action == 'restart'
    case.flow.execute_restart.assert_awaited_once_with(
        publish_id=case.record.id, stage='online', operator='owner',
    )
    restored = case.repo.get_by_id(case.record.id)
    assert restored.status == PublishStatus.SUCCESS
    assert restored.ext == {**case.record.ext, 'retry': True}
    assert case.queue.enqueue.call_count == 2
    assert knowledge_state(case.db) == before
    # A stale delivery cannot restore or issue a second restart.
    assert case.repo.restore_failed_online_restart(
        publish_id=case.record.id, expected_ext=case.record.ext,
    ) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', [
    'missing', 'state', 'stage', 'env', 'workflow', 'newer', 'source',
    'recreate_state', 'recreate_workflow',
])
async def test_invalid_restart_proof_never_restores_or_issues(publication, invalid):
    recreated = invalid.startswith('recreate')
    case = failed_restart(publication, recreated=recreated)
    with case.db.orm_session() as session:
        query = session.query(PublishOperationModel).filter_by(
            operation_kind='first_release' if recreated else 'restart',
        )
        if invalid == 'missing':
            query.delete()
        elif invalid == 'source':
            row = session.query(BotPublishModel).filter_by(id=case.record.id).one()
            import json
            ext = dict(case.record.ext, source_status='online_pub')
            row.ext = json.dumps(ext, ensure_ascii=False)
        elif invalid == 'newer':
            session.add(PublishOperationModel(
                publish_id=case.record.id, operation_kind='restart', stage='online',
                attempt=2, state='completed', request_id='newer',
                baas_publish_id=301, env='dev', operator='owner',
            ))
        else:
            changes = {
                'state': {'state': 'completed'}, 'stage': {'stage': 'verify'},
                'env': {'env': 'prod'}, 'workflow': {'baas_publish_id': None},
                'recreate_state': {'state': 'completed'},
                'recreate_workflow': {'baas_publish_id': 301},
            }
            query.update(changes[invalid])
    current = case.repo.get_by_id(case.record.id)
    before = knowledge_state(case.db)
    # Direct internal entry must reject source=online_pub, not merely rely on Flow.
    with pytest.raises(KnowledgeError, match='PUBLISH_RESTART_RETRY_INVALID'):
        case.repo.restore_failed_online_restart(
            publish_id=current.id, expected_ext=current.ext,
        )
    assert case.repo.get_by_id(current.id).ext == current.ext
    assert case.repo.get_by_id(current.id).status == PublishStatus.FAILED
    assert knowledge_state(case.db) == before
    if invalid != 'source':
        with pytest.raises(PublishFlowServiceError, match='PUBLISH_RESTART_RETRY_INVALID'):
            await case.flow.retry(current.id, operator='owner')
        case.flow.execute_restart.assert_not_awaited()
        case.queue.enqueue.assert_not_called()


def test_stale_ext_or_wrong_environment_cannot_restore(publication, monkeypatch):

    case = failed_restart(publication)
    changed = case.repo.compare_and_set_ext(
        publish_id=case.record.id, expected_ext=case.record.ext,
        ext={**case.record.ext, 'concurrent_metadata': 'keep'},
    )
    assert case.repo.restore_failed_online_restart(
        publish_id=case.record.id, expected_ext=case.record.ext,
    ) is None
    with monkeypatch.context() as patch:
        patch.setenv('SERVER_ENV', 'prod')
        assert case.repo.restore_failed_online_restart(
            publish_id=changed.id, expected_ext=changed.ext,
        ) is None
    assert case.repo.get_by_id(changed.id).ext == changed.ext
    assert case.repo.get_by_id(changed.id).status == PublishStatus.FAILED


@pytest.mark.asyncio
async def test_retry_commit_failure_rolls_back_and_does_not_issue(publication):
    case = failed_restart(publication)
    before = knowledge_state(case.db)
    original = case.db.transactional_orm_session

    @contextmanager
    def failing_commit():
        with original() as session:
            yield session
            raise RuntimeError('injected retry commit failure')

    case.db.transactional_orm_session = failing_commit
    with pytest.raises(PublishFlowServiceError, match='injected retry commit failure'):
        await case.flow.retry(case.record.id, operator='owner')
    assert case.repo.get_by_id(case.record.id).status == PublishStatus.FAILED
    assert case.repo.get_by_id(case.record.id).ext == case.record.ext
    assert knowledge_state(case.db) == before
    case.flow.execute_restart.assert_not_awaited()
    case.queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_failed_status_still_cannot_synthesize_success(publication):
    from agentclaw.community.adapters.http.service_bot.router_publish import update_publish_status
    from agentclaw.community.adapters.http.service_bot.schemas_publish import UpdatePublishStatusRequest

    case = failed_restart(publication)
    result = await update_publish_status.__wrapped__(
        publish_id=case.record.id,
        request=UpdatePublishStatusRequest(
            source_status='failed', target_status='success', failed_status='failed',
        ),
        user=SimpleNamespace(staffId='owner'), publish_service=case.flow._publish_service,
    )
    assert result.success is False and result.error_code == 409
    assert 'KNOWLEDGE_OBSERVATION_REQUIRED' in result.message
    assert case.repo.get_by_id(case.record.id).status == PublishStatus.FAILED


@pytest.mark.asyncio
async def test_native_retry_does_not_access_knowledge_tables(publication):
    from sqlalchemy import event

    case = failed_restart(publication, configured=False)
    engine = case.db.factory.kw['bind']
    Binding.__table__.drop(engine)
    BotCommonConfig.__table__.drop(engine)
    queries = []
    event.listen(engine, 'before_cursor_execute', lambda _c, _cur, sql, *_: queries.append(sql))
    result = await case.flow.retry(case.record.id, operator='owner')
    assert result.action == 'restart'
    assert not any('ac_bot_knowledge_binding' in sql or 'ac_bot_common_config' in sql
                   for sql in queries)
    case.flow.execute_restart.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('upload_fails', [False, True])
async def test_retry_artifact_budget_is_staged_outside_locks(publication, upload_fails):
    import json
    from agentclaw.community.core.service_bot.repository.config_artifact_offload import ConfigArtifactOffloader
    from tests.community.core.service_bot.repository.test_config_artifact_offload import _FakeOSS

    case = failed_restart(publication)
    storage = _FakeOSS()
    case.repo._offload = ConfigArtifactOffloader(storage)
    ext = {**case.record.ext, 'config_artifact': {'data': 'x' * 60000}, 'padding': ''}
    ext['padding'] = 'p' * (65530 - len(json.dumps(ext, ensure_ascii=False).encode()))
    case.record = case.repo.update_status_with_ext(
        case.record.id, PublishStatus.FAILED, ext, PublishStatus.FAILED,
    )
    assert storage.put_calls == 0
    active = [False]
    original = case.db.transactional_orm_session

    @contextmanager
    def tracked():
        with original() as session:
            active[0] = True
            try:
                yield session
            finally:
                active[0] = False

    case.db.transactional_orm_session = tracked
    put = storage.put_object

    def staged_put(key, body):
        assert not active[0], 'retry artifact upload under DB locks'
        if upload_fails:
            raise RuntimeError('retry artifact unavailable')
        return put(key, body)

    storage.put_object = staged_put
    before = knowledge_state(case.db)
    if upload_fails:
        with pytest.raises(PublishFlowServiceError, match='retry artifact unavailable'):
            await case.flow.retry(case.record.id, operator='owner')
        case.flow.execute_restart.assert_not_awaited()
        assert case.repo.get_by_id(case.record.id).status == PublishStatus.FAILED
        assert case.repo.get_by_id(case.record.id).ext == case.record.ext
    else:
        assert (await case.flow.retry(case.record.id, operator='owner')).action == 'restart'
        restored = case.repo.get_by_id(case.record.id)
        assert restored.status == PublishStatus.SUCCESS
        assert restored.ext == {**case.record.ext, 'retry': True}
        assert storage.put_calls == 1
    assert knowledge_state(case.db) == before


def test_normal_configured_service_exposes_restricted_recovery(publication):
    from injector import Module, singleton
    from agentclaw.community.api.bot_publish_service import BotPublishServiceProtocol
    from agentclaw.community.core.service_bot.services.bot_publish_service import BotPublishService
    from agentclaw.community.di import DeployProfile, build_injector
    from agentclaw.community.plugin_api.database import DatabasePlugin

    case = failed_restart(publication)

    class DatabaseBoundary(Module):
        def configure(self, binder):
            binder.bind(DatabasePlugin, to=case.db, scope=singleton)

    injector = build_injector(profile=DeployProfile.TEST, extra_modules=[DatabaseBoundary()])
    service = injector.get(BotPublishServiceProtocol)
    assert service is injector.get(BotPublishService)
    before = knowledge_state(case.db)
    restored = service.restore_failed_online_restart(
        publish_id=case.record.id, expected_ext=case.record.ext,
    )
    assert restored.status == PublishStatus.SUCCESS
    assert restored.ext == {**case.record.ext, 'retry': True}
    assert knowledge_state(case.db) == before
