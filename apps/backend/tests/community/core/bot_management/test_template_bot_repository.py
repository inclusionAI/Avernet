import json
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agentclaw.community.core.bot_management.repository.models import TemplateModel
from agentclaw.community.core.devices.repository.models import EntityDeviceBinding
from agentclaw.community.core.repository.implementations.bot.template_bots import TemplateBotRepository
from agentclaw.community.core.service_bot.repository.models import BotPublishModel
from agentclaw.community.plugin_api.models import BotModel


class Database:
    def __init__(self):
        self.engine = create_engine('sqlite://')
        for model in (BotModel, TemplateModel, BotPublishModel, EntityDeviceBinding):
            model.__table__.create(self.engine)

    @contextmanager
    def orm_session(self):
        with Session(self.engine) as session, session.begin():
            yield session

    transactional_orm_session = orm_session


def seed(db, bot_id='bot', **overrides):
    values = dict(bot_id=bot_id, bot_name='Example', entity_id=bot_id, entity_type='user',
                  creator_id='owner', owner_id='owner', env='dev', status='ACTIVE')
    values.update(overrides)
    with db.orm_session() as session:
        bot = BotModel(**values)
        session.add(bot)
        session.flush()
        session.add(TemplateModel(bot_id=bot_id, ext=json.dumps({
            'template_key': 'coding', 'template_version_id': 7, 'template_version': '1.2',
            'envs': {'SECRET': 'do-not-return'},
        })))
        return bot.id


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
    result = TemplateBotRepository(db).query(template_key='coding', offset=0, limit=20)
    assert result['total'] == 3
    stages = {item['stage']: item for item in result['items']}
    assert stages['draft']['template_version_id'] == 7
    assert stages['prestable']['template_version_id'] is None
    assert stages['online']['template_version_id'] is None
    assert stages['online']['publication_id'] is not None


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
