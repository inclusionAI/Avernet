import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agentclaw.community.core.bot_management.repository.models import TemplateModel
from agentclaw.community.core.devices.repository.models import EntityDeviceBinding
from agentclaw.community.core.service_bot.repository.models import BotPublishModel
from agentclaw.community.plugin_api.models import BotModel


class Database:
    def __init__(self) -> None:
        self.engine = create_engine('sqlite://')
        for model in (BotModel, TemplateModel, BotPublishModel, EntityDeviceBinding):
            model.__table__.create(self.engine)

    @contextmanager
    def orm_session(self) -> Iterator[Session]:
        with Session(self.engine) as session, session.begin():
            yield session

    transactional_orm_session = orm_session


def seed(db: Database, bot_id: str = 'bot', **overrides: Any) -> int:
    values = dict(
        bot_id=bot_id, bot_name='Example', entity_id=bot_id, entity_type='user',
        creator_id='owner', owner_id='owner', env='dev', status='ACTIVE',
    )
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
