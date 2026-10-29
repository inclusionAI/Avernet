"""Supported SQLite paths stay bounded; these are not production latency claims."""

from agentclaw.community.core.service_bot.repository.models import (
    BotPublishModel, PublishOperationKind, PublishStatus,
)
from tests.community.repository.publishing.test_knowledge_selection_lifecycle import (
    Binding, BotModel, publication as publication_fixture,
)
from tests.community.repository.publishing.test_knowledge_selection_restore import (
    _complete, restore_case as restore_case_fixture,
)
from tests.community.repository.publishing.test_knowledge_short_transaction import finish
from tests.community.repository.publishing.test_knowledge_summary_cost import measure

publication = publication_fixture
restore_case = restore_case_fixture


def test_source_lock_work_does_not_grow_with_legacy_same_name_bots(publication):
    db, repo, record = publication
    with db.orm_session() as session:
        session.query(BotModel).filter_by(id=1).update({'bot_id': 'default'})
        session.query(BotPublishModel).filter_by(id=record.id).update(
            {'source_bot_id': 'default', 'publish_bot_id': 'default.pub'})
    costs = []
    for version, count in enumerate((1, 1000, 10000), 1):
        with db.orm_session() as session:
            session.execute(BotModel.__table__.insert(), [dict(
                bot_id='default', bot_name='Other', entity_id=f'other-{count}-{i}',
                entity_type='staff', creator_id=f'other-{count}-{i}',
                owner_id=f'other-{count}-{i}', bot_type='service', space_id=9,
                env='dev', is_delete=0,
            ) for i in range(count)])
        current = repo.get_by_id(record.id) if version == 1 else repo.insert(dict(
            source_bot_pk=1, source_bot_id='default', publish_bot_id='default.pub',
            name='Service', owner_id='owner', permission_owner='owner',
            status=PublishStatus.DRAFT, version=version, env='dev',
        ))
        building, freeze = measure(db.factory.kw['bind'], lambda: repo.update_status(
            current.id, PublishStatus.BUILDING, PublishStatus.DRAFT))
        online = repo.update_status_with_ext(current.id, PublishStatus.ONLINE_PUB,
            {**building.ext, 'publish': {'online': 77 + version}}, PublishStatus.BUILDING)
        completed, completion = measure(db.factory.kw['bind'], lambda: finish(repo, online))
        assert completed.ext['knowledge_selection_result']['status'] == 'applied'
        costs.append((freeze['vm_steps'], completion['vm_steps']))
    for column in zip(*costs):
        assert max(column) < 12000, costs
        assert max(column) - min(column) < 2000, costs
    print('SOURCE_LOCK_VM', costs)


def test_restore_work_does_not_grow_with_disabled_binding_history(restore_case):
    case = restore_case
    costs = []
    for attempt, count in enumerate((1, 1000, 10000), 1):
        if attempt > 1:
            case.operation = case.ledger.insert(dict(
                publish_id=case.draft.id, operation_kind=str(PublishOperationKind.DRAFT_RESTORE),
                stage='draft', attempt=attempt, request_id=f'cost-{attempt}',
                operator='owner', params=case.operation.params,
            ))
        with case.db.orm_session() as session:
            session.execute(Binding.__table__.insert(), [dict(
                bot_pk=1, space_id=9, env='dev', kb_id=f'disabled-{count}-{i}',
                enabled=False, online_enabled=False, revision=1, updated_by='owner',
            ) for i in range(count)])
        result, cost = measure(case.db.factory.kw['bind'], lambda: _complete(case))
        assert result is True
        costs.append(cost['vm_steps'])
    assert max(costs) < 12000, costs
    assert max(costs) - min(costs) < 2000, costs
    print('RESTORE_LOCK_VM', costs)
