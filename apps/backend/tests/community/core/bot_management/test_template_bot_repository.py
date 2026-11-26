import json
import re
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
    assert item['template_status'] is None
    assert 'SECRET' not in json.dumps(result)
    assert repository.query(template_key='other', offset=0, limit=20)['total'] == 0
    assert repository.query(template_key='coding', offset=0, limit=20, outdated_before=7)['total'] == 0
    assert repository.query(template_key='coding', offset=0, limit=20, outdated_before=8)['total'] == 1


def test_service_publications_display_the_source_bots_saved_version():
    db = Database()
    bot_pk = seed(db, bot_type='service')
    with db.orm_session() as session:
        for status, binding in [('validating', {'verify': 3}), ('success', {'online': 4})]:
            session.add(BotPublishModel(source_bot_pk=bot_pk, source_bot_id='bot',
                publish_bot_id='bot', name='Example', owner_id='owner', status=status,
                env='dev', permission_owner='owner', ext=json.dumps({'binding': binding})))
    result = TemplateBotRepository(db).query(template_key='coding', offset=0, limit=20)
    assert result['total'] == 3
    stages = {item['stage']: item for item in result['items']}
    assert stages['draft']['template_version_id'] == 7
    assert stages['prestable']['template_version_id'] == 7
    assert stages['online']['template_version_id'] == 7
    assert stages['online']['publication_id'] is not None
    assert all(item['template_status'] is None for item in result['items'])


@pytest.mark.parametrize('metadata,expected_status', [
    ({}, None),
    ({'template_version_id': None, 'bot_template_config': None}, None),
    ({'bot_template_config': {'status': 'pre_published'}}, 'pre_published'),
    ({'template_version_id': 7, 'bot_template_config': {'status': 'prod_released'}}, 'prod_released'),
    ({'bot_template_config': {'status': 1}}, None),
    ({'bot_template_config': {'status': {'private': 'hidden'}}}, None),
])
def test_discovery_reads_optional_saved_release_status_without_requiring_a_version(metadata, expected_status):
    db = Database()
    seed(db)
    with db.orm_session() as session:
        session.query(TemplateModel).update({'ext': json.dumps({'template_key': 'coding', **metadata})})
    result = TemplateBotRepository(db).query(template_key='coding', offset=0, limit=20)
    assert result['total'] == 1
    assert result['items'][0]['template_status'] == expected_status
    assert result['items'][0]['template_version_id'] == metadata.get('template_version_id')
    assert 'template_config' not in result['items'][0]
    assert 'private' not in json.dumps(result)


def test_all_stages_display_the_saved_template_status_even_if_publication_metadata_differs():
    db = Database()
    bot_pk = seed(db, bot_type='service')
    with db.orm_session() as session:
        session.query(TemplateModel).update({'ext': json.dumps({
            'template_key': 'coding', 'template_version_id': 7,
            'bot_template_config': {'status': 'pre_published'},
        })})
        for lifecycle_status, binding, template_status in [
            ('validating', {'verify': 3}, 'prod_released'),
            ('success', {'online': 4}, 'pre_published'),
        ]:
            session.add(BotPublishModel(source_bot_pk=bot_pk, source_bot_id='bot',
                publish_bot_id='bot', name='Example', owner_id='owner', status=lifecycle_status,
                env='dev', permission_owner='owner', ext=json.dumps({
                    'binding': binding, 'template_config': {'template_version_id': 6,
                        'bot_template_config': {'status': template_status}},
                })))
    result = TemplateBotRepository(db).query(template_key='coding', offset=0, limit=20)
    stages = {item['stage']: item['template_status'] for item in result['items']}
    assert stages == dict.fromkeys(('draft', 'prestable', 'online'), 'pre_published')
    assert TemplateBotRepository(db).query(template_key='coding', offset=0, limit=1, outdated_before=7)['total'] == 0


def test_retained_verify_stage_uses_the_online_publication_until_a_new_validation_exists():
    db = Database()
    source = seed(db, bot_type='service')
    with db.orm_session() as session:
        session.add(EntityDeviceBinding(id=3, entity_id='owner', entity_type='user', device_id='verify',
            device_provider='arca', env='dev', status='ACTIVE', applied_by='owner'))
        session.add(BotPublishModel(source_bot_pk=source, source_bot_id='bot', publish_bot_id='bot',
            name='Example', owner_id='owner', status='success', env='dev', permission_owner='owner',
            ext=json.dumps({'binding': {'verify': 3, 'online': 4}})))
    repository = TemplateBotRepository(db)
    for offset, stage in enumerate(('draft', 'online', 'prestable')):
        page = repository.query(template_key='coding', search='bot', outdated_before=8, offset=offset, limit=1)
        assert page['total'] == 3
        assert [row['stage'] for row in page['items']] == [stage]
    with db.orm_session() as session:
        session.add(BotPublishModel(source_bot_pk=source, source_bot_id='bot', publish_bot_id='bot',
            name='Example', owner_id='owner', status='validating', env='dev', permission_owner='owner', ext='{}'))
    assert repository.query(template_key='coding', stage='prestable', offset=0, limit=1) == {'total': 0, 'items': []}


def test_discovery_projects_release_status_without_per_bot_queries_or_full_snapshots():
    db = Database()
    for index in range(20):
        seed(db, f'bot-{index}')
    with db.orm_session() as session:
        session.query(TemplateModel).update({'ext': json.dumps({
            'template_key': 'coding', 'template_version_id': 7,
            'bot_template_config': {'status': 'prod_released', 'private': 'hidden'},
        })})
    statements = []
    event.listen(db.engine, 'before_cursor_execute', lambda _conn, _cursor, statement, _params, _context, _many:
                 statements.append(statement))
    result = TemplateBotRepository(db).query(template_key='coding', offset=0, limit=20)
    assert result['total'] == 20
    assert len(result['items']) == 20
    assert all(item['template_status'] == 'prod_released' for item in result['items'])
    assert len(statements) == 1
    assert len(re.findall(r'\b(?:FROM|JOIN) ac_templates\b', statements[0], re.IGNORECASE)) == 1
    assert 'template_config' not in result['items'][0]
    assert 'hidden' not in json.dumps(result)


def test_only_visible_source_bots_count_towards_pages_without_hiding_restart_observations():
    db = Database()
    for status in ('ACTIVE', 'FAILED', 'PENDING', 'RECYCLED', 'REACTIVATING', 'RELEASED'):
        seed(db, status, status=status)
    seed(db, 'deleted', is_delete=1)
    repository = TemplateBotRepository(db)
    for offset, expected in [(0, ['ACTIVE']), (1, ['FAILED']), (2, []), (20, [])]:
        result = repository.query(template_key='coding', offset=offset, limit=1)
        assert result['total'] == 2
        assert [row['bot_id'] for row in result['items']] == expected
    assert repository.statuses(template_key='coding', targets=[('PENDING', 'owner')])[0]['status'] == 'PENDING'


def test_legacy_bot_without_a_saved_template_key_is_not_attached_to_a_template():
    db = Database()
    seed(db)
    with db.orm_session() as session:
        session.query(TemplateModel).update({'ext': '{"custom":"kept"}'})
    result = TemplateBotRepository(db).query(template_key='coding', offset=0, limit=20)
    assert result == {'total': 0, 'items': []}


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
