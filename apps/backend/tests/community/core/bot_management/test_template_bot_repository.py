import json
from unittest.mock import Mock

import pytest
from sqlalchemy import event

from agentclaw.community.core.bot_management.managed_bots import ManagedBotService
from agentclaw.community.core.bot_management.repository.models import TemplateModel
from agentclaw.community.core.devices.repository.models import EntityDeviceBinding
from agentclaw.community.core.repository.implementations.bot.template_bots import (
    TemplateBotRepository,
)
from agentclaw.community.core.service_bot.repository.models import BotPublishModel
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from tests.community.core.bot_management.managed_bot_support import Database, seed


def test_query_is_scoped_filtered_and_display_safe():
    db = Database()
    seed(db)
    seed(db, 'pre-bot', env='pre')
    seed(db, 'deleted', is_delete=1)
    repository = TemplateBotRepository(db)
    result = repository.query(template_key='coding', offset=0, limit=20)
    assert result['total'] == 1
    item = result['items'][0]
    assert (item['bot_id'], item['template_version_id'], item['stage']) == ('bot', 7, 'draft')
    assert 'SECRET' not in json.dumps(result)
    assert repository.query(template_key='other', offset=0, limit=20)['total'] == 0
    assert repository.query(template_key='coding', offset=0, limit=20, outdated_before=7)['total'] == 0
    assert repository.query(template_key='coding', offset=0, limit=20, outdated_before=8)['total'] == 1


def test_service_publications_do_not_borrow_draft_snapshot_version():
    db = Database()
    bot_pk = seed(db, bot_type='service')
    with db.orm_session() as session:
        for status, binding in [('validating', {'verify': 3}), ('success', {'online': 4})]:
            session.add(BotPublishModel(source_bot_pk=bot_pk, source_bot_id='bot',
                publish_bot_id='bot', name='Example', owner_id='owner', status=status,
                env='dev', permission_owner='owner', ext=json.dumps({'binding': binding})))
    result = TemplateBotRepository(db).query(template_key='coding', offset=0, limit=20, bot_type='service')
    assert result['total'] == 3
    stages = {item['stage']: item for item in result['items']}
    assert stages['draft']['template_version_id'] == 7
    assert stages['prestable']['template_version_id'] is None
    assert stages['online']['template_version_id'] is None
    assert stages['online']['publication_id'] is not None


@pytest.mark.parametrize('bot_type,expected_ids', [
    ('service', ['service']),
    ('non_service', ['legacy', 'personal', 'unknown']),
    ('', ['legacy', 'personal', 'service', 'unknown']),
])
def test_bot_type_filters_before_count_and_pagination(bot_type, expected_ids):
    db = Database()
    seed(db, 'service', bot_type='service')
    seed(db, 'personal', bot_type='personal')
    seed(db, 'unknown', bot_type='')
    seed(db, 'legacy')
    seed(db, 'other-env', bot_type='service', env='pre')
    seed(db, 'deleted', bot_type='personal', is_delete=1)
    with db.orm_session() as session:
        session.query(BotModel).filter(BotModel.bot_id == 'legacy').update({'bot_type': None})
    service = ManagedBotService(TemplateBotRepository(db), Mock())
    result = service.query(template_key='coding', offset=0, limit=20, bot_type=bot_type)
    assert result['total'] == len(expected_ids)
    assert [item['bot_id'] for item in result['items']] == expected_ids
    page = service.query(template_key='coding', offset=1, limit=1, bot_type=bot_type)
    assert page['total'] == len(expected_ids)
    assert [item['bot_id'] for item in page['items']] == expected_ids[1:2]


def test_snapshot_requires_exact_owner_template_and_environment():
    db = Database()
    seed(db)
    repository = TemplateBotRepository(db)
    assert repository.snapshot(bot_id='bot', owner_id='owner', template_key='coding')['envs']['SECRET']
    assert repository.snapshot(bot_id='bot', owner_id='other', template_key='coding') is None
    assert repository.snapshot(bot_id='bot', owner_id='owner', template_key='wrong') is None


def test_malformed_json_is_not_a_scan_failure():
    db = Database()
    seed(db)
    with db.orm_session() as session:
        session.query(TemplateModel).update({'ext': '{broken'})
    assert TemplateBotRepository(db).query(template_key='coding', offset=0, limit=20)['total'] == 0


def test_status_batch_projects_readiness_and_public_restart_without_loading_snapshots():
    db = Database()
    for index in range(20):
        seed(db, f'bot-{index}', active_engine='aicoding', template_type='applicationCoding',
             binding_id=1, device_id='device', ext=json.dumps({
                 'start_status': 'SUCCEEDED',
                 'private': 'do-not-return',
                 'coding_restart': {'operation_id': f'op-{index}', 'phase': 'SUCCEEDED',
                                    'backup_warning': 'backup_failed_continue', 'private': 'do-not-return'},
             }))
    seed(db, 'deleted', is_delete=1)
    seed(db, 'other-env', env='pre')
    with avernet_tenant_scope('other-tenant'):
        seed(db, 'other-tenant')
    statements = []
    event.listen(db.engine, 'before_cursor_execute', lambda _conn, _cursor, statement, _params, _context, _many:
                 statements.append(statement))
    lifecycle = Mock()
    targets = [(f'bot-{index}', 'owner') for index in range(20)]
    items = ManagedBotService(TemplateBotRepository(db), lifecycle).statuses(
        template_key='coding', targets=[*targets, *targets, ('deleted', 'owner'),
                                        ('other-env', 'owner'), ('other-tenant', 'owner'), ('bot-0', 'wrong-owner')],
    )
    assert len(items) == 20
    assert all(item['active_engine'] == 'aicoding' for item in items)
    assert all(item['is_ready'] for item in items)
    assert items[0]['ext']['aicoding']['restart']['warning']['code'] == 'backup_failed_continue'
    assert 'do-not-return' not in json.dumps(items)
    lifecycle.get_bot.assert_not_called()
    assert len(statements) == 1
    projection = statements[0].split('FROM')[0]
    assert 'ac_templates.ext,' not in projection
    assert 'ac_bots.ext,' not in projection


@pytest.mark.parametrize('start_status,binding_status,expected_ready', [
    ('SUCCEEDED', 'ACTIVE', True),
    ('PENDING', 'ACTIVE', False),
    ('FAILED', 'ACTIVE', False),
    (None, 'ACTIVE', True),
    (None, 'PENDING', False),
])
def test_status_projection_keeps_existing_application_readiness_and_backup_failure_policy(
    start_status, binding_status, expected_ready,
):
    db = Database()
    seed(db, active_engine='aicoding', template_type='applicationCoding', binding_id=1, device_id='device',
         ext=json.dumps({
             'start_status': start_status,
             'coding_restart': {'operation_id': 'op', 'phase': 'FAILED', 'failure_phase': 'BACKING_UP',
                                'error_message': 'private provider diagnostic'},
         }))
    with db.orm_session() as session:
        session.add(EntityDeviceBinding(
            id=1, entity_id='owner', entity_type='user', device_id='device', device_provider='arca',
            env='dev', status=binding_status, applied_by='owner', device_props='{"private":"value"}',
        ))
    items = ManagedBotService(TemplateBotRepository(db), Mock()).statuses(
        template_key='coding', targets=[('bot', 'owner')],
    )
    assert len(items) == 1
    assert items[0]['is_ready'] is expected_ready
    restart = items[0]['ext']['aicoding']['restart']
    assert restart['status'] == 'FAILED'
    assert restart['phase'] == 'BACKING_UP'
    assert 'private' not in json.dumps(items)
